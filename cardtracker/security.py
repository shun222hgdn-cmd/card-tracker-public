"""CSRF対策。"""
from __future__ import annotations

import hmac
import secrets

from flask import abort, request, session
from markupsafe import Markup


def csrf_field() -> Markup:
    """フォームに埋め込む、CSRF対策用の隠しトークン。"""
    token = session.setdefault("_csrf", secrets.token_urlsafe(32))
    return Markup(f'<input type="hidden" name="_csrf" value="{token}">')


def check_csrf() -> None:
    """POSTのたびに、フォームに埋め込んだトークンとセッションのトークンが一致するか確認する。"""
    expected = session.get("_csrf", "")
    if request.method == "POST" and not (expected and hmac.compare_digest(request.form.get("_csrf", ""), expected)):
        abort(400, "フォームの有効期限が切れました。ページを再読み込みしてください。")

