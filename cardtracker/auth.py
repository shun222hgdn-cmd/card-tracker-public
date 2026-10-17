"""Googleログインと、登録済み3アカウント以外の拒否(要件 F-01, 2-1)。"""
from __future__ import annotations

from datetime import date
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select

from .db import db_session
from .gmail_client import GMAIL_SCOPE
from .models import CardSetting, GmailConnection, User

bp = Blueprint("auth", __name__)
oauth = OAuth()

GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"


def init_oauth(app) -> None:
    cfg = app.config["APP"]
    oauth.init_app(app)
    if not cfg.google_client_id:
        return
    # ログイン用(メールアドレスとアカウントIDだけ)と、Gmail読み取り用を別々に登録する。
    # Gmailの許可はログインとは別に、本人が設定画面から明示的に与える。
    common = dict(
        client_id=cfg.google_client_id,
        client_secret=cfg.google_client_secret,
        server_metadata_url=GOOGLE_METADATA,
    )
    oauth.register("google", client_kwargs={"scope": "openid email profile"}, **common)
    oauth.register("google_gmail", client_kwargs={"scope": f"openid email {GMAIL_SCOPE}"}, **common)


def external_url(endpoint: str) -> str:
    base = current_app.config["APP"].base_url
    if base:
        return base + url_for(endpoint)
    return url_for(endpoint, _external=True)


def sync_allowed_users(sess, allowed_users) -> None:
    """環境変数 ALLOWED_USERS の内容をusersテーブルに反映する。"""
    today = date.today()
    for order, au in enumerate(allowed_users):
        user = sess.scalar(select(User).where(User.email == au.email))
        if user is None:
            user = User(email=au.email, display_name=au.display_name)
            sess.add(user)
            sess.flush()
        user.display_name = au.display_name
        user.is_admin = au.is_admin
        user.sort_order = order
        if user.card_setting is None:
            sess.add(
                CardSetting(
                    user_id=user.id,
                    period_start=date(today.year, 1, 1),
                    period_end=date(today.year, 12, 31),
                )
            )
        if user.gmail_connection is None:
            sess.add(GmailConnection(user_id=user.id))
    sess.commit()


def allowed_emails() -> set[str]:
    return {u.email for u in current_app.config["APP"].allowed_users}


def load_current_user() -> None:
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    user = db_session.get(User, user_id)
    # 環境変数から外されたアカウントは、ログイン中でも次のアクセスで締め出す
    if user is None or user.email not in allowed_emails():
        session.clear()
        return
    g.user = user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        if not g.user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _start_session(user: User) -> None:
    session.clear()  # ログイン前のセッションを引き継がない
    session["user_id"] = user.id
    session.permanent = True


@bp.get("/login")
def login():
    if g.user:
        return redirect(url_for("main.home"))
    cfg = current_app.config["APP"]
    dev_users = []
    if cfg.dev_mode:
        dev_users = db_session.scalars(select(User).order_by(User.sort_order)).all()
    return render_template("login.html", dev_users=dev_users, google_ready=bool(cfg.google_client_id))


@bp.post("/login/google")
def login_google():
    if not current_app.config["APP"].google_client_id:
        abort(404)
    return oauth.google.authorize_redirect(external_url("auth.callback"), prompt="select_account")


@bp.get("/auth/callback")
def callback():
    try:
        token = oauth.google.authorize_access_token()
    except Exception:  # noqa: BLE001
        flash("ログインに失敗しました。もう一度お試しください。", "error")
        return redirect(url_for("auth.login"))

    info = token.get("userinfo") or {}
    sub, email = info.get("sub"), (info.get("email") or "").lower()
    if not sub or not email or not info.get("email_verified"):
        flash("Googleアカウントの情報を確認できませんでした。", "error")
        return redirect(url_for("auth.login"))

    user = db_session.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        # 初回ログイン: 登録済みメールアドレスと一致すれば、そのアカウントIDを結び付ける
        user = db_session.scalar(select(User).where(User.email == email, User.google_sub.is_(None)))
        if user is not None:
            user.google_sub = sub
            db_session.commit()

    if user is None or user.email not in allowed_emails():
        flash("このGoogleアカウントは利用登録されていません。", "error")
        return redirect(url_for("auth.login"))

    _start_session(user)
    return redirect(url_for("main.home"))


@bp.post("/login/dev")
def login_dev():
    if not current_app.config["APP"].dev_mode:
        abort(404)
    user = db_session.get(User, request.form.get("user_id", type=int))
    if user is None:
        abort(400)
    _start_session(user)
    return redirect(url_for("main.home"))


@bp.post("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。", "info")
    return redirect(url_for("auth.login"))
