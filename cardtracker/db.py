"""データベース接続。

ローカル開発はSQLiteファイル、Vercel本番はPostgres(Neon)を使う。
どちらもSQLAlchemy経由で扱うので、アプリのコードは同じまま切り替えられる。
"""
from sqlalchemy import create_engine, event
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
