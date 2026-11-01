"""データベース接続。

ローカル開発はSQLiteファイル、Vercel本番はPostgres(Neon)を使う。
どちらもSQLAlchemy経由で扱うので、アプリのコードは同じまま切り替えられる。
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker
from sqlalchemy.pool import NullPool

db_session = scoped_session(sessionmaker(expire_on_commit=False))


class Base(DeclarativeBase):
    pass


def init_engine(url: str):
    # Neon/Vercelが渡す postgres:// を、SQLAlchemy + psycopg3 の形式に直す
    url = url.replace("postgres://", "postgresql://", 1).replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})
    else:
        # サーバーレスでは関数ごとに接続を持ち越さないほうが安全
        engine = create_engine(url, poolclass=NullPool, pool_pre_ping=True)
    db_session.remove()
    db_session.configure(bind=engine)
    return engine


def create_tables(engine) -> None:
    from . import models  # noqa: F401  モデル定義を読み込んでからテーブルを作る

    Base.metadata.create_all(engine)
