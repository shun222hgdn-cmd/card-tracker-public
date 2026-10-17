import pytest

from cardtracker import create_app
from cardtracker.config import DEV_DEFAULT_USERS, Config, parse_allowed_users

CSRF = "test-csrf-token"


@pytest.fixture
def app(tmp_path):
    cfg = Config(
        secret_key="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        dev_mode=True,
        testing=True,
        allowed_users=parse_allowed_users(DEV_DEFAULT_USERS),
    )
    return create_app(cfg)


class Client:
    """ログインとCSRFトークンの付与をまとめたテスト用クライアント。"""

    def __init__(self, app):
        self.c = app.test_client()
        self._set_csrf()

    def _set_csrf(self):
        with self.c.session_transaction() as s:
            s["_csrf"] = CSRF

    def login(self, user_id):
        resp = self.post("/login/dev", user_id=user_id)
        self._set_csrf()  # ログイン時にセッションを作り直すので、トークンを入れ直す
        return resp

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **data):
        data.setdefault("_csrf", CSRF)
        return self.c.post(url, data=data)


@pytest.fixture
def client_for(app):
    def make(user_id):
        cl = Client(app)
        cl.login(user_id)
        return cl

    return make
