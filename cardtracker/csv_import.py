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
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from .classifier import apply_classification, load_rules
from .models import Transaction, User

AMOUNT_RE = re.compile(r"^-?\d[\d,]*$")


class CsvFormatError(ValueError):
    pass


def decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise CsvFormatError("文字コードを判別できません。Vpassからダウンロードしたままのファイルを選んでください。")


def parse_date(text: str) -> datetime | None:
    for fmt in ("%Y/%m/%d", "%y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def parse_csv(text: str) -> tuple[str | None, list[tuple[datetime, str, int]]]:
    """(カード名, [(利用日, 利用先, 金額), ...]) を返す。"""
    card_label, rows = None, []
    for row in csv.reader(io.StringIO(text)):
        # 全角・半角をそろえる(ＡＭＡＺＯＮ→AMAZON、１２３→123)
        date_text, merchant, amount = ([unicodedata.normalize("NFKC", c).strip() for c in row] + ["", "", ""])[:3]
        if date_text.endswith("様") and card_label is None:
            card_label = amount or None  # 見出し行「〇〇 様,カード番号,カード名」の3列目
            continue
        used_at, amount = parse_date(date_text), amount.replace("円", "")
        if used_at and merchant and AMOUNT_RE.match(amount):
            rows.append((used_at, re.sub(r"\s+", " ", merchant), int(amount.replace(",", ""))))
    if not rows:
        raise CsvFormatError("取引の行が見つかりません。三井住友カードの明細CSVか確認してください。")
    return card_label, rows


def import_csv(session, user: User, data: bytes) -> tuple[int, int]:
    """取り込んで (追加した件数, 取り込み済みで飛ばした件数) を返す。"""
    card_label, rows = parse_csv(decode(data))
    existing = set(session.scalars(select(Transaction.import_key).where(Transaction.user_id == user.id)))
    rules = load_rules(session)
    seen: Counter = Counter()
    added = 0
    for used_at, merchant, amount in rows:
        # 同じ日・同じ店・同じ金額の買い物が2回あっても区別できるよう、何件目かもキーに含める
        ident = (used_at, merchant, amount)
        key = hashlib.sha256(f"{used_at:%Y-%m-%d}|{merchant}|{amount}|{seen[ident]}".encode()).hexdigest()
        seen[ident] += 1
        if key in existing:
            continue
        tx = Transaction(user_id=user.id, source="csv", import_key=key, used_at=used_at, merchant=merchant,
                         card_label=card_label, original_amount=Decimal(amount))
        session.add(tx)
        apply_classification(tx, rules, user.card_setting.card_label)
        existing.add(key)
        added += 1
    session.commit()
    return added, len(rows) - added
