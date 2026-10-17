"""テーブル定義(要件定義書 8. データ設計)。"""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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
    gmail_connection: Mapped["GmailConnection"] = relationship(back_populates="user", uselist=False)


class CardSetting(Base):
    __tablename__ = "card_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    target_amount: Mapped[int] = mapped_column(Integer, default=DEFAULT_TARGET_AMOUNT)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    # 本人が期間を確認・保存したか(未設定のあいだはホームで案内する)
    period_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 通知メールの「◯◯についてカードの利用内容をお知らせします」の◯◯に含まれるべき文字列。
    # 空なら全カードを集計する。別カードの通知を除外したいときに使う。
    card_label: Mapped[str | None] = mapped_column(String(100))
    vpass_notice_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="card_setting")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "gmail_message_id", "seq", name="uq_user_message_seq"),
        UniqueConstraint("user_id", "import_key", name="uq_user_import_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(10), default="gmail")  # gmail / csv / manual
    gmail_message_id: Mapped[str | None] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer, default=0)  # 1通に複数取引があるときの順番
    # CSV取込の重複防止キー(利用日・利用先・金額・同じ内容の何件目か から作るハッシュ)
    import_key: Mapped[str | None] = mapped_column(String(64))

    used_at: Mapped[datetime | None] = mapped_column(DateTime)  # 日本時間
    merchant: Mapped[str | None] = mapped_column(String(200))
    usage_type: Mapped[str | None] = mapped_column(String(50))  # 「買物」など
    card_label: Mapped[str | None] = mapped_column(String(100))
    original_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # メール記載の金額
    currency: Mapped[str | None] = mapped_column(String(3))
    corrected_amount: Mapped[int | None] = mapped_column(Integer)  # 本人が修正した円金額

    is_cancel: Mapped[bool] = mapped_column(Boolean, default=False)   # 取消の通知そのもの
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)   # 後から取消された元取引
    parse_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_suspect: Mapped[bool] = mapped_column(Boolean, default=False)

    auto_status: Mapped[str] = mapped_column(String(10), default=REVIEW)
    auto_reason: Mapped[str] = mapped_column(String(200), default="")
    final_status: Mapped[str] = mapped_column(String(10), default=REVIEW)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def effective_amount(self) -> int | None:
        """集計に使う円金額。修正額があればそれを優先し、外貨は修正額がない限り計上しない。"""
        if self.corrected_amount is not None:
            return self.corrected_amount
        if self.currency == "JPY" and self.original_amount is not None:
            return int(self.original_amount)
        return None

    @property
    def used_at_label(self) -> str:
        if self.used_at is None:
            return "日時不明"
        # 明細CSVには時刻がないので日付だけ表示する
        return self.used_at.strftime("%Y/%m/%d" if self.source == "csv" else "%Y/%m/%d %H:%M")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.final_status, self.final_status)


class ExclusionRule(Base):
    __tablename__ = "exclusion_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(String(100))
    # merchant: 利用先 / usage: 利用区分(「買物」など) / all: 両方
    applies_to: Mapped[str] = mapped_column(String(10), default="merchant")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    memo: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GmailConnection(Base):
    __tablename__ = "gmail_connections"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    # connected / error / expired / disconnected
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)  # 暗号化済み
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)          # 最後に成功した同期(UTC)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)       # 最後に試した同期(UTC)
    last_error: Mapped[str | None] = mapped_column(Text)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)  # 1回で取り切れなかった件数

    user: Mapped[User] = relationship(back_populates="gmail_connection")
