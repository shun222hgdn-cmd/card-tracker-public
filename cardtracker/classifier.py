"""対象 / 対象外 / 要確認 の自動判定(要件 F-03)。

判定に使うのは明細CSVから読み取った利用先とカード名だけ。上から順に当てはまったものを採用する。
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select

from .models import EXCLUDED, REVIEW, TARGET, ExclusionRule, Transaction, User


def match_key(text: str | None) -> str:
    """表記ゆれをそろえた比較用の文字列(全角→半角、大文字化、空白除去)。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).upper()


def load_rules(session) -> list[str]:
    """有効な対象外ルールのキーワード一覧。"""
    return [r.keyword for r in session.scalars(select(ExclusionRule).where(ExclusionRule.enabled)) if match_key(r.keyword)]


def classify(tx: Transaction, rules: list[str], card_label_setting: str | None) -> tuple[str, str]:
    if tx.source == "csv" and match_key(card_label_setting):
        if not tx.card_label:
            return REVIEW, "カード名を判別できません"
        if match_key(card_label_setting) not in match_key(tx.card_label):
            return EXCLUDED, "対象カード以外"
    for keyword in rules:
        if match_key(keyword) in match_key(tx.merchant):
            return EXCLUDED, f"対象外ルール「{keyword}」"
    return TARGET, ""


def apply_classification(tx: Transaction, rules: list[str], card_label_setting: str | None) -> None:
    """自動判定を更新する。本人が手動で決めた取引は最終判定を変えない(要件 F-04)。"""
    tx.auto_status, tx.auto_reason = classify(tx, rules, card_label_setting)
    if not tx.manual_override:
        tx.final_status = tx.auto_status


def reclassify(session, users: list[User] | None = None) -> None:
    """取引を判定し直す。users を省略すると全員(対象外ルールを変えたとき)。"""
    rules = load_rules(session)
    for user in users or session.scalars(select(User)):
        for tx in session.scalars(select(Transaction).where(Transaction.user_id == user.id)):
            apply_classification(tx, rules, user.card_setting.card_label)
