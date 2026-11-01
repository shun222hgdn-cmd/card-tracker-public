"""Vpassの「ご利用明細」CSVの取り込み。

家族それぞれが、Vpassで毎月ダウンロードできる明細CSVをアップロードして集計する。
想定しているCSV(Shift_JIS):

    〇〇 様,4980-00**-****-****,三井住友カード（ＮＬ）
    2026/09/01,セブン－イレブン,648,１,１,648,
    2026/09/03,ＡＭＡＺＯＮ．ＣＯ．ＪＰ,12800,１,１,12800,
    ,,,,,13448,

方針:
- 1列目が日付、3列目が金額の行だけを取引として読む。それ以外の行(見出し・合計など)は飛ばす
- アップロードされたファイルは保存しない。読み取った日付・利用先・金額だけを保存する
- 同じ明細を何度アップロードしても二重に計上しない(内容から作ったキーで判定)
- 返品などのマイナス金額はそのまま保存し、合計から差し引かれるようにする
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from .classifier import apply_classification, load_rules
from .models import Transaction, User

MAX_BYTES = 1_000_000
DATE_RE = re.compile(r"^(\d{2,4})/(\d{1,2})/(\d{1,2})$")
AMOUNT_RE = re.compile(r"^-?\d[\d,]*$")


class CsvFormatError(ValueError):
    pass


@dataclass
class CsvRow:
    used_at: datetime
    merchant: str
    amount: int


@dataclass
class CsvParseResult:
    card_label: str | None
    rows: list[CsvRow] = field(default_factory=list)
    skipped: int = 0  # 取引として読めなかった、空でない行の数


@dataclass
class ImportOutcome:
    added: int
    duplicates: int
    skipped: int
    card_label: str | None


def decode(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise CsvFormatError("ファイルが大きすぎます(1MBまで)")
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvFormatError("文字コードを判別できません。Vpassからダウンロードしたままのファイルを選んでください。")


def _cell(row: list[str], i: int) -> str:
    return unicodedata.normalize("NFKC", row[i]).strip() if i < len(row) else ""


def _parse_date(text: str) -> datetime | None:
    m = DATE_RE.match(text)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if y < 100:
        y += 2000
    try:
        return datetime(y, mo, d)
    except ValueError:
        return None


def parse_csv(text: str) -> CsvParseResult:
    result = CsvParseResult(card_label=None)
    for row in csv.reader(io.StringIO(text)):
        if not any(c.strip() for c in row):
            continue
        first = _cell(row, 0)
        # 見出し行「〇〇 様,カード番号,カード名」からカード名を取る
        if first.endswith("様") and result.card_label is None and len(row) >= 3:
            result.card_label = _cell(row, 2) or None
            continue
        used_at = _parse_date(first)
        amount_text = _cell(row, 2).replace("円", "")
        merchant = re.sub(r"\s+", " ", _cell(row, 1))
        if used_at is None or not merchant or not AMOUNT_RE.match(amount_text):
            result.skipped += 1
            continue
        result.rows.append(CsvRow(used_at=used_at, merchant=merchant, amount=int(amount_text.replace(",", ""))))
    if not result.rows:
        raise CsvFormatError("取引の行が見つかりません。三井住友カードの明細CSVか確認してください。")
    return result


def import_key(row: CsvRow, occurrence: int) -> str:
    """同じ日・同じ店・同じ金額の買い物が2回あっても区別できるよう、何件目かも含める。"""
    raw = f"{row.used_at:%Y-%m-%d}|{row.merchant}|{row.amount}|{occurrence}"
    return hashlib.sha256(raw.encode()).hexdigest()


def import_csv(session, user: User, data: bytes) -> ImportOutcome:
    parsed = parse_csv(decode(data))
    existing = set(
        session.scalars(
            select(Transaction.import_key).where(Transaction.user_id == user.id, Transaction.import_key.is_not(None))
        )
    )
    rules = load_rules(session)
    seen: Counter = Counter()
    added = duplicates = 0
    for row in parsed.rows:
        ident = (row.used_at, row.merchant, row.amount)
        key = import_key(row, seen[ident])
        seen[ident] += 1
        if key in existing:
            duplicates += 1
            continue
        tx = Transaction(
            user_id=user.id,
            source="csv",
            import_key=key,
            used_at=row.used_at,
            merchant=row.merchant,
            card_label=parsed.card_label,
            original_amount=Decimal(row.amount),
        )
        session.add(tx)
        apply_classification(tx, rules, user.card_setting.card_label)
        existing.add(key)
        added += 1
    session.commit()
    return ImportOutcome(added=added, duplicates=duplicates, skipped=parsed.skipped, card_label=parsed.card_label)
