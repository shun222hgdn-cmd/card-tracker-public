"""対象 / 対象外 / 要確認 の自動判定(要件 F-03)。

判定に使うのはメールから抜き出した利用先・区分・カード名だけで、メール全文は使わない。
上から順に当てはまったものを採用する。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import EXCLUDED, REVIEW, TARGET, ExclusionRule, Transaction


def match_key(text: str | None) -> str:
    """表記ゆれをそろえた比較用の文字列(全角→半角、大文字化、空白除去)。"""
    if not text:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).upper()


@dataclass
class Rule:
    keyword: str
    applies_to: str

    @property
    def key(self) -> str:
        return match_key(self.keyword)


def rules_from_db(rows: list[ExclusionRule]) -> list[Rule]:
    return [Rule(r.keyword, r.applies_to) for r in rows if r.enabled and match_key(r.keyword)]


def find_rule(tx: Transaction, rules: list[Rule]) -> Rule | None:
    merchant = match_key(tx.merchant)
    usage = match_key(tx.usage_type)
    for rule in rules:
        targets = {"merchant": [merchant], "usage": [usage]}.get(rule.applies_to, [merchant, usage])
        if any(rule.key in t for t in targets if t):
            return rule
    return None


def classify(tx: Transaction, rules: list[Rule], card_label_setting: str | None) -> tuple[str, str]:
    if tx.parse_failed:
        return REVIEW, "メールを解析できませんでした"
    if tx.is_cancel:
        return EXCLUDED, "取消の通知"
    if tx.cancelled:
        return EXCLUDED, "取消済み"
    if tx.source == "gmail" and match_key(card_label_setting):
        if not tx.card_label:
            return REVIEW, "カード区分を判別できません"
        if match_key(card_label_setting) not in match_key(tx.card_label):
            return EXCLUDED, "対象カード以外"
    rule = find_rule(tx, rules)
    if rule:
        return EXCLUDED, f"対象外ルール「{rule.keyword}」"
    if tx.currency != "JPY":
        return REVIEW, "外貨建て(円の金額を入力してください)"
    if tx.duplicate_suspect:
        return REVIEW, "重複の疑い"
    return TARGET, ""


def apply_classification(tx: Transaction, rules: list[Rule], card_label_setting: str | None) -> None:
    """自動判定を更新する。本人が手動で決めた取引は最終判定を変えない(要件 F-04)。"""
    tx.auto_status, tx.auto_reason = classify(tx, rules, card_label_setting)
    if not tx.manual_override:
        tx.final_status = tx.auto_status
