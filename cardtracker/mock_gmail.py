"""開発用のダミーGmail。

通知メールと同じ構成の本文を生成する。
Googleの認証情報がなくても、同期・解析・判定・画面の動きを確認できる。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .gmail_client import GmailMessage

TEMPLATE = """〇〇〇 様

いつも三井住友カードをご利用いただきありがとうございます。
{card}についてカードの利用内容をお知らせします。

ご利用内容


ご利用日時：{used_at}
{merchant}（{usage}）\t{amount}




本メールはカードご利用の承認照会に基づく通知であり、カードのご利用及びご請求を確定するものではございません。
ご利用情報が明細に反映するまでにはお日にちがかかる場合がございます。

※\t送信元「ｓｔａｔｅｍｅｎｔ＠ｖｐａｓｓ．ｎｅ．ｊｐ」は送信専用です。
Copyright (C) Sumitomo Mitsui Card Co., Ltd.
"""

CARD = "三井住友カード（ＮＬ）"

# (何日前, 利用先, 区分, 金額表記, 件名)
BASE_ITEMS = [
    (40, "ＡＭＡＺＯＮ．ＣＯ．ＪＰ", "買物", "12,800円", None),
    (35, "セブン－イレブン", "買物", "648円", None),
    (30, "モバイルＳｕｉｃａ", "買物", "5,000円", None),
    (25, "ユニクロ", "買物", "7,990円", None),
    (20, "ＪＲ東日本", "買物", "14,170円", None),
    (14, "SAMPLESHOPSEOUL", "買物", "5,000.00 KRW", None),
    (10, "ヨドバシカメラ", "買物", "54,780円", None),
    (9, "ヨドバシカメラ", "取消", "54,780円", "ご利用取消のお知らせ【三井住友カード】"),
    (5, "ローソン", "買物", "1,230円", None),
    (2, "スターバックス", "買物", "690円", None),
]


def _render(card: str, used_at: datetime, merchant: str, usage: str, amount: str) -> str:
    return TEMPLATE.format(
        card=card, used_at=used_at.strftime("%Y/%m/%d %H:%M"), merchant=merchant, usage=usage, amount=amount
    )


class MockGmailClient:
    def __init__(self, user_id: int, today: date | None = None):
        self.user_id = user_id
        self.today = today or date.today()
        self.messages: dict[str, GmailMessage] = {}
        scale = {1: 1, 2: 4, 3: 2}.get(user_id, 1)  # 家族ごとに金額を変えて見分けやすくする
        for i, (days, merchant, usage, amount, subject) in enumerate(BASE_ITEMS):
            used_at = datetime.combine(self.today - timedelta(days=days), datetime.min.time()) + timedelta(
                hours=12, minutes=i
            )
            if amount.endswith("円") and scale > 1:
                amount = f"{int(amount[:-1].replace(',', '')) * scale:,}円"
            body = _render(CARD, used_at, merchant, usage, amount)
            self.messages[f"mock-{user_id}-{i:03d}"] = GmailMessage(
                id=f"mock-{user_id}-{i:03d}",
                subject=subject or "ご利用のお知らせ【三井住友カード】",
                bodies=[("text/plain", body)],
            )
        # 形式が想定外のメール(解析失敗の表示確認用)
        self.messages[f"mock-{user_id}-broken"] = GmailMessage(
            id=f"mock-{user_id}-broken",
            subject="ご利用のお知らせ【三井住友カード】",
            bodies=[("text/plain", "ご利用内容\n(本文の形式が変わった想定)\n")],
        )

    def list_message_ids(self, start: date, end: date) -> list[str]:
        return list(reversed(self.messages))  # Gmailと同じく新しい順

    def get_message(self, message_id: str) -> GmailMessage:
        return self.messages[message_id]
