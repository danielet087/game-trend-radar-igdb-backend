"""NS/NS2 languages from the matched product's Hong Kong eShop page."""
from datetime import date, datetime, timezone
import json
from pathlib import Path

import pytest

from nintendo_backend import nintendo_taiwan, official_store as S
from nintendo_backend.catalog import normalize_game
from nintendo_backend.official_descriptions import OfficialDescriptionClient, parse_languages

FIXTURES = Path(__file__).parent / "fixtures"
PAGES = {name: (FIXTURES / f"nintendo_hk_{name}_product.html").read_text(encoding="utf-8")
         for name in ("sakura", "revolt", "crymelight", "ocarina")}
SAKURA, REVOLT, CRYMELIGHT = "70010000123916", "70010000099379", "70010000132540"
URL = "https://ec.nintendo.com/HK/zh/titles/{}".format
START, END = date(2026, 11, 20), date(2027, 11, 20)
CHECKED = "2026-11-26T00:30:00Z"


def languages(name, nsuid, titles, platform):
    return parse_languages(PAGES[name], URL(nsuid), titles, platform=platform)


def test_the_language_row_names_each_chinese_script_and_a_row_without_chinese_means_none():
    assert languages("sakura", SAKURA, ["夢幻櫻樓閣"], "NS") == {
        "supported_languages": ["ja", "en", "zh-Hans", "zh-Hant"], "complete": True,
        "evidence_type": "official_product_languages", "official_title": "夢幻櫻樓閣"}
    assert languages("revolt", REVOLT, ["Re-Volt"], "NS") == {
        "supported_languages": ["ja", "en", "es", "fr", "de", "it", "ko"], "complete": True,
        "evidence_type": "official_product_languages", "official_title": "Re-Volt"}


def test_without_the_row_the_json_ld_proves_chinese_but_not_its_script():
    unspecified = {"supported_languages": ["zh"], "complete": False, "evidence_type": "official_chinese_unspecified"}
    assert languages("crymelight", CRYMELIGHT, ["Crymelight", "慟哭幽光／CRYMELIGHT"], "NS2") == {
        **unspecified, "official_title": "慟哭幽光／CRYMELIGHT"}
    assert languages("ocarina", "70010000130795", ["薩爾達傳說 時之笛"], "NS2") == {
        **unspecified, "official_title": "薩爾達傳說 時之笛"}
    # Without "zh" the JSON-LD alone says nothing.
    page = PAGES["crymelight"].replace('"inLanguage":["ja","en","ko","zh","zh"]', '"inLanguage":["ja","en","ko"]')
    assert parse_languages(page, URL(CRYMELIGHT), ["慟哭幽光／CRYMELIGHT"], platform="NS2") is None


def edit(name, old, new):
    assert old in PAGES[name]
    return PAGES[name].replace(old, new)


SAKURA_ARGS = (URL(SAKURA), ["夢幻櫻樓閣"])


@pytest.mark.parametrize("page,url,titles,platform", [
    (PAGES["sakura"], *SAKURA_ARGS, "NS2"),  # The page names another platform.
    (PAGES["sakura"], URL(SAKURA), ["Sakura Fantasy"], "NS"),  # Another game.
    (PAGES["sakura"], f"https://ec.nintendo.com/TW/zh/titles/{SAKURA}", ["夢幻櫻樓閣"], "NS"),  # Not the Hong Kong eShop.
    (PAGES["sakura"], *SAKURA_ARGS, "PS5"),
    # The row and the JSON-LD disagree: one Chinese entry fewer, one entry more.
    (edit("sakura", '"inLanguage":["ja","en","zh","zh"]', '"inLanguage":["ja","en","ko","zh"]'), *SAKURA_ARGS, "NS"),
    (edit("sakura", '"inLanguage":["ja","en","zh","zh"]', '"inLanguage":["ja","en","ko","zh","zh"]'), *SAKURA_ARGS, "NS"),
    (edit("revolt", '"inLanguage":["ja","en","es","fr","de","it","ko"]', '"inLanguage":["ja","en","es","fr","de","it","zh"]'),
     URL(REVOLT), ["Re-Volt"], "NS"),
    # A Chinese entry without one script, or an empty entry.
    (edit("sakura", "中文 (簡體字)", "中文"), *SAKURA_ARGS, "NS"),
    (edit("sakura", "中文 (簡體字)", "中文 (繁體字)"), *SAKURA_ARGS, "NS"),
    (edit("sakura", '<li class="inline">日文<span', '<li class="inline"><span'), *SAKURA_ARGS, "NS"),
    # No language list in the JSON-LD, or two different games of that name.
    (edit("sakura", '"inLanguage":["ja","en","zh","zh"],', ""), *SAKURA_ARGS, "NS"),
    (edit("sakura", "</head>", '<script type="application/ld+json">{"@type":"VideoGame","name":"夢幻櫻樓閣",'
                               '"gamePlatform":"Nintendo Switch","inLanguage":["ja","zh"]}</script></head>'), *SAKURA_ARGS, "NS"),
    # Two language rows.
    (edit("sakura", "</dl>", '<div><div class="label">對應語言</div><div><ul><li>日文</li></ul></div></div></dl>'),
     *SAKURA_ARGS, "NS"),
])
def test_a_page_that_does_not_hold_up_gives_no_languages(page, url, titles, platform):
    assert parse_languages(page, url, titles, platform=platform) is None


def test_a_live_row_with_thai_and_polish_in_the_json_ld_s_order():
    """SONIC PICO PARK's Switch page on 2026-11-25 (detail row as served, classes and icons removed)."""
    items = ["日文", "英文", "葡萄牙文", "西班牙文", "法文", "德文", "意大利文", "俄文", "韓文",
             "中文 (簡體字)", "中文 (繁體字)", "泰文", "波蘭文"]
    row = "".join(f'<li>{item}<span aria-hidden="true">, </span></li>' for item in items[:-1]) + f"<li>{items[-1]}</li>"
    game = {"@context": "https://schema.org", "@type": "VideoGame", "gamePlatform": "Nintendo Switch", "name": "SONIC PICO PARK",
            "inLanguage": ["ja", "en", "pt", "es", "fr", "de", "it", "ru", "ko", "zh", "zh", "th", "pl"]}
    page = (f'<html><head><script type="application/ld+json">{json.dumps(game, ensure_ascii=False)}</script></head>'
            f"<body><dl><div><div>遊玩人數</div><div>× 1 ～ 8</div></div><div><div>對應語言</div><div><ul>{row}</ul></div></div></dl></body></html>")
    assert parse_languages(page, URL("70010000133649"), ["SONIC PICO PARK"], platform="NS") == {
        "supported_languages": ["ja", "en", "pt", "es", "fr", "de", "it", "ru", "ko", "zh-Hans", "zh-Hant", "th", "pl"],
        "complete": True, "evidence_type": "official_product_languages", "official_title": "SONIC PICO PARK"}


def test_a_language_without_a_known_code_leaves_the_list_partial():
    page = edit("sakura", '"inLanguage":["ja","en","zh","zh"]', '"inLanguage":["ja","xx","zh","zh"]')
    assert parse_languages(page, *SAKURA_ARGS, platform="NS") == {
        "supported_languages": ["ja", "zh-Hans", "zh-Hant"], "complete": False,
        "evidence_type": "official_product_languages", "official_title": "夢幻櫻樓閣"}
    # Without Chinese a partial list says nothing about Chinese.
    page = edit("revolt", '"inLanguage":["ja","en","es","fr","de","it","ko"]', '"inLanguage":["ja","en","es","fr","de","it","xx"]')
    assert parse_languages(page, URL(REVOLT), ["Re-Volt"], platform="NS") is None


class Response:
    def __init__(self, url, status=200, text=""):
        self.url, self.status_code, self.text = url, status, text
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def iter_content(self, chunk_size=16384):
        yield self.text.encode("utf-8")

    def close(self):
        pass


class Session:
    def __init__(self, pages, status=200):
        self.pages, self.status, self.calls = pages, status, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return Response(url, self.status if url in self.pages else 404, self.pages.get(url, ""))


def test_the_client_reads_a_page_once_for_both_clients_and_says_why_it_did_not():
    session, pages = Session({URL(SAKURA): PAGES["sakura"]}), {}
    client = OfficialDescriptionClient(session, max_requests=2, cache=pages)
    found = client.read_languages(URL(SAKURA), ["夢幻櫻樓閣"], "NS")
    assert found["status"] == "ok" and found["languages"]["complete"] is True
    assert client.read_languages(URL(REVOLT), ["Re-Volt"], "NS") == {"status": "unavailable"}  # 404
    assert client.read_languages(URL(CRYMELIGHT), ["慟哭幽光／CRYMELIGHT"], "NS2") == {"status": "skipped"}  # budget
    assert client.read_languages(f"https://ec.nintendo.com/TW/zh/titles/{SAKURA}", ["夢幻櫻樓閣"], "NS") == {"status": "unavailable"}
    # The description client reuses the page; the cache says which pages were requested.
    descriptions = OfficialDescriptionClient(session, cache=pages)
    assert descriptions.attempted([URL(SAKURA)]) and not descriptions.attempted([URL(CRYMELIGHT)])
    assert descriptions._fetch(URL(SAKURA)) == PAGES["sakura"] and session.calls == [URL(SAKURA), URL(REVOLT)]
    limited = OfficialDescriptionClient(Session({URL(SAKURA): PAGES["sakura"]}, status=429))
    assert limited.read_languages(URL(SAKURA), ["夢幻櫻樓閣"], "NS") == {"status": "rate_limited"}
    assert limited.read_languages(URL(SAKURA), ["夢幻櫻樓閣"], "NS") == {"status": "rate_limited"}  # cached failure


def stamp(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def sakura_game(platform=130):
    return {"id": 2001, "name": "Sakura Fantasy", "hypes": 40, "category": 0, "summary": "A roguelite.",
            "platforms": [{"id": platform}],
            "release_dates": [{"id": 21, "platform": {"id": platform}, "category": 0, "date": stamp("2026-12-03"), "region": 8}]}


def read(codes=("ja", "en", "zh-Hans", "zh-Hant"), complete=True, kind="official_product_languages",
         checked=CHECKED, nsuid=SAKURA):
    return {"nsuid": nsuid, "checked_at": checked, "supported_languages": list(codes), "complete": complete,
            "evidence_type": kind, "official_title": "夢幻櫻樓閣" if kind else None}


UNSPECIFIED = {"codes": ("zh",), "complete": False, "kind": "official_chinese_unspecified"}


def state(languages=None, marked=False, platform="NS", nsuid=SAKURA):
    entry = {"outcome": "confirmed", "checked_at": CHECKED, "attempted_at": CHECKED, "confirmed_at": CHECKED,
             "evidence": None,
             "product": {"nsuid": nsuid, "titles": ["夢幻櫻樓閣"], "tw_title": "夢幻櫻樓閣", "hk_title": None,
                         "matched_at": CHECKED},
             "price": None, "price_checked_at": None, "languages": languages}
    if marked:
        entry["evidence"] = {"region": "taiwan", "source_url": nintendo_taiwan.LIST_URL, "title": "夢幻櫻樓閣",
                             "date": "2026-12-03", "nsuid": nsuid, "chinese": True, "section": "upcoming"}
    return {"igdb_id": 2001, "name_en": "Sakura Fantasy", platform: entry}


def support(store, platform=130, checked=CHECKED):
    row = normalize_game(sakura_game(platform), START, END, checked, store_state=store)
    return row["platform_language_support"]["NS" if platform == 130 else "NS2"]


def test_the_page_list_is_the_version_s_languages_with_its_hong_kong_source():
    found = support(state(read()))
    assert found == {"status": "confirmed", "region": "hong_kong",
                     "languages": {"tchinese": True, "schinese": True, "english": True, "chinese": True},
                     "supported_languages": [{"code": "ja", "name": "日文"}, {"code": "en", "name": "英文"},
                                             {"code": "zh-Hans", "name": "簡體中文"}, {"code": "zh-Hant", "name": "繁體中文"}],
                     "complete": True, "source": "任天堂 eShop 香港", "source_url": URL(SAKURA), "checked_at": CHECKED,
                     "evidence_type": "official_product_languages"}
    # A complete list without Chinese says no Chinese; JSON-LD alone only "Chinese".
    none = support(state(read(("ja", "en"))))
    assert none["languages"] == {"tchinese": False, "schinese": False, "english": True, "chinese": False}
    switch2 = support(state(read(**UNSPECIFIED), platform="NS2"), platform=508)
    assert (switch2["status"], switch2["languages"]["chinese"], switch2["languages"]["tchinese"]) == ("partial", True, None)
    assert switch2["evidence_type"] == "official_chinese_unspecified" and switch2["source_url"] == URL(SAKURA)


def test_taiwan_s_chinese_mark_and_the_page_list_together():
    taiwan = {"region": "taiwan", "source": "任天堂台灣官網", "evidence_type": "official_chinese_unspecified"}
    def summary(found):
        return {key: found[key] for key in taiwan} | {"chinese": found["languages"]["chinese"]}
    # The mark alone, as before; with only "Chinese" on the page Taiwan's comes first.
    assert summary(support(state(marked=True))) == {**taiwan, "chinese": True}
    assert summary(support(state(read(**UNSPECIFIED), marked=True))) == {**taiwan, "chinese": True}
    # The page's complete list names the scripts.
    assert support(state(read(), marked=True))["region"] == "hong_kong"
    # A page list without Chinese does not undo Taiwan's mark.
    assert summary(support(state(read(("ja", "en")), marked=True))) == {**taiwan, "chinese": True}


def test_an_old_or_another_product_s_read_is_not_shown():
    assert support(state(read()), checked="2026-12-26T00:30:01Z")["status"] == "unknown"
    assert support(state(read()), checked="2026-12-26T00:30:00Z")["status"] == "confirmed"
    assert support(state(read(nsuid=REVOLT)))["status"] == "unknown"
    assert support(state(read(checked="2026-11-26T00:30:01Z")))["status"] == "unknown"  # after the collection
    assert support(state(read(codes=(), complete=False, kind=None)))["status"] == "unknown"  # read, no list


@pytest.mark.parametrize("changes", [
    {"supported_languages": ["zh"]}, {"supported_languages": ["ja", "ja", "zh-Hant"]}, {"supported_languages": ["jp"]},
    {"complete": False, "supported_languages": ["ja", "en"]}, {"evidence_type": "official_chinese_unspecified"},
    {"official_title": None}, {"official_title": ""}, {"nsuid": "7001"}, {"checked_at": "2026-11-26"},
    {"evidence_type": "steam"}, {"supported_languages": "zh-Hant"},
])
def test_a_stored_read_that_does_not_hold_up_is_dropped(changes):
    assert S.valid_switch_languages({**read(), **changes}) is None
    assert S.validated_state(sakura_game(), state({**read(), **changes}), CHECKED, ("NS",))["NS"]["languages"] is None


def test_reads_are_due_when_new_every_three_days_before_release_and_weekly_after():
    entry = state(read())["NS"]
    release, day = date(2026, 12, 3), date(2026, 11, 26)
    assert not S.languages_due({**entry, "product": None}, release, day)
    assert S.languages_due({**entry, "languages": None}, release, day)
    assert S.languages_due({**entry, "languages": read(nsuid=REVOLT)}, release, day)  # Another product.
    assert not S.languages_due(entry, release, date(2026, 11, 28)) and S.languages_due(entry, release, date(2026, 11, 29))
    assert not S.languages_due(entry, None, date(2026, 11, 28)) and S.languages_due(entry, None, date(2026, 11, 29))
    released = {**entry, "languages": read(checked="2026-12-03T00:30:00Z")}
    assert not S.languages_due(released, release, date(2026, 12, 9)) and S.languages_due(released, release, date(2026, 12, 10))
    assert S.languages_due(entry, release, date(2026, 11, 25))  # A read "from the future" is read again.


def test_a_read_replaces_the_list_only_when_the_page_answered_and_stays_with_its_product():
    base = state()
    found = S.merge_languages(base, "NS", {"status": "ok", "languages": {key: value for key, value in read().items()
                                                                          if key not in {"nsuid", "checked_at"}}}, CHECKED)
    assert found["NS"]["languages"] == read()
    empty = S.merge_languages(found, "NS", {"status": "ok", "languages": None}, "2026-11-29T00:30:00Z")["NS"]["languages"]
    assert empty == read(codes=(), complete=False, kind=None, checked="2026-11-29T00:30:00Z")
    for status in ("unavailable", "rate_limited", "skipped"):
        assert S.merge_languages(found, "NS", {"status": status}, "2026-11-29T00:30:00Z") == found
    forged = S.merge_languages(base, "NS", {"status": "ok", "languages": {**read(), "supported_languages": ["zh"]}}, CHECKED)
    assert forged["NS"]["languages"]["evidence_type"] is None
    # Today's pages match the same product: the read stays; another product: it goes.
    from nintendo_backend import nintendo_official as O
    raw = sakura_game()
    entries = [{"platform": "NS", "title": "夢幻櫻樓閣", "date": "2026-12-03", "nsuid": SAKURA}]
    [same] = O.products({nintendo_taiwan.LIST_URL: entries})
    kept = S.merge_nintendo(found, raw, "NS", {"status": "ok", "taiwan": {nintendo_taiwan.LIST_URL}}, same, CHECKED)
    assert kept["NS"]["languages"] == read()
    [other] = O.products({nintendo_taiwan.LIST_URL: [{**entries[0], "nsuid": REVOLT}]})
    moved = S.merge_nintendo(found, raw, "NS", {"status": "ok", "taiwan": {nintendo_taiwan.LIST_URL}}, other, CHECKED)
    assert moved["NS"]["languages"] is None
    unread = S.merge_nintendo(found, raw, "NS", {"status": "unavailable", "taiwan": set()}, None, CHECKED)
    assert unread["NS"]["languages"] == read()
