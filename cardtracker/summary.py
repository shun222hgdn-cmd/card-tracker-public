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
    total: int
    period_start: date
    period_end: date
    last_updated_at: datetime | None = None  # 最後にCSVを取り込んだ(取引を登録した)日時(UTC)
    review_count: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.target_amount - self.total)

    @property
    def rate(self) -> float:
        if not self.target_amount:
            return 0.0
        return self.total / self.target_amount * 100

    @property
    def rate_capped(self) -> float:
        return min(100.0, self.rate)


def in_period(tx: Transaction, start: date, end: date) -> bool:
    return tx.used_at is not None and datetime.combine(start, time.min) <= tx.used_at <= datetime.combine(end, time.max)


def compute_summary(session, user: User) -> Summary:
    s = user.card_setting
    summary = Summary(
        user_id=user.id,
        display_name=user.display_name,
        target_amount=s.target_amount,
        total=0,
        period_start=s.period_start,
        period_end=s.period_end,
    )
    for tx in session.scalars(select(Transaction).where(Transaction.user_id == user.id)):
        if summary.last_updated_at is None or tx.created_at > summary.last_updated_at:
            summary.last_updated_at = tx.created_at
        if tx.used_at is not None and not in_period(tx, s.period_start, s.period_end):
            continue
        if tx.final_status == TARGET and tx.effective_amount is not None:
            summary.total += tx.effective_amount
        elif tx.final_status == REVIEW:
            summary.review_count += 1
    return summary
