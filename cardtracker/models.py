"""テーブル定義(要件定義書 8. データ設計)。"""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

TARGET = "target"      # 対象
EXCLUDED = "excluded"  # 対象外
REVIEW = "review"      # 要確認
STATUSES = (TARGET, EXCLUDED, REVIEW)
STATUS_LABELS = {TARGET: "対象", EXCLUDED: "対象外", REVIEW: "要確認"}

DEFAULT_TARGET_AMOUNT = 1_000_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # GoogleアカウントID(sub)。初回ログイン時に、登録済みメールアドレスと結び付ける
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(50))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    card_setting: Mapped["CardSetting"] = relationship(back_populates="user", uselist=False)


class CardSetting(Base):
    __tablename__ = "card_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    target_amount: Mapped[int] = mapped_column(Integer, default=DEFAULT_TARGET_AMOUNT)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    # 本人が期間を確認・保存したか(未設定のあいだはホームで案内する)
    period_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 明細CSVの見出し行にあるカード名に含まれるべき文字列。
    # 空なら全カードを集計する。別カードの明細を除外したいときに使う。
    card_label: Mapped[str | None] = mapped_column(String(100))

    user: Mapped[User] = relationship(back_populates="card_setting")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("user_id", "import_key", name="uq_user_import_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(10), default="csv")  # 現在はcsvのみ(本番DBにある列なので残す)
    # CSV取込の重複防止キー(利用日・利用先・金額・同じ内容の何件目か から作るハッシュ)
    import_key: Mapped[str | None] = mapped_column(String(64))

    used_at: Mapped[datetime] = mapped_column(DateTime)  # 日本時間
    merchant: Mapped[str] = mapped_column(String(200))
    card_label: Mapped[str | None] = mapped_column(String(100))
    original_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # 明細CSVの円金額
    corrected_amount: Mapped[int | None] = mapped_column(Integer)  # 本人が修正した円金額

    auto_status: Mapped[str] = mapped_column(String(10), default=REVIEW)
    auto_reason: Mapped[str] = mapped_column(String(200), default="")
    final_status: Mapped[str] = mapped_column(String(10), default=REVIEW)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def effective_amount(self) -> int:
        """集計に使う円金額。本人が修正した金額があればそれを優先する。"""
        return self.corrected_amount if self.corrected_amount is not None else int(self.original_amount)

    @property
    def used_at_label(self) -> str:
        return self.used_at.strftime("%Y/%m/%d")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.final_status]


class ExclusionRule(Base):
    __tablename__ = "exclusion_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(String(100))  # 利用先にこの文字を含む取引を対象外にする
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    memo: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

