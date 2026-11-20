from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from nintendo_backend import official_store as S
from nintendo_backend.catalog import normalize_game
from nintendo_backend.collect import collect
from nintendo_backend.nintendo_schedule import HK_URL, TW_URL, parse_schedule
from nintendo_backend.official_descriptions import OfficialDescriptionClient
from scripts.publish import FILES, PublishError, validate_bundle

FIXTURES = Path(__file__).parent / "fixtures"
TAIWAN = parse_schedule((FIXTURES / "nintendo_tw_schedule.html").read_text(encoding="utf-8"))
HONG_KONG = parse_schedule((FIXTURES / "nintendo_hk_schedule.html").read_text(encoding="utf-8"))
OCARINA_PAGE = (FIXTURES / "nintendo_hk_ocarina_product.html").read_text(encoding="utf-8")
START, END = date(2026, 11, 20), date(2027, 11, 20)
CHECKED = "2026-11-20T00:30:00Z"
NOW = datetime(2026, 11, 20, 0, 45, tzinfo=timezone.utc)
METAPHOR, OCARINA = "70010000105918", "70010000130795"
METAPHOR_URL, OCARINA_URL = (f"https://ec.nintendo.com/HK/zh/titles/{nsuid}" for nsuid in (METAPHOR, OCARINA))


def stamp(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def metaphor(**changes):
    row = {"id": 1001, "name": "Metaphor: ReFantazio", "hypes": 40, "category": 0, "summary": "A fantasy RPG.",
           "platforms": [{"id": 508}],
           "release_dates": [{"id": 11, "platform": {"id": 508}, "category": 0, "date": stamp("2026-11-11"), "region": 8}]}
    row.update(changes)
    return row


def ocarina(**changes):
    row = {"id": 1002, "name": "The Legend of Zelda: Ocarina of Time", "hypes": 80, "category": 0,
           "summary": "A remake of the adventure.", "platforms": [{"id": 508}],
           "game_localizations": [{"name": "薩爾達傳說 時之笛", "region": {"identifier": "zh-TW"}}],
           "release_dates": [{"id": 12, "platform": {"id": 508}, "category": 0, "date": stamp("2026-11-05"), "region": 8}]}
    row.update(changes)
    return row


def price(final=18700, regular=37400, ends="2026-11-27T14:59:59Z", status="onsale"):
    return {"status": status, "regular_cents": regular, "final_cents": final, "discount_ends_at": ends if final < regular else None}


def nintendo_state(raw, nsuid=METAPHOR, title="暗喻幻想：ReFantazio（Metaphor: ReFantazio）", **changes):
    entry = {"outcome": "confirmed", "checked_at": CHECKED, "attempted_at": CHECKED, "confirmed_at": CHECKED,
             "evidence": {"region": "taiwan", "source_url": TW_URL, "title": title, "date": "2026-11-12",
                          "nsuid": nsuid, "chinese": False, "section": "schedule"},
             "product": {"nsuid": nsuid, "titles": [title], "tw_title": title, "hk_title": title, "matched_at": CHECKED},
             "price": {"currency": "HKD", **price()}, "price_checked_at": CHECKED}
    entry.update(changes)
    return {"igdb_id": raw["id"], "name_en": raw["name"], "NS2": entry}


def test_a_switch_2_game_gets_its_taiwan_day_eshop_link_price_and_chinese_name():
    row = normalize_game(metaphor(), START, END, CHECKED, store_state=nintendo_state(metaphor()))
    [release] = row["releases"]
    assert (release["date"], release["source"], release["official_source_url"], release["official_source_name"]) == (
        "2026-11-12", "official_registry", TW_URL, "任天堂台灣官網")
    assert release["source_date"] == "2026-11-11"
    assert row["platform_urls"] == {"NS2": METAPHOR_URL}
    assert row["platform_prices"] == {"NS2": {
        "currency": "HKD", "free": False, "final_cents": 18700, "status": "onsale", "source": "任天堂 eShop 香港",
        "source_url": METAPHOR_URL, "checked_at": CHECKED, "original_cents": 37400, "discount_pct": 50,
        "discount_ends_at": "2026-11-27T14:59:59Z"}}
    assert (row["display_name"], row["name_source"], row["name_evidence"]["provider"]) == (
        "暗喻幻想：ReFantazio", "任天堂台灣官網", "nintendo_official")
    # A week later without a new read the price is gone; the link and name stay.
    later = normalize_game(metaphor(), START, END, "2026-11-27T00:30:01Z", store_state=nintendo_state(metaphor()))
    assert later["platform_prices"] == {} and later["platform_urls"] == {"NS2": METAPHOR_URL}
    # The game's own Chinese name stays.
    named = metaphor(game_localizations=[{"name": "暗喻幻想", "region": {"identifier": "zh-TW"}}])
    assert normalize_game(named, START, END, CHECKED, store_state=nintendo_state(named))["display_name"] == "暗喻幻想"


@pytest.mark.parametrize("changes", [
    {"product": {"nsuid": "7001", "titles": ["x"], "tw_title": None, "hk_title": None, "matched_at": CHECKED}},
    {"product": {"nsuid": METAPHOR, "titles": [], "tw_title": None, "hk_title": None, "matched_at": CHECKED}},
    {"product": {"nsuid": METAPHOR, "titles": ["x"], "tw_title": "y", "hk_title": None, "matched_at": CHECKED}},
    {"product": {"nsuid": METAPHOR, "titles": ["x"], "tw_title": None, "hk_title": None, "matched_at": "2026-11-21T00:00:00Z"}},
])
def test_a_product_that_does_not_hold_up_gives_no_link_price_or_name(changes):
    row = normalize_game(metaphor(), START, END, CHECKED, store_state=nintendo_state(metaphor(), **changes))
    assert row["platform_urls"] == {} and row["platform_prices"] == {} and row["display_name"] == "Metaphor: ReFantazio"


def test_a_price_read_after_the_collection_or_in_another_currency_is_not_shown():
    for changes in ({"price_checked_at": "2026-11-20T00:30:01Z"}, {"price": {"currency": "TWD", **price()}},
                    {"price": {"currency": "HKD", **price(final=40000)}}):
        row = normalize_game(metaphor(), START, END, CHECKED, store_state=nintendo_state(metaphor(), **changes))
        assert row["platform_prices"] == {} and row["platform_urls"] == {"NS2": METAPHOR_URL}


def read(taiwan=(TW_URL,)):
    return {"status": "ok", "taiwan": set(taiwan)}


def test_the_product_stays_after_the_schedules_drop_it_and_changes_with_a_new_match():
    from nintendo_backend import nintendo_official as O
    raw = metaphor()
    [found] = O.products({TW_URL: [row for row in TAIWAN if row["nsuid"] == METAPHOR]})
    first = S.merge_nintendo(None, raw, "NS2", read(), found, CHECKED)["NS2"]
    assert first["product"]["nsuid"] == METAPHOR and first["evidence"]["date"] == "2026-11-12"
    assert first["product"]["matched_at"] == CHECKED and first["price"] is None
    priced = S.merge_price({"igdb_id": raw["id"], "name_en": raw["name"], "NS2": first}, "NS2",
                           {"status": "ok", "prices": {METAPHOR: price()}}, CHECKED)
    # The next month the schedule no longer lists it (released): product and price stay, the final day too.
    later = "2026-12-20T00:30:00Z"
    final = deepcopy(priced)
    final["NS2"]["confirmed_at"] = "2026-11-12T00:30:00Z"
    kept = S.merge_nintendo(final, raw, "NS2", read(), None, later)["NS2"]
    assert kept["product"] == first["product"] and kept["price"] == priced["NS2"]["price"]
    assert kept["evidence"]["date"] == "2026-11-12"
    # Hong Kong's page alone adds its title to the same product.
    [hong_kong] = O.products({HK_URL: [row for row in HONG_KONG if row["nsuid"] == METAPHOR]})
    joined = S.merge_nintendo(priced, raw, "NS2", read(()), hong_kong, later)["NS2"]
    assert joined["product"]["matched_at"] == CHECKED and joined["product"]["hk_title"] and joined["price"]
    # Another product replaces it, without the old price.
    other = deepcopy(found)
    other["nsuid"] = "70010000105919"
    moved = S.merge_nintendo(priced, raw, "NS2", read(), other, later)["NS2"]
    assert moved["product"]["nsuid"] == "70010000105919" and moved["product"]["matched_at"] == later
    assert moved["price"] is None
    # A price reply without the product keeps the price; "not for sale" removes it.
    assert S.merge_price(priced, "NS2", {"status": "ok", "prices": {}}, later) == priced
    gone = S.merge_price(priced, "NS2", {"status": "ok", "prices": {METAPHOR: None}}, later)["NS2"]
    assert gone["price"] is None and gone["price_checked_at"] == later


class API:
    request_count, retry_count = 4, 0

    def __init__(self, results):
        self.results = list(results)

    def verify_platforms(self):
        pass

    def query(self, endpoint, query):
        return [] if endpoint == "popularity_primitives" else self.results.pop(0)


def api():
    rows = [{"id": 1, "game": 1001, "platform": 508}, {"id": 2, "game": 1002, "platform": 508}]
    return API([{"count": 2}, rows, {"count": 2}, [metaphor(), ocarina()]])


class Schedules:
    def __init__(self, taiwan="ok", hong_kong="ok"):
        self.statuses = taiwan, hong_kong

    def fetch(self):
        return {"taiwan": {"status": self.statuses[0], "entries": TAIWAN if self.statuses[0] == "ok" else None},
                "hong_kong": {"status": self.statuses[1], "entries": HONG_KONG if self.statuses[1] == "ok" else None}}


class Prices:
    def __init__(self, prices=None, status="ok"):
        self.prices, self.status, self.request_count, self.calls = prices, status, 0, []

    def fetch(self, nsuids):
        self.calls.append(list(nsuids))
        self.request_count += 1
        if self.prices is None:
            return {"status": self.status, "prices": {}}
        return {"status": self.status, "prices": {nsuid: self.prices.get(nsuid) for nsuid in nsuids}}


class Response:
    def __init__(self, url, status=200, text=""):
        self.url, self.status_code, self.text = url, status, text
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def iter_content(self, chunk_size=16384):
        yield self.text.encode("utf-8")

    def close(self):
        pass


class Pages:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return Response(url, text=OCARINA_PAGE) if url == OCARINA_URL else Response(url, 404)


def collected(previous=None, checked_at=CHECKED, start=START, schedules=None, prices=None, pages=None):
    pages = pages or Pages()
    descriptions = OfficialDescriptionClient(pages)
    return collect(api(), start=start, checked_at=checked_at, previous=previous,
                   nintendo_schedule_client=schedules or Schedules(),
                   nintendo_price_client=prices or Prices({METAPHOR: price(), OCARINA: price(44900, 44900)}),
                   official_description_client=descriptions), pages


def test_collect_reads_the_schedules_and_prices_once_and_the_gate_rebuilds_every_row():
    prices = Prices({METAPHOR: price(), OCARINA: price(44900, 44900)})
    (master, public, status), pages = collected(prices=prices)
    games = {row["igdb_id"]: row for row in public["games"]}
    meta, zelda = games[1001], games[1002]
    assert meta["releases"][0]["date"] == "2026-11-12" and meta["releases"][0]["official_source_url"] == TW_URL
    assert meta["platform_urls"] == {"NS2": METAPHOR_URL} and meta["platform_prices"]["NS2"]["final_cents"] == 18700
    assert meta["display_name"] == "暗喻幻想：ReFantazio"
    # The Hong Kong eShop page gives Zelda's Chinese description; its own Chinese name stays.
    assert zelda["short_description_source"] == "official_zh_tw" and zelda["description_source_url"] == OCARINA_URL
    assert zelda["short_description"].startswith("居住在科奇里森林的少年林克")
    assert zelda["display_name"] == "薩爾達傳說 時之笛" and zelda["name_source"] == "IGDB game_localizations"
    assert zelda["platform_prices"]["NS2"]["final_cents"] == 44900 and "original_cents" not in zelda["platform_prices"]["NS2"]
    assert prices.calls == [sorted([METAPHOR, OCARINA])] and OCARINA_URL in pages.calls
    summary = status["nintendo_official"]
    assert summary["pages"][TW_URL] == {"status": "ok", "entry_count": 7}
    assert summary["platforms"]["NS2"] == {"game_count": 2, "matched_count": 2, "taiwan_date_count": 2,
                                           "eshop_link_count": 2, "price_count": 2}
    assert summary["official_name_count"] == 1 and summary["price"]["product_count"] == 2
    validate_bundle(dict(zip(FILES, (master, public, status))), now=NOW)


@pytest.mark.parametrize("forge", [
    lambda master, public: public["platform_prices"]["NS2"].update(final_cents=10000),
    lambda master, public: [row["platform_prices"]["NS2"].update(final_cents=10000) for row in (master, public)],
    lambda master, public: [row["platform_urls"].update(NS2="https://ec.nintendo.com/HK/zh/titles/70010000999999")
                            for row in (master, public)],
    lambda master, public: [row.update(display_name="假名稱", name_zh_tw="假名稱") for row in (master, public)],
    lambda master, public: master["official_store"]["NS2"].update(price_checked_at="2026-11-20T00:31:00Z"),
    lambda master, public: master.pop("official_store"),
])
def test_nintendo_rows_cannot_be_forged(forge):
    (master, public, status), _ = collected()
    key = next(key for key, row in master["games"].items() if row["igdb_id"] == 1001)
    forge(master["games"][key], next(row for row in public["games"] if row["id"] == key))
    with pytest.raises(PublishError):
        validate_bundle(dict(zip(FILES, (master, public, status))), now=NOW)


def test_the_next_day_without_any_page_keeps_everything_and_a_failed_price_read_keeps_the_price():
    (master, _, _), _ = collected()
    later, day = "2026-11-21T00:30:00Z", date(2026, 11, 21)
    (_, public, status), _ = collected(previous=master, checked_at=later, start=day,
                                       schedules=Schedules("unavailable", "unavailable"), prices=Prices(status="unavailable"))
    meta = next(row for row in public["games"] if row["igdb_id"] == 1001)
    assert meta["releases"][0]["date"] == "2026-11-12" and meta["platform_prices"]["NS2"]["checked_at"] == CHECKED
    assert status["nintendo_official"]["pages"][TW_URL]["status"] == "unavailable"
