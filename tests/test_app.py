"""受け入れテスト(要件 10)のうち、自動で確かめられるもの。"""
from datetime import date

from sqlalchemy import select

from cardtracker.db import db_session
from cardtracker.models import EXCLUDED, REVIEW, TARGET, Transaction, User
from cardtracker.summary import compute_summary

from .conftest import SAMPLE_TOTAL


def find_tx(user_id, merchant):
    return db_session.scalar(
        select(Transaction).where(Transaction.user_id == user_id, Transaction.merchant == merchant).limit(1)
    )


def total(user_id):
    db_session.remove()
    return compute_summary(db_session, db_session.get(User, user_id)).total


def set_period(cl, start, end, card_label=""):
    return cl.post("/settings/period", period_start=start.isoformat(), period_end=end.isoformat(),
                   card_label=card_label)


def test_login_required(app):
    resp = app.test_client().get("/")
    assert resp.status_code == 302 and "/login" in resp.location


def test_csrf_required(client_for):
    cl = client_for(1)
    assert cl.c.post("/transactions/new", data={}).status_code == 400


def test_home_shows_total(client_for):
    cl = client_for(1)
    set_period(cl, date(2026, 1, 1), date(2026, 12, 31))
    cl.upload()
    html = cl.get("/").get_data(as_text=True)
    assert f"{SAMPLE_TOTAL:,}円" in html


def test_manual_change_survives_reimport_and_rule_change(client_for):
    cl = client_for(1)
    set_period(cl, date(2026, 1, 1), date(2026, 12, 31))
    cl.upload()
    uniqlo = find_tx(1, "ユニクロ")
    resp = cl.post(f"/transactions/{uniqlo.id}", final_status="excluded", corrected_amount="")
    assert resp.status_code == 302
    cl.upload()
    cl.post("/rules", keyword="ユニクロ")  # 本人(id=1)は管理者
    db_session.remove()
    t = db_session.get(Transaction, uniqlo.id)
    assert (t.final_status, t.manual_override) == (EXCLUDED, True)
    # 返品(-1,990円)を対象外にしたので、合計はその分増える
    assert total(1) == SAMPLE_TOTAL + 1990


def test_corrected_amount_is_used(client_for):
    cl = client_for(1)
    set_period(cl, date(2026, 1, 1), date(2026, 12, 31))
    cl.upload()
    amazon = find_tx(1, "AMAZON.CO.JP")
    cl.post(f"/transactions/{amazon.id}", final_status="target", corrected_amount="１０，０００")
    assert total(1) == SAMPLE_TOTAL - 12800 + 10000


def test_review_is_not_counted(client_for):
    cl = client_for(1)
    set_period(cl, date(2026, 1, 1), date(2026, 12, 31))
    cl.upload()
    amazon = find_tx(1, "AMAZON.CO.JP")
    cl.post(f"/transactions/{amazon.id}", final_status="review", corrected_amount="")
    assert total(1) == SAMPLE_TOTAL - 12800
    assert db_session.get(Transaction, amazon.id).final_status == REVIEW


def test_cannot_view_or_edit_other_users_transactions(client_for):
    me, dad = client_for(1), client_for(2)
    me.upload()
    my_tx = find_tx(1, "AMAZON.CO.JP")
    assert dad.get(f"/transactions/{my_tx.id}").status_code == 404
    assert dad.post(f"/transactions/{my_tx.id}", final_status="excluded").status_code == 404
    assert dad.post(f"/transactions/{my_tx.id}/reset").status_code == 404
    assert "AMAZON" not in dad.get("/history").get_data(as_text=True)
    db_session.remove()
    assert db_session.get(Transaction, my_tx.id).final_status == TARGET


def test_family_page_shows_only_totals(client_for):
    me, mom = client_for(1), client_for(3)
    set_period(me, date(2026, 1, 1), date(2026, 12, 31))
    me.upload()
    html = mom.get("/family").get_data(as_text=True)
    assert f"{SAMPLE_TOTAL:,}円" in html
    for secret in ("セブン", "ユニクロ", "AMAZON", "12,800"):
        assert secret not in html


def test_out_of_period_not_counted(client_for):
    cl = client_for(1)
    cl.upload()
    # 9/5〜9/30だけを期間にする → ユニクロ(-1,990)とセブン(648)だけが残る(Suicaは対象外)
    set_period(cl, date(2026, 9, 5), date(2026, 9, 30))
    assert total(1) == -1990 + 648


def test_card_label_filter_reclassifies(client_for):
    cl = client_for(1)
    cl.upload()
    set_period(cl, date(2026, 1, 1), date(2026, 12, 31), card_label="Gold")
    db_session.remove()
    assert find_tx(1, "AMAZON.CO.JP").final_status == EXCLUDED


def test_non_admin_cannot_change_rules(client_for):
    dad = client_for(2)
    assert dad.post("/rules", keyword="X").status_code == 403


def test_manual_registration(client_for):
    cl = client_for(2)
    cl.post("/transactions/new", date="2026-10-01", time="12:00", merchant="近所の店", amount="3,000")
    t = find_tx(2, "近所の店")
    assert (t.source, t.final_status, t.effective_amount) == ("manual", TARGET, 3000)


def test_csv_rows_cannot_be_deleted(client_for):
    cl = client_for(1)
    cl.upload()
    assert cl.post(f"/transactions/{find_tx(1, 'AMAZON.CO.JP').id}/delete").status_code == 400


def test_removed_account_is_logged_out(app, client_for):
    cl = client_for(3)
    app.config["APP"].allowed_users = [u for u in app.config["APP"].allowed_users if u.email != "mother@example.com"]
    resp = cl.get("/")
    assert resp.status_code == 302 and "/login" in resp.location


def test_privacy_page_is_public(app):
    resp = app.test_client().get("/privacy")
    assert resp.status_code == 200
    assert "プライバシーポリシー" in resp.get_data(as_text=True)
