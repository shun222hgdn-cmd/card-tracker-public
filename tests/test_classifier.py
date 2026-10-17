from decimal import Decimal

from cardtracker.classifier import Rule, apply_classification, classify
from cardtracker.models import EXCLUDED, REVIEW, TARGET, Transaction


def tx(**kw):
    base = dict(source="gmail", merchant="ローソン", usage_type="買物", original_amount=Decimal("1000"),
                currency="JPY", card_label="三井住友カード(NL)", is_cancel=False, cancelled=False,
                parse_failed=False, duplicate_suspect=False, manual_override=False)
    base.update(kw)
    return Transaction(**base)


RULES = [Rule("Suica", "merchant"), Rule("年会費", "all")]


def test_plain_yen_is_target():
    assert classify(tx(), RULES, None) == (TARGET, "")


def test_rule_matches_ignoring_width_and_case():
    status, reason = classify(tx(merchant="モバイルＳＵＩＣＡ"), RULES, None)
    assert status == EXCLUDED and "Suica" in reason


def test_rule_applies_to_usage_when_all():
    assert classify(tx(merchant="三井住友カード", usage_type="年会費"), RULES, None)[0] == EXCLUDED


def test_foreign_currency_needs_review():
    assert classify(tx(currency="KRW"), RULES, None)[0] == REVIEW


def test_parse_failed_needs_review():
    assert classify(tx(parse_failed=True, merchant=None), RULES, None)[0] == REVIEW


def test_cancel_and_cancelled_are_excluded():
    assert classify(tx(is_cancel=True), RULES, None)[0] == EXCLUDED
    assert classify(tx(cancelled=True), RULES, None)[0] == EXCLUDED


def test_card_label_filter():
    assert classify(tx(card_label="Sample/クレジット"), RULES, "NL") == (EXCLUDED, "対象カード以外")
    assert classify(tx(card_label="三井住友カード(NL)"), RULES, "ＮＬ")[0] == TARGET
    assert classify(tx(card_label=None), RULES, "NL")[0] == REVIEW
    # 手動登録はカード名を持たないので、カード名の条件では判定しない
    assert classify(tx(source="manual", card_label=None), RULES, "NL")[0] == TARGET


def test_duplicate_suspect_needs_review():
    assert classify(tx(duplicate_suspect=True), RULES, None)[0] == REVIEW


def test_manual_override_keeps_final_status():
    t = tx(merchant="モバイルSuica", manual_override=True, final_status=TARGET)
    apply_classification(t, RULES, None)
    assert t.auto_status == EXCLUDED
    assert t.final_status == TARGET
