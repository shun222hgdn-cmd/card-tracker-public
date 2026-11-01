from datetime import datetime

import pytest

from cardtracker.csv_import import CsvFormatError, decode, parse_csv
from cardtracker.db import db_session
from cardtracker.models import EXCLUDED, Transaction, User
from cardtracker.summary import compute_summary

from .conftest import SAMPLE_CSV, SAMPLE_TOTAL


def test_parse_rows_and_card_label():
    rows = parse_csv(SAMPLE_CSV)
    assert [(m, a) for _, m, a, _ in rows][:2] == [("セブン-イレブン", 648), ("AMAZON.CO.JP", 12800)]
    assert rows[0][0] == datetime(2026, 9, 1)
    assert rows[0][3] == "三井住友カード(NL)"
    assert rows[3][2] == -1990
    assert len(rows) == 5  # 見出し行と合計行は読まない


# 公開されている実際のVpass明細CSVの例(vpass-csv-parser のREADMEより。名前は伏せ字)
REAL_FORMAT_CSV = (
    "〇〇　〇〇　様,4980-11**-****-****,三井住友カードプラチナプリファード\r\n"
    "2023/04/13,ＥＴＣ首都高,390,,,,自神田橋外　　至台場　　　　軽・二\r\n"
    "2023/04/30,GITHUB, INC. (HTTPSGITHUB.C),1394,,,,10.00　USD　139.488　05 01\r\n"
    "〇〇　〇〇　様,6900-11**-****-****,ＡｐｐｌｅＰａｙ／ｉＤ\r\n"
    "2023/04/27,飲料自販機／ｉＤ,120,,,,\r\n"
)


def test_real_format_comma_in_merchant_and_multiple_cards():
    rows = parse_csv(REAL_FORMAT_CSV)
    assert [(m, a, c) for _, m, a, c in rows] == [
        ("ETC首都高", 390, "三井住友カードプラチナプリファード"),
        ("GITHUB, INC. (HTTPSGITHUB.C)", 1394, "三井住友カードプラチナプリファード"),  # カンマ入りでも落ちない
        ("飲料自販機/iD", 120, "ApplePay/iD"),  # 2つ目の見出し行のカード名が付く
    ]


def test_decode_shift_jis_and_utf8():
    assert decode(SAMPLE_CSV.encode("cp932")) == decode(SAMPLE_CSV.encode("utf-8-sig"))


def test_not_a_statement():
    with pytest.raises(CsvFormatError):
        parse_csv("name,email\nfoo,bar\n")


def test_upload_counts_and_dedup(client_for):
    mom = client_for(3)
    assert mom.upload().status_code == 302
    rows = db_session.query(Transaction).filter_by(user_id=3).all()
    assert len(rows) == 5  # 同じ日の同額セブンも別の取引として残る
    suica = next(t for t in rows if "Suica" in t.merchant)
    assert suica.final_status == EXCLUDED
    assert compute_summary(db_session, db_session.get(User, 3)).total == SAMPLE_TOTAL

    mom.upload(encoding="utf-8-sig")  # 同じ明細をもう一度
    db_session.remove()
    assert db_session.query(Transaction).filter_by(user_id=3).count() == 5
    assert all(t.source == "csv" for t in db_session.query(Transaction).filter_by(user_id=3))


def test_upload_rejects_bad_file(client_for):
    dad = client_for(2)
    resp = dad.upload("hello,world\n", encoding="utf-8")
    assert resp.status_code == 302 and resp.location.endswith("/import")
    assert db_session.query(Transaction).filter_by(user_id=2).count() == 0
