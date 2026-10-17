"""受け入れテスト(要件 10)のうち、自動で確かめられるもの。"""
from datetime import date, timedelta

from sqlalchemy import func, select

from cardtracker.db import db_session
from cardtracker.models import EXCLUDED, REVIEW, TARGET, Transaction, User
from cardtracker.summary import compute_summary


def connect_and_sync(cl):
    cl.post("/gmail/connect")
    return cl.get("/")  # 開いた時点で自動同期される


def tx_count(user_id):
    return db_session.scalar(select(func.count()).select_from(Transaction).where(Transaction.user_id == user_id))


def find_tx(user_id, merchant):
    return db_session.scalar(select(Transaction).where(Transaction.user_id == user_id, Transaction.merchant == merchant))


def test_login_required(app):
    resp = app.test_client().get("/")
    assert resp.status_code == 302 and "/login" in resp.location


def test_csrf_required(client_for):
    cl = client_for(1)
    resp = cl.c.post("/sync", data={})
    assert resp.status_code == 400


def test_sync_parses_and_classifies(client_for):
    cl = client_for(1)
    resp = connect_and_sync(cl)
    assert resp.status_code == 200
    assert tx_count(1) == 11  # 通知10通 + 解析できない1通

    assert find_tx(1, "モバイルSuica").final_status == EXCLUDED          # 対象外ルール
    assert find_tx(1, "SAMPLESHOPSEOUL").final_status == REVIEW       # 外貨
    assert find_tx(1, "ローソン").final_status == TARGET
    yodobashi = db_session.scalars(select(Transaction).where(
        Transaction.user_id == 1, Transaction.merchant == "ヨドバシカメラ")).all()
    assert all(t.final_status == EXCLUDED for t in yodobashi)            # 取消と元取引
    broken = db_session.scalar(select(Transaction).where(Transaction.user_id == 1, Transaction.parse_failed.is_(True)))
    assert broken.final_status == REVIEW and broken.original_amount is None

    s = compute_summary(db_session, db_session.get(User, 1))
    # 12,800 + 648 + 7,990 + 14,170 + 1,230 + 690
    assert s.total == 37528
    assert s.remaining == 1_000_000 - 37528
    assert s.review_count == 2


def test_resync_does_not_double_count(client_for):
    cl = client_for(1)
    connect_and_sync(cl)
    before = compute_summary(db_session, db_session.get(User, 1)).total
    cl.post("/sync")
    cl.post("/sync")
    assert tx_count(1) == 11
    assert compute_summary(db_session, db_session.get(User, 1)).total == before


def test_auto_sync_skipped_within_interval(client_for):
    cl = client_for(1)
    connect_and_sync(cl)
    first = db_session.get(User, 1).gmail_connection.last_attempt_at
    db_session.remove()
    cl.get("/")
    assert db_session.get(User, 1).gmail_connection.last_attempt_at == first


def test_manual_change_survives_resync_and_rule_change(client_for):
    cl = client_for(1)
    connect_and_sync(cl)
    foreign = find_tx(1, "SAMPLESHOPSEOUL")
    resp = cl.post(f"/transactions/{foreign.id}", final_status="target", corrected_amount="１，０２０")
    assert resp.status_code == 302
    cl.post("/sync")
    cl.post("/rules", keyword="SAMPLESHOP", applies_to="merchant")  # 本人(id=1)は管理者
    db_session.remove()
    t = db_session.get(Transaction, foreign.id)
    assert (t.final_status, t.corrected_amount, t.manual_override) == (TARGET, 1020, True)
    assert t.auto_status == EXCLUDED


def test_target_without_yen_amount_is_rejected(client_for):
    cl = client_for(1)
    connect_and_sync(cl)
    foreign = find_tx(1, "SAMPLESHOPSEOUL")
    cl.post(f"/transactions/{foreign.id}", final_status="target", corrected_amount="")
    db_session.remove()
    assert db_session.get(Transaction, foreign.id).final_status == REVIEW


def test_cannot_view_or_edit_other_users_transactions(client_for):
    me, dad = client_for(1), client_for(2)
    connect_and_sync(me)
    my_tx = find_tx(1, "ローソン")
    assert dad.get(f"/transactions/{my_tx.id}").status_code == 404
    assert dad.post(f"/transactions/{my_tx.id}", final_status="excluded").status_code == 404
    assert dad.post(f"/transactions/{my_tx.id}/reset").status_code == 404
    assert "ローソン" not in dad.get("/history").get_data(as_text=True)
    db_session.remove()
    assert db_session.get(Transaction, my_tx.id).final_status == TARGET


def test_family_page_shows_only_totals(client_for):
    me, mom = client_for(1), client_for(3)
    connect_and_sync(me)
    html = mom.get("/family").get_data(as_text=True)
    assert "37,528円" in html
    for secret in ("ローソン", "ユニクロ", "AMAZON", "12,800"):
        assert secret not in html


def test_out_of_period_not_counted(client_for):
    cl = client_for(1)
    connect_and_sync(cl)
    # 直近7日だけを期間にする → ローソン(5日前)とスターバックス(2日前)だけが残る
    today = date.today()
    cl.post("/settings/period", period_start=(today - timedelta(days=7)).isoformat(), period_end=today.isoformat())
    assert compute_summary(db_session, db_session.get(User, 1)).total == 1230 + 690


def test_card_label_filter_reclassifies(client_for):
    cl = client_for(1)
    connect_and_sync(cl)
    today = date.today()
    cl.post("/settings/period", period_start=date(today.year - 1, 1, 1).isoformat(),
            period_end=(date(today.year - 1, 1, 1) + timedelta(days=399)).isoformat(), card_label="Gold")
    db_session.remove()
    assert find_tx(1, "ローソン").final_status == EXCLUDED


def test_non_admin_cannot_change_rules(client_for):
    dad = client_for(2)
    assert dad.post("/rules", keyword="X", applies_to="merchant").status_code == 403


def test_manual_registration(client_for):
    cl = client_for(2)
    cl.post("/transactions/new", date="2026-10-01", time="12:00", merchant="近所の店", amount="3,000")
    t = find_tx(2, "近所の店")
    assert (t.source, t.final_status, t.effective_amount) == ("manual", TARGET, 3000)


def test_removed_account_is_logged_out(app, client_for):
    cl = client_for(3)
    app.config["APP"].allowed_users = [u for u in app.config["APP"].allowed_users if u.email != "mother@example.com"]
    resp = cl.get("/")
    assert resp.status_code == 302 and "/login" in resp.location


def test_disconnect_keeps_data(client_for):
    cl = client_for(1)
    connect_and_sync(cl)
    cl.post("/gmail/disconnect")
    assert tx_count(1) == 11
    assert db_session.get(User, 1).gmail_connection.status == "disconnected"


def test_privacy_page_is_public(app):
    resp = app.test_client().get("/privacy")
    assert resp.status_code == 200
    assert "プライバシーポリシー" in resp.get_data(as_text=True)
