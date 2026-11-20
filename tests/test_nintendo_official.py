from datetime import date

from nintendo_backend import nintendo_official as O
from nintendo_backend.nintendo_schedule import HK_URL, TW_URL
from nintendo_backend.nintendo_taiwan import LIST_URL


def schedule(title="暗喻幻想：ReFantazio（Metaphor: ReFantazio）", day="2026-11-12", platform="NS2",
             nsuid="70010000105918", **changes):
    return {"platform": platform, "title": title, "date": day, "publisher": "SEGA", "nsuid": nsuid,
            "formats": "下載版", **changes}


def listing(title="闇夜王國", day="2026-11-05", nsuid="70010000129453", **changes):
    return {"section": "on_sale", "title": title, "hardware": "Nintendo Switch", "date": day, "publisher": "DANGEN",
            "nsuid": nsuid, "chinese": True, **changes}


def game(name="Metaphor: ReFantazio", **changes):
    return {"id": 1, "name": name, **changes}


def test_paired_titles_match_either_part_and_give_their_chinese_part():
    keys = O.title_keys("慟哭幽光／CRYMELIGHT")
    assert {O.normalize("慟哭幽光"), O.normalize("CRYMELIGHT")} <= keys
    assert O.normalize("Metaphor: ReFantazio") in O.title_keys("暗喻幻想：ReFantazio（Metaphor: ReFantazio）")
    # A slash in an English title is not a pair.
    assert O.title_keys("Fate/Samurai Remnant") == {O.normalize("Fate/Samurai Remnant")}
    assert O.chinese_title("暗喻幻想：ReFantazio（Metaphor: ReFantazio）") == "暗喻幻想：ReFantazio"
    assert O.chinese_title("慟哭幽光／CRYMELIGHT") == "慟哭幽光"
    assert O.chinese_title("劍星™ 完全版") == "劍星 完全版"
    assert O.chinese_title("Warhammer 40,000: Boltgun 2") is None
    assert O.chinese_title("ドラゴンクエスト") is None and O.chinese_title(None) is None


def test_one_product_per_title_id_with_the_earliest_taiwan_day():
    rows = O.products({
        LIST_URL: O.list_entries([listing(), listing(title="Switch 2 only", hardware="Nintendo Switch 2")]),
        TW_URL: [schedule(), schedule(title="闇夜王國", day="2026-11-06", platform="NS", nsuid="70010000129453")],
        HK_URL: [schedule(day="2026-11-11"), schedule(title="闇夜王國", day="2026-11-05", platform="NS", nsuid="70010000129453")],
    })
    by_id = {row["nsuid"]: row for row in rows}
    assert set(by_id) == {"70010000105918", "70010000129453"}
    kingdom = by_id["70010000129453"]
    # The list's day is earlier than the schedule's; Hong Kong's day is not a Taiwan day.
    assert kingdom["taiwan"] == {"source_url": LIST_URL, "title": "闇夜王國", "date": "2026-11-05", "chinese": True,
                                 "section": "on_sale"}
    metaphor = by_id["70010000105918"]
    assert metaphor["taiwan"]["source_url"] == TW_URL and metaphor["taiwan"]["date"] == "2026-11-12"
    assert metaphor["days"] == ["2026-11-11", "2026-11-12"] and metaphor["hk_title"] == metaphor["tw_title"]
    # Only Hong Kong's page: a product without a Taiwan day.
    [hong_kong] = O.products({HK_URL: [schedule()]})
    assert hong_kong["taiwan"] is None and hong_kong["tw_title"] is None and hong_kong["hk_title"]


def test_a_title_only_list_entry_joins_the_one_product_with_its_title():
    rows = O.products({LIST_URL: O.list_entries([listing(nsuid=None)]),
                       TW_URL: [schedule(title="闇夜王國", platform="NS", nsuid="70010000129453")]})
    assert len(rows) == 1 and rows[0]["taiwan"]["source_url"] == LIST_URL
    alone = O.products({LIST_URL: O.list_entries([listing(nsuid=None)])})
    assert len(alone) == 1 and alone[0]["nsuid"] is None


def test_match_needs_one_product_one_game_the_platform_and_a_nearby_day():
    rows = O.products({TW_URL: [schedule(), schedule(title="闇夜王國", platform="NS", nsuid="70010000129453")]})
    games = {"igdb:1": (game(), {}, {"NS2": date(2026, 11, 12), "PS5": date(2026, 11, 12)})}
    found = O.match(rows, games)["igdb:1"]
    assert set(found) == {"NS2"} and found["NS2"]["nsuid"] == "70010000105918"
    # A Chinese name from IGDB matches the Chinese title.
    chinese = {"igdb:2": ({"id": 2, "name": "Kingdom of Night"}, {"name_zh_tw": "闇夜王國"}, {"NS": None})}
    assert O.match(rows, chinese)["igdb:2"]["NS"]["nsuid"] == "70010000129453"
    # Another hardware, a far day, a twin claim or two products with the title match nothing.
    assert O.match(rows, {"igdb:1": (game(), {}, {"NS": date(2026, 11, 12)})}) == {}
    assert O.match(rows, {"igdb:1": (game(), {}, {"NS2": date(2027, 2, 1)})}) == {}
    twins = {**games, "igdb:3": (game(), {}, {"NS2": None})}
    assert O.match(rows, twins) == {}
    doubled = O.products({TW_URL: [schedule(), schedule(nsuid="70010000105919")]})
    assert O.match(doubled, games) == {}
    # An undated product still matches by title.
    undated = O.products({TW_URL: [schedule(day=None)]})
    assert O.match(undated, games)["igdb:1"]["NS2"]["taiwan"] is None


def product(**changes):
    return {"nsuid": "70010000105918", "titles": ["暗喻幻想：ReFantazio（Metaphor: ReFantazio）"],
            "tw_title": "暗喻幻想：ReFantazio（Metaphor: ReFantazio）", "hk_title": None,
            "matched_at": "2026-11-20T00:30:00Z", **changes}


def test_an_official_chinese_name_fills_only_a_game_without_its_own():
    english = {"display_name": "Metaphor: ReFantazio", "name_en": "Metaphor: ReFantazio", "name_zh_tw": None,
               "name_source": "IGDB name", "name_url": None, "name_evidence": None}
    named = O.official_name(game(), [("NS2", product())], english)
    assert (named["display_name"], named["name_source"], named["name_url"]) == (
        "暗喻幻想：ReFantazio", "任天堂台灣官網", "https://ec.nintendo.com/HK/zh/titles/70010000105918")
    assert named["name_evidence"]["provider"] == "nintendo_official" and named["name_evidence"]["converted"] is False
    hong_kong = O.official_name(game(), [("NS2", product(tw_title=None, hk_title=product()["titles"][0]))], english)
    assert hong_kong["name_source"] == "任天堂香港官網"
    # The game's own Traditional name stays; a converted Simplified one is replaced.
    own = {**english, "name_zh_tw": "暗喻幻想", "name_evidence": {"converted": False}}
    assert O.official_name(game(), [("NS2", product())], own) is None
    converted = {**english, "name_zh_tw": "暗喻幻想", "name_evidence": {"converted": True}}
    assert O.official_name(game(), [("NS2", product())], converted)["name_zh_tw"] == "暗喻幻想：ReFantazio"
    # An English-only or Simplified title is no Chinese name.
    assert O.official_name(game(), [("NS2", product(titles=["Metaphor"], tw_title="Metaphor"))], english) is None
    assert O.official_name(game(), [("NS2", product(titles=["暗喻幻想：隐喻"], tw_title="暗喻幻想：隐喻"))], english) is None


def test_the_matched_products_give_their_hong_kong_eshop_pages():
    rows = O.description_candidates(game(), [("NS2", product()), ("NS", product(nsuid="70010000129453"))])
    assert [row["url"] for row in rows] == ["https://ec.nintendo.com/HK/zh/titles/70010000105918",
                                            "https://ec.nintendo.com/HK/zh/titles/70010000129453"]
    assert rows[0]["platform"] == "NS2" and rows[0]["titles"] == [
        "Metaphor: ReFantazio", "暗喻幻想：ReFantazio（Metaphor: ReFantazio）", "暗喻幻想：ReFantazio"]
