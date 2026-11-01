"""Googleログインと、登録済みアカウント以外の拒否(要件 F-01, 2-1)。"""
from __future__ import annotations

from datetime import date
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select

from .db import db_session
from .models import CardSetting, User

bp = Blueprint("auth", __name__)
oauth = OAuth()


def init_oauth(app) -> None:
    cfg = app.config["APP"]
    oauth.init_app(app)
    if cfg.google_client_id:
        # ログインに必要な、メールアドレスとアカウントIDだけを求める(Gmailなどへのアクセスは求めない)
        oauth.register(
            "google",
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )


def sync_allowed_users(sess, allowed_users) -> None:
    """環境変数 ALLOWED_USERS の内容をusersテーブルに反映する。"""
    year = date.today().year
    for order, au in enumerate(allowed_users):
        user = sess.scalar(select(User).where(User.email == au.email))
        if user is None:
            user = User(email=au.email, display_name=au.display_name)
            sess.add(user)
            sess.flush()
        user.display_name, user.is_admin, user.sort_order = au.display_name, au.is_admin, order
        if user.card_setting is None:
            sess.add(CardSetting(user_id=user.id, period_start=date(year, 1, 1), period_end=date(year, 12, 31)))
    sess.commit()


def allowed_emails() -> set[str]:
    return {u.email for u in current_app.config["APP"].allowed_users}


def load_current_user() -> None:
    user_id = session.get("user_id")
    g.user = db_session.get(User, user_id) if user_id else None
    # 環境変数から外されたアカウントは、ログイン中でも次のアクセスで締め出す
    if g.user is not None and g.user.email not in allowed_emails():
        session.clear()
        g.user = None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _start_session(user: User):
    session.clear()  # ログイン前のセッションを引き継がない
    session["user_id"] = user.id
    session.permanent = True
    return redirect(url_for("main.home"))


@bp.get("/login")
def login():
    if g.user:
        return redirect(url_for("main.home"))
    cfg = current_app.config["APP"]
    dev_users = db_session.scalars(select(User).order_by(User.sort_order)).all() if cfg.dev_mode else []
    return render_template("login.html", dev_users=dev_users, google_ready=bool(cfg.google_client_id))


@bp.post("/login/google")
def login_google():
    cfg = current_app.config["APP"]
    if not cfg.google_client_id:
        abort(404)
    callback = cfg.base_url + url_for("auth.callback") if cfg.base_url else url_for("auth.callback", _external=True)
    return oauth.google.authorize_redirect(callback, prompt="select_account")


@bp.get("/auth/callback")
def callback():
    try:
        info = oauth.google.authorize_access_token().get("userinfo") or {}
    except Exception:  # noqa: BLE001
        info = {}
    sub, email = info.get("sub"), (info.get("email") or "").lower()
    if not sub or not email or not info.get("email_verified"):
        flash("ログインに失敗しました。もう一度お試しください。", "error")
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
    return _start_session(user)


@bp.post("/login/dev")
def login_dev():
    user = db_session.get(User, request.form.get("user_id", type=int) or 0)
    if not current_app.config["APP"].dev_mode or user is None:
        abort(404)
    return _start_session(user)


@bp.post("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。", "info")
    return redirect(url_for("auth.login"))


@bp.get("/privacy")
def privacy():
    # Googleの同意画面から参照されるため、ログインなしで見られるようにする
    return render_template("privacy.html")
