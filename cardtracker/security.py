"""CSRF対策。"""
from __future__ import annotations

import hmac
import secrets

from flask import abort, request, session


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf() -> None:
    """POSTのたびに、フォームに埋め込んだトークンとセッションのトークンが一致するか確認する。"""
    if request.method != "POST":
        return
    sent = request.form.get("_csrf", "")
    expected = session.get("_csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "フォームの有効期限が切れました。ページを再読み込みしてください。")

