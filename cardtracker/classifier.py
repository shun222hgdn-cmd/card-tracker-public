"""対象 / 対象外 / 要確認 の自動判定(要件 F-03)。

判定に使うのは明細CSVから読み取った利用先とカード名だけ。
上から順に当てはまったものを採用する。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select

from .models import EXCLUDED, REVIEW, TARGET, ExclusionRule, Transaction, User


def match_key(text: str | None) -> str:
    """表記ゆれをそろえた比較用の文字列(全角→半角、大文字化、空白除去)。"""
    if not text:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).upper()


@dataclass
class Rule:
    keyword: str

    @property
    def key(self) -> str:
        return match_key(self.keyword)


def rules_from_db(rows: list[ExclusionRule]) -> list[Rule]:
    return [Rule(r.keyword) for r in rows if r.enabled and match_key(r.keyword)]


def load_rules(session) -> list[Rule]:
    return rules_from_db(session.scalars(select(ExclusionRule)).all())


def find_rule(tx: Transaction, rules: list[Rule]) -> Rule | None:
    merchant = match_key(tx.merchant)
    return next((rule for rule in rules if merchant and rule.key in merchant), None)


def classify(tx: Transaction, rules: list[Rule], card_label_setting: str | None) -> tuple[str, str]:
    if tx.source == "csv" and match_key(card_label_setting):
        if not tx.card_label:
            return REVIEW, "カード名を判別できません"
        if match_key(card_label_setting) not in match_key(tx.card_label):
            return EXCLUDED, "対象カード以外"
    rule = find_rule(tx, rules)
    if rule:
        return EXCLUDED, f"対象外ルール「{rule.keyword}」"
    return TARGET, ""


def apply_classification(tx: Transaction, rules: list[Rule], card_label_setting: str | None) -> None:
    """自動判定を更新する。本人が手動で決めた取引は最終判定を変えない(要件 F-04)。"""
    tx.auto_status, tx.auto_reason = classify(tx, rules, card_label_setting)
    if not tx.manual_override:
        tx.final_status = tx.auto_status


def reclassify_user(session, user: User, rules: list[Rule] | None = None) -> None:
    rules = rules if rules is not None else load_rules(session)
    for tx in session.scalars(select(Transaction).where(Transaction.user_id == user.id)):
        apply_classification(tx, rules, user.card_setting.card_label)


def reclassify_all(session) -> None:
    """対象外ルールを変えたときに、全員の取引を判定し直す。"""
    rules = load_rules(session)
    for user in session.scalars(select(User)):
        reclassify_user(session, user, rules)
