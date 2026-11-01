"""Vpassの「ご利用明細」CSVの取り込み。

家族それぞれが、Vpassで毎月ダウンロードできる明細CSVをアップロードして集計する。
想定しているCSV(Shift_JIS)。カードごとに「〇〇 様,カード番号,カード名」の見出し行があり、
その下に「利用日,利用先,金額,支払区分,今回回数,支払金額,備考」の7列の明細が続く:

    〇〇 様,4980-00**-****-****,三井住友カード（ＮＬ）
    2026/09/01,セブン－イレブン,648,１,１,648,
    2026/09/03,GITHUB, INC. (HTTPSGITHUB.C),1394,,,,10.00 USD ...   ← 利用先にカンマが入ることがある
    〇〇 様,6900-00**-****-****,ＡｐｐｌｅＰａｙ／ｉＤ             ← 同じCSVに別のカード(iDなど)が続くことがある
    2026/09/05,飲料自販機／ｉＤ,120,,,,

方針:
- 1列目が日付の行だけを取引として読む。それ以外の行(合計など)は飛ばす
- カード名は、その明細の直前にある見出し行のものを使う
- 利用先にカンマが入って列が増えたときは、増えた分を利用先としてつなぎ直す(後ろの5列は位置が決まっている)
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


def parse_csv(text: str) -> list[tuple[datetime, str, int, str | None]]:
    """[(利用日, 利用先, 金額, カード名), ...] を返す。"""
    card_label, rows = None, []
    for row in csv.reader(io.StringIO(text)):
        # 全角・半角をそろえる(ＡＭＡＺＯＮ→AMAZON、１２３→123)
        cells = [unicodedata.normalize("NFKC", c).strip() for c in row] + ["", "", ""]
        if cells[0].endswith("様"):
            card_label = cells[2] or None  # 見出し行「〇〇 様,カード番号,カード名」の3列目
            continue
        if len(row) > 7:
            # 利用先のカンマで列が増えた: 後ろ5列(金額〜備考)の位置は決まっているので、手前をつなぎ直す
            cells = [cells[0], ", ".join(cells[1:len(row) - 5])] + cells[len(row) - 5:]
        used_at, merchant, amount = parse_date(cells[0]), re.sub(r"\s+", " ", cells[1]), cells[2].replace("円", "")
        if used_at and merchant and AMOUNT_RE.match(amount):
            rows.append((used_at, merchant, int(amount.replace(",", "")), card_label))
    if not rows:
        raise CsvFormatError("取引の行が見つかりません。三井住友カードの明細CSVか確認してください。")
    return rows


def import_csv(session, user: User, data: bytes) -> tuple[int, int]:
    """取り込んで (追加した件数, 取り込み済みで飛ばした件数) を返す。"""
    rows = parse_csv(decode(data))
    existing = set(session.scalars(select(Transaction.import_key).where(Transaction.user_id == user.id)))
    rules = load_rules(session)
    seen: Counter = Counter()
    added = 0
    for used_at, merchant, amount, card_label in rows:
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
