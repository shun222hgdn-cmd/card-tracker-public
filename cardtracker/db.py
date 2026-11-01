"""データベース接続。

ローカル開発はSQLiteファイル、Vercel本番はPostgres(Neon)を使う。
どちらもSQLAlchemy経由で扱うので、アプリのコードは同じまま切り替えられる。
"""
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker
from sqlalchemy.pool import NullPool

db_session = scoped_session(sessionmaker(expire_on_commit=False))


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    # Neon/Vercelが渡す postgres:// をSQLAlchemy + psycopg3 の形式に直す
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def init_engine(url: str):
    url = normalize_database_url(url)
    if url.startswith("sqlite"):
        from pathlib import Path

        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    else:
        # サーバーレスでは関数ごとに接続を持ち越さないほうが安全
        engine = create_engine(url, poolclass=NullPool, pool_pre_ping=True)

    db_session.remove()
    db_session.configure(bind=engine)
    return engine


def create_tables(engine) -> None:
    from . import models  # noqa: F401  モデル定義を読み込んでからテーブルを作る

    Base.metadata.create_all(engine)
    remove_gmail_schema(engine)


# Gmail連携を廃止したとき(2026-11)に不要になった列
LEGACY_COLUMNS = {
    "transactions": [
        "gmail_message_id", "seq", "usage_type", "currency",
        "is_cancel", "cancelled", "parse_failed", "duplicate_suspect",
    ],
    "card_settings": ["vpass_notice_confirmed"],
    "exclusion_rules": ["applies_to"],
}


def remove_gmail_schema(engine) -> None:
    """Gmail連携時代の本番DB(Postgres)を、今のテーブル定義に合わせる。

    - Gmailから取り込んだ取引は、CSVで取り込み直すと二重計上になるので削除する
    - Gmailの認可情報(暗号化済みトークン)のテーブルを削除する
    - 使わなくなった列を削除する
    すでに移行済みなら何もしない。ローカルのSQLiteは作り直せばよいので対象外。
    """
    if engine.dialect.name != "postgresql":
        return
    if "gmail_message_id" not in {c["name"] for c in inspect(engine).get_columns("transactions")}:
        return
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM transactions WHERE source = 'gmail'"))
        conn.execute(text("DROP TABLE IF EXISTS gmail_connections"))
        for table, columns in LEGACY_COLUMNS.items():
            for column in columns:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}"))
