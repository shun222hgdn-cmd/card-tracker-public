from datetime import datetime
from decimal import Decimal

from cardtracker.parser import parse_message, parse_text

# 通知メールの形式に合わせた架空の本文
REAL_SAMPLE = """


〇〇〇 様

いつも三井住友カードをご利用いただきありがとうございます。
Ｓａｍｐｌｅ／クレジットについてカードの利用内容をお知らせします。

ご利用内容


ご利用日時：2026/01/15 12:00
SAMPLESHOPSEOUL（買物）	5,000.00 KRW




本メールはカードご利用の承認照会に基づく通知であり、カードのご利用及びご請求を確定するものではございません。
ご利用情報が明細に反映するまでにはお日にちがかかる場合がございます。
※	携帯電話や公共料金などの継続的なご利用（注） 及び ETCやPiTaPa等、一部の電子マネー利用については通知されません。
Copyright (C) Sumitomo Mitsui Card Co., Ltd.
"""

SUBJECT = "ご利用のお知らせ【三井住友カード】"


def test_real_sample_foreign_currency():
    r = parse_text(SUBJECT, REAL_SAMPLE)
    assert r.ok
    assert r.card_label == "Sample/クレジット"
    [e] = r.entries
    assert e.used_at == datetime(2026, 1, 15, 12, 0)
    assert e.merchant == "SAMPLESHOPSEOUL"
    assert e.usage_type == "買物"
    assert e.amount == Decimal("5000.00")
    assert e.currency == "KRW"
    assert not e.is_cancel


def test_yen_amount_and_fullwidth():
    body = REAL_SAMPLE.replace("SAMPLESHOPSEOUL（買物）\t5,000.00 KRW", "セブン－イレブン（買物）　１，２３４円")
    [e] = parse_text(SUBJECT, body).entries
    assert e.ok
    assert e.currency == "JPY"
    assert e.amount == Decimal("1234")
    assert e.merchant == "セブン-イレブン"


def test_merchant_with_parentheses():
    body = REAL_SAMPLE.replace("SAMPLESHOPSEOUL（買物）\t5,000.00 KRW", "ABC(TOKYO)（買物）\t500円")
    [e] = parse_text(SUBJECT, body).entries
    assert e.merchant == "ABC(TOKYO)"
    assert e.usage_type == "買物"


def test_html_body_with_table_cells():
    html = """<html><head><style>p{}</style></head><body>
    <p>三井住友カード（ＮＬ）についてカードの利用内容をお知らせします。</p>
    <table><tr><td>ご利用日時：2026/10/01 09:05</td></tr>
    <tr><td>ローソン（買物）</td><td>1,230円</td></tr></table></body></html>"""
    r = parse_message(SUBJECT, [("text/html", html)])
    assert r.ok
    assert r.card_label == "三井住友カード(NL)"
    [e] = r.entries
    assert (e.merchant, e.amount, e.currency) == ("ローソン", Decimal("1230"), "JPY")


def test_cancel_detected_from_subject_or_usage():
    body = REAL_SAMPLE.replace("SAMPLESHOPSEOUL（買物）\t5,000.00 KRW", "ヨドバシカメラ（取消）\t54,780円")
    [e] = parse_text("ご利用取消のお知らせ【三井住友カード】", body).entries
    assert e.is_cancel
    assert e.amount == Decimal("54780")


def test_negative_amount_is_cancel():
    body = REAL_SAMPLE.replace("SAMPLESHOPSEOUL（買物）\t5,000.00 KRW", "ユニクロ（買物）\t－7,990円")
    [e] = parse_text(SUBJECT, body).entries
    assert e.is_cancel and e.amount == Decimal("7990")


def test_unknown_format_is_not_guessed():
    r = parse_text(SUBJECT, "ご利用内容\nなにか別の形式\n")
    assert not r.ok
    assert r.entries[0].amount is None
    assert r.entries[0].error


def test_date_without_amount_line_fails():
    r = parse_text(SUBJECT, "ご利用日時：2026/01/15 12:00\n利用先は後日お知らせします\n")
    assert not r.ok
    assert r.entries[0].used_at == datetime(2026, 1, 15, 12, 0)


def test_multiple_transactions_in_one_mail():
    body = "ご利用日時：2026/10/01 10:00\nA店（買物）\t100円\nご利用日時：2026/10/01 11:00\nB店（買物）\t200円\n"
    r = parse_text(SUBJECT, body)
    assert [e.merchant for e in r.entries] == ["A店", "B店"]
    assert [e.seq for e in r.entries] == [0, 1]
