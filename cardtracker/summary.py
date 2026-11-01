"""集計(要件 4-1〜4-3, F-08)。

集計はその都度DBから計算する。期間を変えればすぐに再計算された値になる。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import select

from .models import REVIEW, TARGET, Transaction, User


@dataclass
class Summary:
    user_id: int
    display_name: str
    target_amount: int
    period_start: date
    period_end: date
    total: int = 0
    review_count: int = 0
    last_updated_at: datetime | None = None  # 最後にCSVを取り込んだ(取引を登録した)日時(UTC)

    @property
    def remaining(self) -> int:
        return max(0, self.target_amount - self.total)

    @property
    def rate(self) -> float:
        return self.total / self.target_amount * 100 if self.target_amount else 0.0

    @property
    def rate_capped(self) -> float:
        return min(100.0, self.rate)


def in_period(tx: Transaction, start: date, end: date) -> bool:
    return datetime.combine(start, time.min) <= tx.used_at <= datetime.combine(end, time.max)


def compute_summary(session, user: User) -> Summary:
    s = user.card_setting
    summary = Summary(user.id, user.display_name, s.target_amount, s.period_start, s.period_end)
    for tx in session.scalars(select(Transaction).where(Transaction.user_id == user.id)):
        summary.last_updated_at = max(filter(None, (summary.last_updated_at, tx.created_at)))
        if not in_period(tx, s.period_start, s.period_end):
            continue
        if tx.final_status == TARGET:
            summary.total += tx.effective_amount
        elif tx.final_status == REVIEW:
            summary.review_count += 1
    return summary
