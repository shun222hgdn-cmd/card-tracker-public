from decimal import Decimal

from cardtracker.classifier import Rule, apply_classification, classify
from cardtracker.models import EXCLUDED, REVIEW, TARGET, Transaction


def tx(**kw):
    base = dict(source="csv", merchant="ローソン", original_amount=Decimal("1000"),
                card_label="三井住友カード(NL)", manual_override=False)
    base.update(kw)
    return Transaction(**base)


RULES = [Rule("Suica"), Rule("年会費")]


def test_plain_purchase_is_target():
    assert classify(tx(), RULES, None) == (TARGET, "")


def test_rule_matches_ignoring_width_and_case():
    status, reason = classify(tx(merchant="モバイルＳＵＩＣＡ"), RULES, None)
    assert status == EXCLUDED and "Suica" in reason


def test_card_label_filter():
    assert classify(tx(card_label="Sample/クレジット"), RULES, "NL") == (EXCLUDED, "対象カード以外")
    assert classify(tx(card_label="三井住友カード(NL)"), RULES, "ＮＬ")[0] == TARGET
    assert classify(tx(card_label=None), RULES, "NL")[0] == REVIEW
    # 手動登録はカード名を持たないので、カード名の条件では判定しない
    assert classify(tx(source="manual", card_label=None), RULES, "NL")[0] == TARGET


def test_manual_override_keeps_final_status():
    t = tx(merchant="モバイルSuica", manual_override=True, final_status=TARGET)
    apply_classification(t, RULES, None)
    assert t.auto_status == EXCLUDED
    assert t.final_status == TARGET
