"""三井住友カード「ご利用のお知らせ」メールの解析。

想定しているメール本文(抜粋):

    Ｓａｍｐｌｅ／クレジットについてカードの利用内容をお知らせします。
    ご利用日時：2026/01/15 12:00
    SAMPLESHOPSEOUL（買物）	5,000.00 KRW

方針:
- 解析前に NFKC 正規化して、全角・半角の揺れ(Ｓａｍｐｌｅ / ： / （） など)をそろえる
- 必要な項目が取れないものは金額を推測せず「解析失敗」として返す(要件 4-6)
- メール本文全体は保存しない。ここで抜き出した項目だけを呼び出し元に返す(要件 6-1)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

SENDER = "statement@vpass.ne.jp"
SUBJECT_KEYWORDS = ("ご利用のお知らせ", "取消")

DATE_RE = re.compile(r"ご利用日時\s*:\s*(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})")
LINE_RE = re.compile(
    r"^(?P<merchant>\S.*?)"
    r"(?:\s*\((?P<usage>[^()]*)\))?"
    r"\s*(?P<amount>-?\d[\d,]*(?:\.\d+)?)\s*(?P<currency>円|[A-Z]{3})\s*$"
)
CARD_RE = re.compile(r"([^\n]+?)について(?:カードの)?(?:ご)?利用内容をお知らせ")
# 日時の後ろ、この行数以内に「利用先 金額」が無ければ解析失敗とみなす
LOOKAHEAD_LINES = 6


@dataclass
class ParsedEntry:
    seq: int
    used_at: datetime | None = None
    merchant: str | None = None
    usage_type: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    is_cancel: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ParseResult:
    card_label: str | None
    entries: list[ParsedEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.entries) and all(e.ok for e in self.entries)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    # 「-」に似た記号を半角に寄せる(取消時のマイナス表記対策)
    text = re.sub(r"[−‐-―－]", "-", text)
    return text


class _HTMLToText(HTMLParser):
    BLOCK_TAGS = {"br", "p", "div", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
    CELL_TAGS = {"td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in self.CELL_TAGS:
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _HTMLToText()
    p.feed(html)
    p.close()
    return "".join(p.parts)


def _parse_amount(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _match_line(lines: list[str]) -> re.Match | None:
    """日時の後ろの行から「利用先(区分) 金額 通貨」を探す。HTMLの表で行が割れる場合に備え2行連結も試す。"""
    for i, line in enumerate(lines):
        m = LINE_RE.match(line)
        if m:
            return m
        if i + 1 < len(lines):
            m = LINE_RE.match(f"{line} {lines[i + 1]}")
            if m:
                return m
    return None


def parse_text(subject: str, body: str) -> ParseResult:
    subject_n = normalize(subject or "")
    text = normalize(body or "")

    card_match = CARD_RE.search(text)
    card_label = card_match.group(1).strip() if card_match else None

    date_matches = list(DATE_RE.finditer(text))
    if not date_matches:
        return ParseResult(card_label, [ParsedEntry(seq=0, error="ご利用日時が見つかりません")])

    entries = []
    for seq, dm in enumerate(date_matches):
        entry = ParsedEntry(seq=seq)
        try:
            y, mo, d, h, mi = (int(x) for x in dm.groups())
            entry.used_at = datetime(y, mo, d, h, mi)
        except ValueError:
            entry.error = "ご利用日時を読み取れません"
            entries.append(entry)
            continue

        block_end = date_matches[seq + 1].start() if seq + 1 < len(date_matches) else len(text)
        following = [ln.strip() for ln in text[dm.end():block_end].split("\n")]
        following = [ln for ln in following if ln][:LOOKAHEAD_LINES]

        m = _match_line(following)
        amount = _parse_amount(m.group("amount")) if m else None
        if not m or amount is None:
            entry.error = "利用先・金額を読み取れません"
            entries.append(entry)
            continue

        entry.merchant = re.sub(r"\s+", " ", m.group("merchant")).strip()
        entry.usage_type = (m.group("usage") or "").strip() or None
        currency = m.group("currency")
        entry.currency = "JPY" if currency in ("円", "JPY") else currency
        entry.is_cancel = (
            "取消" in subject_n
            or "取消" in (entry.usage_type or "")
            or amount < 0
        )
        entry.amount = abs(amount)
        entries.append(entry)

    return ParseResult(card_label, entries)


def parse_message(subject: str, bodies: list[tuple[str, str]]) -> ParseResult:
    """Gmailから取り出した本文候補 [(mime_type, text), ...] を順に試す。プレーンテキストを優先する。"""
    ordered = sorted(bodies, key=lambda b: 0 if b[0] == "text/plain" else 1)
    best: ParseResult | None = None
    for mime, content in ordered:
        text = html_to_text(content) if mime == "text/html" else content
        result = parse_text(subject, text)
        if result.ok:
            return result
        if best is None or sum(e.ok for e in result.entries) > sum(e.ok for e in best.entries):
            best = result
    return best or ParseResult(None, [ParsedEntry(seq=0, error="メール本文がありません")])
