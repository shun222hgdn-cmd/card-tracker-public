"""画面(要件 5. 画面要件)。

すべての取引の取得・変更は「user_id == ログイン中の本人」で絞り込む。
他人の取引IDを直接URLに入れても 404 になる(要件 2-2: サーバー側での権限制御)。
"""
from __future__ import annotations

import unicodedata
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .auth import admin_required, external_url, login_required, oauth
from .classifier import apply_classification
from .db import db_session
from .csv_import import CsvFormatError, import_csv
from .gmail_client import GmailClient, revoke_token
from .mock_gmail import MockGmailClient
from .models import (
    STATUS_LABELS,
    STATUSES,
    TARGET,
    ExclusionRule,
    Transaction,
    User,
)
from .security import decrypt, encrypt
from .summary import compute_summary, in_period
from .sync import load_rules, needs_auto_sync, reclassify_all, reclassify_user, sync_user

bp = Blueprint("main", __name__)

MAX_AMOUNT = 10_000_000


# ---------- 共通 ----------

def own_transaction(tx_id: int) -> Transaction:
    tx = db_session.scalar(select(Transaction).where(Transaction.id == tx_id, Transaction.user_id == g.user.id))
    if tx is None:
        abort(404)
    return tx


def gmail_client_for(user: User):
    conn = user.gmail_connection
    cfg = current_app.config["APP"]
    if conn.is_mock:
        return MockGmailClient(user.id)
    token = decrypt(conn.refresh_token_enc) if conn.refresh_token_enc else None
    if not token:
        conn.status = "expired"
        conn.last_error = "保存された認可情報を読み取れません。Gmailを再連携してください。"
        db_session.commit()
        return None
    return GmailClient(cfg.google_client_id, cfg.google_client_secret, token)


def run_sync(user: User):
    client = gmail_client_for(user)
    if client is None:
        return None
    return sync_user(db_session, user, client, current_app.config["APP"].max_messages_per_sync)


def safe_next(default: str) -> str:
    """フォームの戻り先は自サイト内のパスだけ許可する(オープンリダイレクト対策)。"""
    nxt = request.form.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else default


def parse_yen(raw: str | None) -> int | None:
    """「12,800」「１２８００」などを円の整数にする。空なら None、不正なら ValueError。"""
    raw = unicodedata.normalize("NFKC", raw or "").replace(",", "").replace("円", "").strip()
    if raw == "":
        return None
    value = int(raw)
    if not 0 <= value <= MAX_AMOUNT:
        raise ValueError
    return value


# ---------- 個人ホーム (F-05) ----------

@bp.get("/")
@login_required
def home():
    user = g.user
    sync_result = None
    if needs_auto_sync(user.gmail_connection, current_app.config["APP"].sync_interval_minutes):
        sync_result = run_sync(user)
    return render_template(
        "home.html",
        summary=compute_summary(db_session, user),
        conn=user.gmail_connection,
        setting=user.card_setting,
        sync_result=sync_result,
    )


@bp.post("/sync")
@login_required
def sync_now():
    conn = g.user.gmail_connection
    if conn.status not in ("connected", "error"):
        flash("先に設定画面でGmailを連携してください。", "error")
        return redirect(url_for("main.settings"))
    result = run_sync(g.user)
    if result and result.error:
        flash(result.error, "error")
    elif result:
        flash(result.message, "info")
    return redirect(safe_next(url_for("main.home")))


# ---------- 利用履歴 (F-06) ----------

@bp.get("/history")
@login_required
def history():
    status = request.args.get("status", "all")
    s = g.user.card_setting
    query = select(Transaction).where(Transaction.user_id == g.user.id)
    if status in STATUSES:
        query = query.where(Transaction.final_status == status)
    query = query.order_by(Transaction.used_at.desc().nulls_first(), Transaction.id.desc()).limit(500)
    rows = db_session.scalars(query).all()
    return render_template(
        "history.html",
        rows=[(tx, tx.used_at is None or in_period(tx, s.period_start, s.period_end)) for tx in rows],
        status=status,
        labels=STATUS_LABELS,
    )


# ---------- 手動修正 (F-04) ----------

@bp.route("/transactions/<int:tx_id>", methods=["GET", "POST"])
@login_required
def edit_transaction(tx_id):
    tx = own_transaction(tx_id)
    if request.method == "POST":
        status = request.form.get("final_status")
        try:
            corrected = parse_yen(request.form.get("corrected_amount"))
        except ValueError:
            flash("集計金額は0〜1,000万円の整数で入力してください。", "error")
            return redirect(url_for("main.edit_transaction", tx_id=tx.id))
        if status not in STATUSES:
            abort(400)
        if status == TARGET and corrected is None and tx.effective_amount is None:
            flash("「対象」にするには、日本円の集計金額を入力してください。", "error")
            return redirect(url_for("main.edit_transaction", tx_id=tx.id))

        tx.final_status = status
        tx.corrected_amount = corrected
        tx.manual_override = True
        memo = (request.form.get("note") or "").strip()[:500]
        tx.note = memo or None
        db_session.commit()
        flash("保存しました。次回の同期でも上書きされません。", "info")
        return redirect(url_for("main.history", status=request.args.get("back", "all")))
    return render_template("edit.html", tx=tx, labels=STATUS_LABELS)


@bp.post("/transactions/<int:tx_id>/reset")
@login_required
def reset_transaction(tx_id):
    tx = own_transaction(tx_id)
    tx.manual_override = False
    tx.corrected_amount = None
    apply_classification(tx, load_rules(db_session), g.user.card_setting.card_label)
    db_session.commit()
    flash("自動判定に戻しました。", "info")
    return redirect(url_for("main.edit_transaction", tx_id=tx.id))


@bp.post("/transactions/<int:tx_id>/delete")
@login_required
def delete_transaction(tx_id):
    tx = own_transaction(tx_id)
    if tx.source != "manual":
        abort(400, "メールから取り込んだ取引は削除できません。「対象外」に変更してください。")
    db_session.delete(tx)
    db_session.commit()
    flash("手動登録の取引を削除しました。", "info")
    return redirect(url_for("main.history"))


# ---------- 手動登録 (F-09) ----------

@bp.route("/transactions/new", methods=["GET", "POST"])
@login_required
def new_transaction():
    if request.method == "POST":
        try:
            used_at = datetime.strptime(
                f"{request.form.get('date')} {request.form.get('time') or '00:00'}", "%Y-%m-%d %H:%M"
            )
            amount = parse_yen(request.form.get("amount"))
        except ValueError:
            amount, used_at = None, None
        merchant = (request.form.get("merchant") or "").strip()[:200]
        if used_at is None or amount is None or not merchant:
            flash("日付・利用先・金額(円)を正しく入力してください。", "error")
            return render_template("new.html", form=request.form)
        tx = Transaction(
            user_id=g.user.id,
            source="manual",
            used_at=used_at,
            merchant=merchant,
            original_amount=Decimal(amount),
            currency="JPY",
        )
        db_session.add(tx)
        apply_classification(tx, load_rules(db_session), g.user.card_setting.card_label)
        db_session.commit()
        flash(f"登録しました(判定: {tx.status_label})。", "info")
        return redirect(url_for("main.history"))
    return render_template("new.html", form={})


# ---------- 明細CSVの取り込み (Gmail連携をしない家族用) ----------

@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_statement():
    if request.method == "POST":
        file = request.files.get("csv_file")
        if file is None or not file.filename:
            flash("CSVファイルを選んでください。", "error")
            return redirect(url_for("main.import_statement"))
        try:
            outcome = import_csv(db_session, g.user, file.read())
        except CsvFormatError as e:
            flash(str(e), "error")
            return redirect(url_for("main.import_statement"))
        msg = f"{outcome.added}件を取り込みました。"
        if outcome.duplicates:
            msg += f"取り込み済みの{outcome.duplicates}件は飛ばしました。"
        flash(msg, "info")
        return redirect(url_for("main.history"))
    return render_template("import.html")


# ---------- 家族一覧 (F-07) ----------

@bp.get("/family")
@login_required
def family():
    # 明細はテンプレートに渡さず、集計値だけを渡す
    users = db_session.scalars(select(User).order_by(User.sort_order)).all()
    summaries = [compute_summary(db_session, u) for u in users]
    family_rows = [
        dict(
            name=s.display_name,
            is_me=s.user_id == g.user.id,
            total=s.total,
            remaining=s.remaining,
            rate=s.rate,
            rate_capped=s.rate_capped,
            last_sync_at=s.last_sync_at,
        )
        for s in summaries
    ]
    return render_template("family.html", rows=family_rows)


# ---------- 設定 (F-08, Gmail連携, 対象外ルール) ----------

@bp.get("/settings")
@login_required
def settings():
    rules = db_session.scalars(select(ExclusionRule).order_by(ExclusionRule.keyword)).all()
    return render_template(
        "settings.html",
        setting=g.user.card_setting,
        conn=g.user.gmail_connection,
        rules=rules,
        dev_mode=current_app.config["APP"].dev_mode,
    )


@bp.post("/settings/period")
@login_required
def save_period():
    s = g.user.card_setting
    try:
        start = date.fromisoformat(request.form.get("period_start", ""))
        end = date.fromisoformat(request.form.get("period_end", ""))
    except ValueError:
        flash("日付の形式が正しくありません。", "error")
        return redirect(url_for("main.settings"))
    if end < start or (end - start).days > 400:
        flash("終了日は開始日以降、期間は400日以内にしてください。", "error")
        return redirect(url_for("main.settings"))
    card_label = (request.form.get("card_label") or "").strip()[:100] or None
    label_changed = card_label != s.card_label
    s.period_start, s.period_end, s.card_label = start, end, card_label
    s.period_confirmed = True
    if label_changed:
        reclassify_user(db_session, g.user)
    # 期間を広げたときに新しい範囲のメールをすぐ取りに行けるよう、待ち時間をリセットする
    g.user.gmail_connection.last_attempt_at = None
    db_session.commit()
    flash("集計期間を保存しました。対象金額は新しい期間で再計算されます。", "info")
    return redirect(url_for("main.settings"))


@bp.post("/settings/vpass")
@login_required
def confirm_vpass():
    g.user.card_setting.vpass_notice_confirmed = request.form.get("confirmed") == "1"
    db_session.commit()
    return redirect(safe_next(url_for("main.settings")))


@bp.post("/gmail/connect")
@login_required
def gmail_connect():
    cfg = current_app.config["APP"]
    if cfg.dev_mode and not cfg.google_client_id:
        conn = g.user.gmail_connection
        conn.status, conn.is_mock, conn.last_error, conn.last_attempt_at = "connected", True, None, None
        db_session.commit()
        flash("開発用のダミーGmailと連携しました。", "info")
        return redirect(url_for("main.home"))
    return oauth.google_gmail.authorize_redirect(
        external_url("main.gmail_callback"),
        access_type="offline",
        prompt="consent",  # 更新トークンを確実に受け取るため毎回同意画面を出す
        login_hint=g.user.email,
    )


@bp.get("/gmail/callback")
@login_required
def gmail_callback():
    try:
        token = oauth.google_gmail.authorize_access_token()
    except Exception:  # noqa: BLE001
        flash("Gmailの連携に失敗しました。", "error")
        return redirect(url_for("main.settings"))

    info = token.get("userinfo") or {}
    if info.get("sub") != g.user.google_sub:
        # ログイン中の本人とは別のGoogleアカウントのGmailは連携させない
        revoke_token(token.get("refresh_token") or token.get("access_token", ""))
        flash("ログイン中と同じGoogleアカウントで許可してください。", "error")
        return redirect(url_for("main.settings"))
    if not token.get("refresh_token"):
        flash("Googleから更新トークンを受け取れませんでした。もう一度連携してください。", "error")
        return redirect(url_for("main.settings"))

    conn = g.user.gmail_connection
    conn.refresh_token_enc = encrypt(token["refresh_token"])
    conn.status, conn.is_mock, conn.last_error, conn.last_attempt_at = "connected", False, None, None
    db_session.commit()
    flash("Gmailと連携しました。", "info")
    return redirect(url_for("main.home"))


@bp.post("/gmail/disconnect")
@login_required
def gmail_disconnect():
    conn = g.user.gmail_connection
    if conn.refresh_token_enc:
        token = decrypt(conn.refresh_token_enc)
        if token:
            revoke_token(token)
    conn.refresh_token_enc = None
    conn.status, conn.is_mock = "disconnected", False
    db_session.commit()
    flash("Gmail連携を解除しました。取り込み済みの取引と集計は残ります。", "info")
    return redirect(url_for("main.settings"))


@bp.post("/rules")
@admin_required
def add_rule():
    keyword = (request.form.get("keyword") or "").strip()[:100]
    applies_to = request.form.get("applies_to", "merchant")
    if not keyword or applies_to not in ("merchant", "usage", "all"):
        flash("キーワードを入力してください。", "error")
        return redirect(url_for("main.settings"))
    db_session.add(ExclusionRule(keyword=keyword, applies_to=applies_to, memo=(request.form.get("memo") or "")[:200]))
    db_session.flush()
    reclassify_all(db_session)
    db_session.commit()
    flash(f"対象外ルール「{keyword}」を追加し、全員の取引を再判定しました。", "info")
    return redirect(url_for("main.settings"))


@bp.post("/rules/<int:rule_id>/toggle")
@admin_required
def toggle_rule(rule_id):
    rule = db_session.get(ExclusionRule, rule_id) or abort(404)
    rule.enabled = not rule.enabled
    db_session.flush()
    reclassify_all(db_session)
    db_session.commit()
    return redirect(url_for("main.settings"))


@bp.post("/rules/<int:rule_id>/delete")
@admin_required
def delete_rule(rule_id):
    rule = db_session.get(ExclusionRule, rule_id) or abort(404)
    db_session.delete(rule)
    db_session.flush()
    reclassify_all(db_session)
    db_session.commit()
    return redirect(url_for("main.settings"))


@bp.app_template_filter("yen")
def yen(value) -> str:
    return "-" if value is None else f"{int(value):,}円"

