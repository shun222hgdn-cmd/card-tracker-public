"""Gmail APIとのやり取り。

google-api-python-client は使わず、requests で REST API を直接呼ぶ。
依存を減らし、どのURLに何を送っているかを説明しやすくするため。
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import requests

from .parser import SENDER

JST = timezone(timedelta(hours=9))
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TIMEOUT = 15


class GmailError(Exception):
    pass


class GmailAuthError(GmailError):
    """更新トークンが無効(連携解除・期限切れ)。本人に再連携してもらう必要がある。"""


@dataclass
class GmailMessage:
    id: str
    subject: str
    bodies: list[tuple[str, str]]  # [(mime_type, text)]


def build_query(start: date, end: date) -> str:
    # after/before にUNIX秒を渡すと、日本時間の0時区切りで正確に絞り込める
    after = int(datetime.combine(start, time.min, JST).timestamp())
    before = int(datetime.combine(end + timedelta(days=1), time.min, JST).timestamp())
    subjects = " OR ".join(f'subject:"{s}"' for s in ("ご利用のお知らせ", "取消"))
    return f"from:{SENDER} ({subjects}) after:{after} before:{before}"


def _decode_part(part: dict) -> str:
    data = part.get("body", {}).get("data")
    if not data:
        return ""
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    charset = "utf-8"
    for h in part.get("headers", []):
        if h.get("name", "").lower() == "content-type" and "charset=" in h.get("value", "").lower():
            charset = h["value"].lower().split("charset=")[1].split(";")[0].strip().strip('"')
    for cs in (charset, "utf-8", "iso-2022-jp", "shift_jis"):
        try:
            return raw.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def extract_bodies(payload: dict) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        if mime in ("text/plain", "text/html"):
            text = _decode_part(part)
            if text:
                found.append((mime, text))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return found


class GmailClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token: str | None = None

    def _token(self) -> str:
        if self._access_token:
            return self._access_token
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=TIMEOUT,
        )
        if resp.status_code in (400, 401):
            raise GmailAuthError("Gmailの認可が無効になっています。設定画面から再連携してください。")
        if not resp.ok:
            raise GmailError(f"トークンの更新に失敗しました (HTTP {resp.status_code})")
        self._access_token = resp.json()["access_token"]
        return self._access_token

    def _get(self, path: str, params: dict) -> dict:
        resp = requests.get(
            f"{GMAIL_API}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 401:
            raise GmailAuthError("Gmailの認可が無効になっています。設定画面から再連携してください。")
        if not resp.ok:
            raise GmailError(f"Gmail APIエラー (HTTP {resp.status_code})")
        return resp.json()

    def list_message_ids(self, start: date, end: date) -> list[str]:
        ids: list[str] = []
        params = {"q": build_query(start, end), "maxResults": 100}
        while True:
            data = self._get("/messages", params)
            ids.extend(m["id"] for m in data.get("messages", []))
            token = data.get("nextPageToken")
            if not token or len(ids) >= 2000:
                return ids
            params["pageToken"] = token

    def get_message(self, message_id: str) -> GmailMessage:
        data = self._get(f"/messages/{message_id}", {"format": "full"})
        payload = data.get("payload", {})
        subject = next(
            (h["value"] for h in payload.get("headers", []) if h.get("name", "").lower() == "subject"),
            "",
        )
        return GmailMessage(id=message_id, subject=subject, bodies=extract_bodies(payload))


def revoke_token(token: str) -> None:
    """連携解除時にGoogle側の認可も取り消す。失敗しても手元のトークンは削除する。"""
    try:
        requests.post(REVOKE_URL, params={"token": token}, timeout=TIMEOUT)
    except requests.RequestException:
        pass
