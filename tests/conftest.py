from io import BytesIO

import pytest

from cardtracker import create_app
from cardtracker.config import DEV_DEFAULT_USERS, Config, parse_allowed_users

CSRF = "test-csrf-token"

# Vpassの明細CSVを模したサンプル(Shift_JISで送る)
SAMPLE_CSV = (
    "〇〇 様,4980-00**-****-****,三井住友カード（ＮＬ）\r\n"
    "2026/09/01,セブン－イレブン,648,１,１,648,\r\n"
    "2026/09/03,ＡＭＡＺＯＮ．ＣＯ．ＪＰ,\"12,800\",１,１,12800,\r\n"
    "2026/09/05,モバイルＳｕｉｃａ,5000,１,１,5000,\r\n"
    "2026/09/10,ユニクロ,-1990,１,１,-1990,返品\r\n"
    "2026/09/12,セブン－イレブン,648,１,１,648,\r\n"
    ",,,,,17106,\r\n"
)
# 648 + 12800 - 1990 + 648 (Suicaは対象外ルールで除外)
SAMPLE_TOTAL = 12106


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

    def upload(self, text=SAMPLE_CSV, encoding="cp932", name="meisai.csv"):
        return self.c.post(
            "/import",
            data={"_csrf": CSRF, "csv_file": (BytesIO(text.encode(encoding)), name)},
            content_type="multipart/form-data",
        )


@pytest.fixture
def client_for(app):
    def make(user_id):
        cl = Client(app)
        cl.login(user_id)
        return cl

    return make
