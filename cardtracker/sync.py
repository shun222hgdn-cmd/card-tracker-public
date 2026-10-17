"""Gmail同期と再判定(要件 F-02, F-03, 4-4)。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from .classifier import apply_classification, match_key, rules_from_db
from .gmail_client import GmailAuthError
from .models import CardSetting, ExclusionRule, GmailConnection, Transaction, User, utcnow
from .parser import ParsedEntry, parse_message


@dataclass
class SyncOutcome:
    ran: bool
    added: int = 0
    failed_parse: int = 0
    message: str = ""
    error: str | None = None


def load_rules(session):
    return rules_from_db(session.scalars(select(ExclusionRule)).all())


def needs_auto_sync(conn: GmailConnection | None, interval_minutes: int, now: datetime | None = None) -> bool:
    if not conn or conn.status not in ("connected", "error"):
        return False
    now = now or utcnow()
    return conn.last_attempt_at is None or now - conn.last_attempt_at >= timedelta(minutes=interval_minutes)


def _entry_to_tx(user: User, message_id: str, card_label: str | None, entry: ParsedEntry) -> Transaction:
    return Transaction(
        user_id=user.id,
        source="gmail",
        gmail_message_id=message_id,
        seq=entry.seq,
        used_at=entry.used_at,
        merchant=entry.merchant,
        usage_type=entry.usage_type,
        card_label=card_label,
        original_amount=entry.amount,
        currency=entry.currency,
        is_cancel=entry.is_cancel,
        parse_failed=not entry.ok,
        note=entry.error,
    )


def _is_duplicate(session, tx: Transaction) -> bool:
    """別のメールIDで、日時・利用先・金額がまったく同じ取引がすでにあるか。"""
    if tx.parse_failed or tx.is_cancel:
        return False
    return (
        session.scalar(
            select(Transaction.id).where(
                Transaction.user_id == tx.user_id,
                Transaction.gmail_message_id != tx.gmail_message_id,
                Transaction.used_at == tx.used_at,
                Transaction.merchant == tx.merchant,
                Transaction.original_amount == tx.original_amount,
                Transaction.currency == tx.currency,
                Transaction.is_cancel.is_(False),
            ).limit(1)
        )
        is not None
    )


def _link_cancellation(session, cancel_tx: Transaction) -> Transaction | None:
    """取消通知に対応する元の取引を探して「取消済み」にする。同じ利用先・金額で、取消より前の最新のもの。"""
    candidates = session.scalars(
        select(Transaction)
        .where(
            Transaction.user_id == cancel_tx.user_id,
            Transaction.is_cancel.is_(False),
            Transaction.cancelled.is_(False),
            Transaction.parse_failed.is_(False),
            Transaction.original_amount == cancel_tx.original_amount,
            Transaction.currency == cancel_tx.currency,
            Transaction.used_at <= cancel_tx.used_at,
        )
        .order_by(Transaction.used_at.desc())
    ).all()
    for original in candidates:
        if match_key(original.merchant) == match_key(cancel_tx.merchant):
            original.cancelled = True
            return original
    return None


def sync_user(session, user: User, client, max_messages: int = 50) -> SyncOutcome:
    conn: GmailConnection = user.gmail_connection
    setting: CardSetting = user.card_setting
    conn.last_attempt_at = utcnow()
    session.commit()

    rules = load_rules(session)
    outcome = SyncOutcome(ran=True)
    try:
        ids = client.list_message_ids(setting.period_start, setting.period_end)
        known = set(
            session.scalars(
                select(Transaction.gmail_message_id).where(
                    Transaction.user_id == user.id, Transaction.gmail_message_id.is_not(None)
                )
            ).all()
        )
        new_ids = [i for i in ids if i not in known]
        # Gmailは新しい順に返すので、古い順に取り込む(取消より先に元取引を登録するため)
        batch = list(reversed(new_ids))[:max_messages]

        for message_id in batch:
            msg = client.get_message(message_id)
            result = parse_message(msg.subject, msg.bodies)
            for entry in result.entries:
                tx = _entry_to_tx(user, message_id, result.card_label, entry)
                tx.duplicate_suspect = _is_duplicate(session, tx)
                session.add(tx)
                session.flush()
                if tx.is_cancel:
                    original = _link_cancellation(session, tx)
                    if original:
                        apply_classification(original, rules, setting.card_label)
                apply_classification(tx, rules, setting.card_label)
                outcome.added += 1
                outcome.failed_parse += int(tx.parse_failed)
            # 1通ずつ確定させ、途中で失敗・タイムアウトしても取り込み済みの分は残す
            session.commit()

        conn.status = "connected"
        conn.last_sync_at = utcnow()
        conn.last_error = None
        conn.pending_count = len(new_ids) - len(batch)
        session.commit()
        outcome.message = f"{outcome.added}件を取り込みました" if outcome.added else "新しい利用通知はありません"
        if conn.pending_count:
            outcome.message += f"(残り{conn.pending_count}件は次回の同期で取り込みます)"
    except GmailAuthError as e:
        session.rollback()
        conn.status = "expired"
        conn.last_error = str(e)
        session.commit()
        outcome.error = str(e)
    except Exception as e:  # noqa: BLE001  同期失敗時も保存済みデータは表示し続ける(要件 F-02)
        session.rollback()
        conn.status = "error"
        conn.last_error = f"同期に失敗しました: {e}"
        session.commit()
        outcome.error = conn.last_error
    return outcome


def reclassify_user(session, user: User, rules=None) -> None:
    rules = rules if rules is not None else load_rules(session)
    for tx in session.scalars(select(Transaction).where(Transaction.user_id == user.id)):
        apply_classification(tx, rules, user.card_setting.card_label)


def reclassify_all(session) -> None:
    rules = load_rules(session)
    for user in session.scalars(select(User)):
        reclassify_user(session, user, rules)
