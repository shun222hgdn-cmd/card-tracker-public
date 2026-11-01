"""環境変数から設定を読み込む。

秘密情報はすべて環境変数で渡し、コードやGitHubには書かない。
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AllowedUser:
    email: str
    display_name: str
    is_admin: bool


def parse_allowed_users(raw: str) -> list[AllowedUser]:
    """ALLOWED_USERS="me@gmail.com:本人:admin,dad@gmail.com:父,mom@gmail.com:母" を解釈する。"""
    users = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split(":")]
        email = parts[0].lower()
        name = parts[1] if len(parts) > 1 and parts[1] else email
        is_admin = len(parts) > 2 and parts[2].lower() == "admin"
        users.append(AllowedUser(email=email, display_name=name, is_admin=is_admin))
    return users


DEV_DEFAULT_USERS = "me@example.com:本人:admin,father@example.com:父,mother@example.com:母"


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
        on_vercel = bool(os.environ.get("VERCEL"))
        # 開発用のダミーログインは、Vercel上では絶対に有効にしない
        dev_mode = os.environ.get("DEV_MODE") == "1" and not on_vercel

        database_url = os.environ.get("DATABASE_URL", "")
        use_local_db = not database_url
        if not database_url:
            if on_vercel:
                # Vercelのファイルは消えるため、SQLiteで動かすとデータを失う。起動を止めて気付かせる。
                raise RuntimeError("Vercelでは DATABASE_URL (Postgres) の設定が必須です")
            database_url = f"sqlite:///{PROJECT_ROOT / 'instance' / 'card_tracker.db'}"

        raw_users = os.environ.get("ALLOWED_USERS", "")
        # ダミーの3人を作るのはローカルDBのときだけ(本番DBに接続した管理コマンドで混ざらないように)
        if not raw_users and dev_mode and use_local_db:
            raw_users = DEV_DEFAULT_USERS

        cfg = cls(
            secret_key=os.environ.get("SECRET_KEY", "dev-secret-key" if dev_mode else ""),
            database_url=database_url,
            dev_mode=dev_mode,
            allowed_users=parse_allowed_users(raw_users),
            google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            base_url=os.environ.get("APP_BASE_URL", "").rstrip("/"),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.dev_mode or self.testing:
            return
        missing = [
            name
            for name, value in [
                ("SECRET_KEY", self.secret_key),
                ("GOOGLE_CLIENT_ID", self.google_client_id),
                ("GOOGLE_CLIENT_SECRET", self.google_client_secret),
                ("ALLOWED_USERS", self.allowed_users),
            ]
            if not value
        ]
        if missing:
            raise RuntimeError("環境変数が未設定です: " + ", ".join(missing))
