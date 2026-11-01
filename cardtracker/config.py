"""環境変数から設定を読み込む。

秘密情報はすべて環境変数で渡し、コードやGitHubには書かない。
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

LOCAL_DB = f"sqlite:///{Path(__file__).resolve().parent.parent / 'instance' / 'card_tracker.db'}"
DEV_DEFAULT_USERS = "me@example.com:本人:admin,father@example.com:父,mother@example.com:母"


@dataclass
class AllowedUser:
    email: str
    display_name: str
    is_admin: bool


def parse_allowed_users(raw: str) -> list[AllowedUser]:
    """ALLOWED_USERS="me@gmail.com:本人:admin,dad@gmail.com:父,mom@gmail.com:母" を解釈する。"""
    users = []
    for item in filter(None, (s.strip() for s in raw.split(","))):
        email, name, role = (item.split(":") + ["", ""])[:3]
        users.append(AllowedUser(email.strip().lower(), name.strip() or email.strip(), role.strip() == "admin"))
    return users


@dataclass
class Config:
    secret_key: str
    database_url: str
    dev_mode: bool
    allowed_users: list[AllowedUser] = field(default_factory=list)
    google_client_id: str = ""
    google_client_secret: str = ""
    base_url: str = ""
    testing: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        on_vercel = bool(env("VERCEL"))
        # 開発用のダミーログインは、Vercel上では絶対に有効にしない
        dev_mode = env("DEV_MODE") == "1" and not on_vercel
        if on_vercel and not env("DATABASE_URL"):
            # Vercelのファイルは消えるため、SQLiteで動かすとデータを失う。起動を止めて気付かせる。
            raise RuntimeError("Vercelでは DATABASE_URL (Postgres) の設定が必須です")
        # ダミーの3人を作るのはローカルDBのときだけ(本番DBに接続した管理コマンドで混ざらないように)
        default_users = DEV_DEFAULT_USERS if dev_mode and not env("DATABASE_URL") else ""

        cfg = cls(
            secret_key=env("SECRET_KEY", "dev-secret-key" if dev_mode else ""),
            database_url=env("DATABASE_URL") or LOCAL_DB,
            dev_mode=dev_mode,
            allowed_users=parse_allowed_users(env("ALLOWED_USERS") or default_users),
            google_client_id=env("GOOGLE_CLIENT_ID", ""),
            google_client_secret=env("GOOGLE_CLIENT_SECRET", ""),
            base_url=env("APP_BASE_URL", "").rstrip("/"),
        )
        required = {
            "SECRET_KEY": cfg.secret_key,
            "GOOGLE_CLIENT_ID": cfg.google_client_id,
            "GOOGLE_CLIENT_SECRET": cfg.google_client_secret,
            "ALLOWED_USERS": cfg.allowed_users,
        }
        missing = [name for name, value in required.items() if not value]
        if missing and not dev_mode:
            raise RuntimeError("環境変数が未設定です: " + ", ".join(missing))
        return cfg
