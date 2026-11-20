from datetime import datetime, timezone

import pytest
import requests

from nintendo_backend import nintendo_eshop as E

NOW = datetime(2026, 11, 20, 0, 30, tzinfo=timezone.utc)


def row(title_id=70010000105918, status="onsale", regular="374", discount=None):
    result = {"title_id": title_id, "sales_status": status}
    if regular is not None:
        result["regular_price"] = {"amount": f"HKD {regular}", "currency": "HKD", "raw_value": regular}
    if discount:
        result["discount_price"] = discount
    return result


def sale(value="187", start="2026-11-13T15:00:00Z", end="2026-11-27T14:59:59Z"):
    return {"amount": f"HKD {value}", "currency": "HKD", "raw_value": value, "start_datetime": start, "end_datetime": end}


def test_title_pages_are_the_hong_kong_eshop_only():
    assert E.title_url("70010000105918") == "https://ec.nintendo.com/HK/zh/titles/70010000105918"
    assert E.title_url("7001000010591") is None and E.title_url("60010000105918") is None and E.title_url(None) is None


def test_prices_in_cents_with_a_running_sale_and_its_end():
    assert E.parse_price(row(), NOW) == {"status": "onsale", "regular_cents": 37400, "final_cents": 37400,
                                         "discount_ends_at": None}
    assert E.parse_price(row(discount=sale()), NOW) == {"status": "onsale", "regular_cents": 37400, "final_cents": 18700,
                                                        "discount_ends_at": "2026-11-27T14:59:59Z"}
    assert E.parse_price(row(status="preorder", regular="240"), NOW)["status"] == "preorder"
    assert E.parse_price(row(regular="0"), NOW)["final_cents"] == 0
    assert E.parse_price(row(regular="449.5"), NOW)["regular_cents"] == 44950
    # A sale not started, already over or without its times shows the regular price.
    for discount in (sale(start="2026-11-21T00:00:00Z"), sale(end="2026-11-19T00:00:00Z"), sale(start=None)):
        assert E.parse_price(row(discount=discount), NOW)["final_cents"] == 37400
    # Not for sale, or no readable price: no price.
    for value in (row(status="not_found", regular=None), row(status="unreleased"), row(regular="HKD 374"),
                  {**row(), "regular_price": {"currency": "JPY", "raw_value": "374"}}):
        assert E.parse_price(value, NOW) is None


def test_a_reply_must_cover_exactly_the_requested_titles():
    requested = {"70010000105918", "70010000129453"}
    document = {"country": "HK", "prices": [row(), row(70010000129453, "not_found", None)]}
    assert E.parse_prices(document, requested, NOW) == {"70010000105918": E.parse_price(row(), NOW), "70010000129453": None}
    assert E.parse_prices({**document, "country": "JP"}, requested, NOW) is None
    assert E.parse_prices({"country": "HK", "prices": [row()]}, requested, NOW) is None
    assert E.parse_prices({"country": "HK", "prices": [row(), row(), row(70010000129453)]}, requested, NOW) is None
    assert E.parse_prices({"country": "HK", "prices": [row(), row(70010000999999)]}, requested, NOW) is None


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code, self.payload = status, payload
        self.content = b"{}"

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.handler(url, kwargs)


def ok(url, kwargs):
    ids = kwargs["params"]["ids"].split(",")
    return Response(payload={"country": "HK", "prices": [row(int(value)) for value in ids]})


def ids(count):
    return [str(70010000100000 + index) for index in range(count)]


def test_prices_are_read_fifty_titles_a_request_without_redirects():
    session = Session(ok)
    client = E.NintendoPriceClient(session, clock=lambda: NOW)
    result = client.fetch(ids(120) + ["bad", "70010000100000"])
    assert result["status"] == "ok" and len(result["prices"]) == 120 and client.request_count == 3
    url, kwargs = session.calls[0]
    assert url == E.PRICE_URL and kwargs["allow_redirects"] is False and kwargs["timeout"] == E.TIMEOUT_SECONDS
    assert kwargs["params"]["country"] == "HK" and len(kwargs["params"]["ids"].split(",")) == 50
    assert E.NintendoPriceClient(Session(ok)).fetch([]) == {"status": "ok", "prices": {}}


def test_a_rate_limit_stops_and_errors_leave_the_titles_unread():
    calls = []

    def limited(url, kwargs):
        calls.append(url)
        return Response(429) if len(calls) == 2 else ok(url, kwargs)
    result = E.NintendoPriceClient(Session(limited), clock=lambda: NOW).fetch(ids(150))
    assert result["status"] == "rate_limited" and len(result["prices"]) == 50 and len(calls) == 2

    def broken(url, kwargs):
        raise requests.ConnectionError("down")
    assert E.NintendoPriceClient(Session(broken), clock=lambda: NOW).fetch(ids(3)) == {"status": "unavailable", "prices": {}}
    odd = E.NintendoPriceClient(Session(lambda url, kwargs: Response(payload=ValueError("not json"))), clock=lambda: NOW)
    assert odd.fetch(ids(3))["status"] == "unavailable"
    # At most MAX_REQUESTS requests a run.
    many = E.NintendoPriceClient(Session(ok), clock=lambda: NOW)
    result = many.fetch(ids(E.MAX_IDS * (E.MAX_REQUESTS + 1)))
    assert result["status"] == "partial" and many.request_count == E.MAX_REQUESTS
