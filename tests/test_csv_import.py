from datetime import datetime

import pytest

from cardtracker.csv_import import CsvFormatError, decode, parse_csv
from cardtracker.db import db_session
from cardtracker.models import EXCLUDED, TARGET, Transaction, User
from cardtracker.summary import compute_summary

SAMPLE = (
    "〇〇 様,4980-00**-****-****,三井住友カード（ＮＬ）\r\n"
    "2026/09/01,セブン－イレブン,648,１,１,648,\r\n"
    "2026/09/03,ＡＭＡＺＯＮ．ＣＯ．ＪＰ,\"12,800\",１,１,12800,\r\n"
    "2026/09/05,モバイルＳｕｉｃａ,5000,１,１,5000,\r\n"
    "2026/09/10,ユニクロ,-1990,１,１,-1990,返品\r\n"
    "2026/09/12,セブン－イレブン,648,１,１,648,\r\n"
    ",,,,,17106,\r\n"
)


def upload(cl, text, encoding="cp932", name="meisai.csv"):
    from io import BytesIO

    return cl.c.post(
        "/import",
        data={"_csrf": "test-csrf-token", "csv_file": (BytesIO(text.encode(encoding)), name)},
        content_type="multipart/form-data",
    )


def test_parse_rows_and_card_label():
    r = parse_csv(SAMPLE)
    assert r.card_label == "三井住友カード(NL)"
    assert [(x.merchant, x.amount) for x in r.rows][:2] == [("セブン-イレブン", 648), ("AMAZON.CO.JP", 12800)]
    assert r.rows[0].used_at == datetime(2026, 9, 1)
    assert r.rows[3].amount == -1990
    assert r.skipped == 1  # 合計行


def test_decode_shift_jis_and_utf8():
    assert decode(SAMPLE.encode("cp932")) == decode(SAMPLE.encode("utf-8-sig"))


def test_not_a_statement():
    with pytest.raises(CsvFormatError):
        parse_csv("name,email\nfoo,bar\n")


def test_upload_counts_and_dedup(client_for):
    mom = client_for(3)
    resp = upload(mom, SAMPLE)
    assert resp.status_code == 302
    rows = db_session.query(Transaction).filter_by(user_id=3).all()
    assert len(rows) == 5  # 同じ日の同額セブンも別の取引として残る
    suica = next(t for t in rows if "Suica" in t.merchant)
    assert suica.final_status == EXCLUDED
    # 648 + 12800 - 1990 + 648 (Suicaは対象外)
    assert compute_summary(db_session, db_session.get(User, 3)).total == 12106

    upload(mom, SAMPLE, encoding="utf-8-sig")  # 同じ明細をもう一度
    db_session.remove()
    assert db_session.query(Transaction).filter_by(user_id=3).count() == 5
    assert all(t.source == "csv" for t in db_session.query(Transaction).filter_by(user_id=3))


def test_upload_rejects_bad_file(client_for):
    dad = client_for(2)
    resp = upload(dad, "hello,world\n", encoding="utf-8")
    assert resp.status_code == 302 and resp.location.endswith("/import")
    assert db_session.query(Transaction).filter_by(user_id=2).count() == 0


def test_csv_transactions_are_private(client_for):
    mom, dad = client_for(3), client_for(2)
    upload(mom, SAMPLE)
    tx = db_session.query(Transaction).filter_by(user_id=3).first()
    assert dad.get(f"/transactions/{tx.id}").status_code == 404
    assert "セブン" not in dad.get("/family").get_data(as_text=True)
    assert tx.final_status in (TARGET, EXCLUDED)
