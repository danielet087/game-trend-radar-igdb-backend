"""The Hong Kong Nintendo eShop: a product's page and its current price.

Title IDs come only from Nintendo's own Taiwan and Hong Kong listings
(nintendo_official). Prices come from the eShop's price service
(https://api.ec.nintendo.com/v1/price, country HK, at most 50 IDs a request),
in Hong Kong dollars: the regular price, and a sale price with its end time.
No redirects or retries; a rate limit stops the run's requests.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

import requests

PRICE_URL = "https://api.ec.nintendo.com/v1/price"
SOURCE_NAME = "任天堂 eShop 香港"
CURRENCY = "HKD"
MAX_IDS = 50
MAX_REQUESTS = 6
TIMEOUT_SECONDS = 10
MAX_BYTES = 1_000_000
_NSUID = re.compile(r"7001\d{10}")
_AMOUNT = re.compile(r"(?:0|[1-9]\d{0,6})(?:\.\d{1,2})?")
SELLING = {"onsale", "preorder"}


def title_url(nsuid):
    """The product's page on the Hong Kong eShop."""
    return f"https://ec.nintendo.com/HK/zh/titles/{nsuid}" if isinstance(nsuid, str) and _NSUID.fullmatch(nsuid) else None


def _cents(value):
    if not isinstance(value, dict) or value.get("currency") != CURRENCY:
        return None
    raw = value.get("raw_value")
    if not isinstance(raw, str) or not _AMOUNT.fullmatch(raw):
        return None
    whole, _, fraction = raw.partition(".")
    return int(whole) * 100 + int((fraction + "00")[:2])


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (AttributeError, TypeError, ValueError):
        return None


def parse_price(row, now):
    """One price row: {status, regular_cents, final_cents, discount_ends_at} or None (not for sale).

    A sale price counts only between its start and end; without both it is
    not shown.
    """
    if not isinstance(row, dict) or row.get("sales_status") not in SELLING:
        return None
    regular = _cents(row.get("regular_price"))
    if regular is None:
        return None
    result = {"status": row["sales_status"], "regular_cents": regular, "final_cents": regular, "discount_ends_at": None}
    discount = row.get("discount_price")
    if isinstance(discount, dict):
        cents = _cents(discount)
        start, end = _stamp(discount.get("start_datetime")), _stamp(discount.get("end_datetime"))
        current = now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if cents is not None and start and end and start <= current < end and cents < regular:
            result.update(final_cents=cents, discount_ends_at=end)
    return result


def parse_prices(document, requested, now):
    """{nsuid: price or None} for the requested IDs, or None when the reply is off."""
    if (not isinstance(document, dict) or document.get("country") != "HK"
            or not isinstance(document.get("prices"), list)):
        return None
    result = {}
    for row in document["prices"]:
        if not isinstance(row, dict) or type(row.get("title_id")) is not int:
            return None
        nsuid = str(row["title_id"])
        if nsuid not in requested or nsuid in result:
            return None
        result[nsuid] = parse_price(row, now)
    return result if set(result) == set(requested) else None


class NintendoPriceClient:
    """Hong Kong eShop prices for the matched products, a few requests a day."""
    def __init__(self, session=None, *, clock=None):
        self.session = session or requests.Session()
        self._owns_session = session is None
        if self._owns_session:
            self.session.trust_env = False
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.request_count = 0

    def fetch(self, nsuids):
        """{"status": ok|partial|unavailable|rate_limited, "prices": {nsuid: price or None}}."""
        ids = sorted({value for value in nsuids if isinstance(value, str) and _NSUID.fullmatch(value)})
        prices, status = {}, "ok"
        try:
            for offset in range(0, len(ids), MAX_IDS):
                batch = ids[offset:offset + MAX_IDS]
                if self.request_count >= MAX_REQUESTS:
                    status = "partial" if prices else "unavailable"
                    break
                self.request_count += 1
                try:
                    response = self.session.get(PRICE_URL, params={"country": "HK", "lang": "zh", "ids": ",".join(batch)},
                                                timeout=TIMEOUT_SECONDS, allow_redirects=False)
                except requests.RequestException:
                    status = "partial" if prices else "unavailable"
                    continue
                if response.status_code == 429:
                    status = "rate_limited"
                    break
                if response.status_code != 200 or len(response.content) > MAX_BYTES:
                    status = "partial" if prices else "unavailable"
                    continue
                try:
                    parsed = parse_prices(response.json(), set(batch), self.clock())
                except ValueError:
                    parsed = None
                if parsed is None:
                    status = "partial" if prices else "unavailable"
                    continue
                prices.update(parsed)
            if status == "unavailable" and prices:
                status = "partial"
            return {"status": status if ids else "ok", "prices": prices}
        finally:
            if self._owns_session:
                self.session.close()
