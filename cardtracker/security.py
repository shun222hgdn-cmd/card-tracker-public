"""CSRF対策とトークン暗号化。"""
from __future__ import annotations

import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken
from flask import abort, current_app, request, session


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


def _fernet() -> Fernet:
    key = current_app.config["APP"].token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY が設定されていません")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str | None:
    """復号できない(暗号化キーを変えた等)ときは None を返し、再連携してもらう。"""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None
