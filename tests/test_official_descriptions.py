from copy import deepcopy
import json
from pathlib import Path

import pytest
import requests

from nintendo_backend import official_descriptions as descriptions

GAME = {"id": 123, "name": "A Game"}
NINTENDO = "https://ec.nintendo.com/HK/zh/titles/70010000123456"
PRODUCT = "HP0001-PPSA12345_00-BASEGAME00000000"
SONY = "https://store.playstation.com/zh-hant-tw/product/" + PRODUCT
TEXT = "踏上全新的冒險旅程，探索未知的世界。"
OCARINA_URL = "https://ec.nintendo.com/HK/zh/titles/70010000130795"
OCARINA = {"id": 405460, "name": "The Legend of Zelda: Ocarina of Time"}
OCARINA_TITLE = "薩爾達傳說 時之笛"


@pytest.fixture
def nintendo_hk_ocarina_html():
    return (Path(__file__).parent / "fixtures" / "nintendo_hk_ocarina_product.html").read_text(encoding="utf-8")


def registry(**changes):
    row = {"igdb_id": 123, "name_en": "A Game", "platforms": {"NS2": {
        "region": "hong_kong", "source_url": NINTENDO, "product_id": "70010000123456",
        "official_title": "遊戲甲"}}}
    row.update(changes)
    return {"igdb:123": row}


def ld(title="A Game", text=TEXT, **changes):
    document = {"@type": "VideoGame", "name": title, "description": text, **changes}
    return '<script type="application/ld+json">' + json.dumps(document).replace("</", "<\\/") + "</script>"


def sony_html(**changes):
    product = {"__typename": "Product", "id": PRODUCT, "name": "A Game (English/Chinese Ver.)",
               "invariantName": "A Game", "longDescription": "<p>" + TEXT + "</p>",
               "edition": {"type": "STANDARD", "name": ""}, "type": "GAME"}
    product.update(changes)
    document = {"args": {"productId": PRODUCT}, "overrides": {"locale": "zh-hant-tw"},
                "cache": {"Product:" + PRODUCT: product}}
    return '<script type="application/json">' + json.dumps(document) + "</script>"


def test_candidates_reuse_bound_exact_products_and_reviewed_chinese_aliases():
    source = registry()
    before = deepcopy(source)
    names = {"igdb:123": {"igdb_id": 123, "name_en": "A Game", "name_zh_tw": "遊戲甲", "url": NINTENDO}}
    result = descriptions.candidates(GAME, language_registry=source, name_registry=names)
    assert result == [
        {"url": NINTENDO, "platform": "NS2", "titles": ["A Game", "遊戲甲"]},
        {"url": NINTENDO.replace("/HK/", "/TW/"), "platform": "NS2", "titles": ["A Game", "遊戲甲"]},
    ]
    assert source == before
    assert descriptions.candidates({**GAME, "name": "Another Game"}, language_registry=source) == []
    assert descriptions.candidates({**GAME, "id": True}, language_registry=source) == []


def test_candidates_derive_precise_product_url_but_never_fetch_a_lineup_or_news_page():
    source = registry()
    row = source["igdb:123"]["platforms"]["NS2"]
    row["source_url"] = "https://www.nintendo.com/tw/games/switch2/lineup"
    row["region"] = "taiwan"
    result = descriptions.candidates(GAME, language_registry=source)
    assert [row["url"] for row in result] == [NINTENDO.replace("/HK/", "/TW/"), NINTENDO]
    assert all(row["platform"] == "NS2" and row["titles"] == ["A Game", "遊戲甲"] for row in result)
    row.pop("product_id")
    assert descriptions.candidates(GAME, language_registry=source) == []
    names = {"igdb:123": {"igdb_id": 123, "name_en": "A Game", "name_zh_tw": "遊戲甲",
                          "url": "https://www.nintendo.com/tw/topics/article/announcement"}}
    assert descriptions.candidates(GAME, language_registry=source, name_registry=names) == []


@pytest.mark.parametrize("changes", [
    {"edition_type": "deluxe"}, {"edition_label": "本體＋DLC"}, {"official_title": "A Game Deluxe Edition"},
])
def test_candidates_reject_bundled_or_deluxe_products(changes):
    source = registry()
    source["igdb:123"]["platforms"]["NS2"].update(changes)
    assert descriptions.candidates(GAME, language_registry=source) == []


def test_release_registry_requires_identity_and_regional_exact_product_url():
    source = registry(platforms={})
    source["igdb:123"]["releases"] = [{"platform": "PS5", "url": SONY.replace("zh-hant-tw", "en-tw"), "product_id": PRODUCT}]
    assert descriptions.candidates(GAME, release_registry=source)[0]["url"] == SONY
    source["igdb:123"]["releases"][0]["product_id"] = PRODUCT.replace("00000000", "99999999")
    assert descriptions.candidates(GAME, release_registry=source) == []


def test_reviewed_name_url_does_not_bypass_platform_or_edition_evidence():
    names = {"igdb:123": {"igdb_id": 123, "name_en": "A Game", "name_zh_tw": "遊戲甲", "url": SONY}}
    assert all(row["platform"] != "PS5" for row in descriptions.candidates(GAME, language_registry=registry(), name_registry=names))
    names["igdb:123"]["url"] = NINTENDO
    source = registry()
    source["igdb:123"]["platforms"]["NS2"]["edition_type"] = "deluxe"
    assert descriptions.candidates(GAME, language_registry=source, name_registry=names) == []


def test_public_title_match_preserves_exact_reviewed_identity():
    assert descriptions.title_matches("Ａ Game™ (English/Chinese Ver.)", ["A Game", "遊戲甲"])
    assert not descriptions.title_matches("A Game Remastered", ["A Game"])
    assert not descriptions.title_matches("A Game Deluxe Edition", ["A Game Deluxe Edition"])


def test_jsonld_exact_product_title_and_graph_are_accepted_and_html_is_cleaned():
    html = ld(title="A Game™", text="<p>踏上冒險</p><script>惡意內容</script><style>樣式</style><p>探索世界 &amp; 拯救同伴</p>")
    result = descriptions.parse_description(html, NINTENDO, "A Game")
    assert result == {"text": "踏上冒險\n\n探索世界 & 拯救同伴", "source_url": NINTENDO,
                      "provider": "Nintendo", "official_title": "A Game™"}
    graph = '<script type="application/ld+json">' + json.dumps({"@graph": [
        {"@type": "Organization", "name": "A Game", "description": "網站介紹"},
        {"@type": ["Product", "VideoGame"], "name": "遊戲甲", "description": TEXT}]}) + "</script>"
    assert descriptions.parse_description(graph, NINTENDO, "A Game", ["遊戲甲"])["text"] == TEXT


def test_observed_hk_product_fixture_yields_full_official_description_with_nbsp_platform(nintendo_hk_ocarina_html):
    result = descriptions.parse_description(nintendo_hk_ocarina_html, OCARINA_URL,
                                           OCARINA["name"], [OCARINA_TITLE], platform="NS2")
    assert result["source_url"] == OCARINA_URL
    assert result["provider"] == "Nintendo" and result["official_title"] == OCARINA_TITLE
    assert len(result["text"]) == 438
    assert result["text"].startswith("居住在科奇里森林的少年林克")
    assert "♦在Nintendo Switch 2上再度甦醒" in result["text"]
    assert "ZELDA NOTES" in result["text"]
    assert "跨越時空，少年將成為勇者" not in result["text"]  # Full JSON-LD beats the short meta blurb.


@pytest.mark.parametrize("page_platform", ["Nintendo Switch", "Nintendo\u00a0Switch", ["Nintendo Switch"]])
@pytest.mark.parametrize("description_text", [TEXT, "", "An English-only description of the old Switch version."])
def test_ns2_description_rejects_explicit_old_switch_version_even_if_title_and_og_match(page_platform, description_text):
    html = ld(title="遊戲甲", text=description_text, gamePlatform=page_platform)
    html += '<meta property="og:title" content="遊戲甲"><meta property="og:description" content="' + TEXT + '">'
    assert descriptions.parse_description(html, NINTENDO, "A Game", ["遊戲甲"], platform="NS2") is None


@pytest.mark.parametrize("page_platform", ["Nintendo Switch 2", "Nintendo Switch\u00a02", ["Nintendo Switch 2"]])
def test_ns2_description_accepts_exact_native_platform_with_regular_or_nbsp_spacing(page_platform):
    result = descriptions.parse_description(ld(gamePlatform=page_platform), NINTENDO, "A Game", platform="NS2")
    assert result["text"] == TEXT


def test_platform_unknown_keeps_legacy_behavior_and_ns_version_is_distinct():
    old = ld(gamePlatform="Nintendo Switch")
    assert descriptions.parse_description(old, NINTENDO, "A Game")["text"] == TEXT
    assert descriptions.parse_description(old, NINTENDO, "A Game", platform="NS")["text"] == TEXT
    assert descriptions.parse_description(ld(), NINTENDO, "A Game", platform="NS2")["text"] == TEXT


@pytest.mark.parametrize("html", [
    ld("Another Game"), ld("A Game Deluxe Edition"), ld(text="An exciting English-only game."),
    ld(text="新しい冒険の世界を探索しましょう"), ld(text="<script>中文都在程式裡</script>"),
    ld(**{"@type": "Article"}), ld() + ld(text="完全不同的遊戲簡介"),
])
def test_unrelated_english_japanese_edition_or_conflicting_content_is_not_used(html):
    assert descriptions.parse_description(html, NINTENDO, "A Game") is None


def test_only_exact_og_title_can_authenticate_og_description():
    html = '<meta property="og:title" content="遊戲甲"><meta name="og:description" content="' + TEXT + '">'
    assert descriptions.parse_description(html, NINTENDO, "A Game", ["遊戲甲"])["text"] == TEXT
    assert descriptions.parse_description(html, NINTENDO, "A Game") is None
    assert descriptions.parse_description(html + '<meta property="og:title" content="其他遊戲">', NINTENDO, "A Game", ["遊戲甲"]) is None


def test_sony_apollo_description_must_match_exact_regional_product_and_base_title():
    assert descriptions.parse_description(sony_html(), SONY, "A Game")["provider"] == "PlayStation"
    assert descriptions.parse_description(sony_html(), SONY.replace("zh-hant-tw", "zh-hant-hk"), "A Game") is None
    assert descriptions.parse_description(sony_html(), SONY.replace("BASEGAME", "DIFFGAME"), "A Game") is None
    assert descriptions.parse_description(sony_html(name="Other Game"), SONY, "A Game") is None
    assert descriptions.parse_description(sony_html(edition={"type": "DELUXE"}), SONY, "A Game") is None
    assert descriptions.parse_description(sony_html(storeDisplayClassification="ADD_ON"), SONY, "A Game") is None


@pytest.mark.parametrize("url", [
    NINTENDO.replace("https:", "http:"), NINTENDO.replace("ec.nintendo.com", "ec.nintendo.com.evil.test"),
    NINTENDO.replace("https://", "https://user@"), NINTENDO + "?id=other", NINTENDO + "#other",
    NINTENDO.replace("ec.nintendo.com", "ec.nintendo.com:443"), NINTENDO.replace("/HK/zh/", "/JP/ja/"),
    "https://www.nintendo.com/tw/topics/article/example", "https://www.nintendo.com/tw/games/switch2/lineup",
    "https://www.nintendo.com/tw/games/../topics/article/example", "https://www.nintendo.com/tw/games/%2e%2e/topics/article/example",
])
def test_unsafe_nonregional_or_nonproduct_urls_are_rejected(url):
    assert descriptions.parse_description(ld(), url, "A Game") is None


class Response:
    def __init__(self, text=None, status=200, url=NINTENDO, headers=None, chunks=None):
        self.status_code, self.url = status, url
        self.headers = headers or {}
        self.chunks = chunks if chunks is not None else [(text if text is not None else ld()).encode()]
        self.closed = False

    def iter_content(self, **kwargs):
        yield from self.chunks

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def pending(url=NINTENDO):
    return [{"url": url, "platform": "NS2", "titles": ["A Game"]}]


def test_client_has_no_redirects_and_caches_requests_and_leaves_language_evidence_untouched():
    response = Response()
    session = Session([response])
    client = descriptions.OfficialDescriptionClient(session)
    raw = deepcopy(GAME)
    assert client.lookup(raw, pending())["text"] == TEXT
    assert client.lookup(raw, pending())["text"] == TEXT
    assert session.calls[0][1] == {"timeout": 5, "allow_redirects": False, "stream": True}
    assert client.request_count == 1 and client.failure_count == 0 and response.closed
    assert raw == GAME


def test_client_tries_same_verified_hk_product_when_tw_product_is_unavailable(nintendo_hk_ocarina_html):
    tw_url = OCARINA_URL.replace("/HK/", "/TW/")
    language = {"igdb:405460": {"igdb_id": 405460, "name_en": OCARINA["name"], "platforms": {"NS2": {
        "region": "taiwan", "source_url": "https://www.nintendo.com/tw/games/switch2/lineup",
        "product_id": "70010000130795", "official_title": OCARINA_TITLE}}}}
    before = deepcopy(language)
    sources = descriptions.candidates(OCARINA, language_registry=language)
    assert [row["url"] for row in sources] == [tw_url, OCARINA_URL]
    session = Session([Response(status=404, url=tw_url), Response(text=nintendo_hk_ocarina_html, url=OCARINA_URL)])
    client = descriptions.OfficialDescriptionClient(session)
    result = client.lookup(OCARINA, sources)
    assert result["source_url"] == OCARINA_URL and len(result["text"]) == 438
    assert [url for url, _ in session.calls] == [tw_url, OCARINA_URL]
    assert client.request_count == 2 and client.failure_count == 1
    assert language == before  # Descriptions do not change region, releases, or language-support evidence.


def test_client_passes_candidate_platform_to_reject_same_name_old_switch_product():
    session = Session([Response(text=ld(gamePlatform="Nintendo Switch"))])
    client = descriptions.OfficialDescriptionClient(session)
    assert client.lookup(GAME, pending()) is None
    assert client.request_count == 1


@pytest.mark.parametrize("status,headers", [(429, {}), (503, {"Retry-After": "10"})])
def test_rate_limits_stop_entire_optional_stage(status, headers):
    response = Response(status=status, headers=headers)
    session = Session([response])
    client = descriptions.OfficialDescriptionClient(session)
    assert client.lookup(GAME, pending() + pending(SONY)) is None
    assert client.lookup(GAME, pending(SONY)) is None
    assert client.rate_limited and client.stopped and client.request_count == 1
    assert response.closed


def test_budgets_and_response_size_are_hard_limits():
    session = Session([Response(text="English only")])
    client = descriptions.OfficialDescriptionClient(session, max_requests=1)
    assert client.lookup(GAME, pending() + pending(SONY)) is None
    assert client.request_count == 1
    client = descriptions.OfficialDescriptionClient(Session([]), deadline_seconds=0)
    assert client.lookup(GAME, pending()) is None and client.request_count == 0
    for response in (Response(headers={"Content-Length": "2000001"}), Response(chunks=[b"x" * 1_000_000, b"x" * 1_000_001])):
        client = descriptions.OfficialDescriptionClient(Session([response]))
        assert client.lookup(GAME, pending()) is None and client.failure_count == 1 and response.closed


def test_redirects_errors_invalid_encoding_and_elapsed_stream_do_not_supply_text():
    for response in (Response(status=302), Response(url=SONY), Response(chunks=[b"\xff\xfe"])):
        client = descriptions.OfficialDescriptionClient(Session([response]))
        assert client.lookup(GAME, pending()) is None and client.failure_count == 1 and response.closed
    client = descriptions.OfficialDescriptionClient(Session([requests.Timeout()]))
    assert client.lookup(GAME, pending()) is None and client.failure_count == 1
    times = iter([0, 0, descriptions.DEADLINE_SECONDS + 1])
    response = Response()
    client = descriptions.OfficialDescriptionClient(Session([response]), clock=lambda: next(times))
    assert client.lookup(GAME, pending()) is None and client.failure_count == 1 and response.closed


def test_chinese_middle_dot_does_not_invalidate_official_chinese_description():
    text = "《真・三國無雙》帶來全新的戰場冒險。"
    assert descriptions.parse_description(ld(text=text), NINTENDO, "A Game")["text"] == text
    assert descriptions.parse_description(ld(text="《真・三國無雙》の冒険です。"), NINTENDO, "A Game") is None


# The Taiwan PlayStation Store item IGDB links to a PS5 game.
CONCEPT_ID = "10012345"
CONCEPT = "https://store.playstation.com/zh-hant-tw/concept/" + CONCEPT_ID
PS5_GAME = {"id": 321, "name": "Dragon Tale II", "platforms": [{"id": 167}],
            "websites": [{"url": "https://store.playstation.com/en-us/concept/" + CONCEPT_ID + "/"}],
            "alternative_names": [{"name": "Dragon Tale 2: The Return"}]}
STORY = "在被遺忘的王國裡，年輕的騎士踏上旅程，尋找失落的龍之心，並揭開王國沉睡百年的祕密。"


def concept_html(*, name="龍之傳說2", invariant="Dragon Tale 2", long=None, platforms=("PS4", "PS5"),
                 locale="zh-hant-tw", extra=()):
    """Sony's real layout: one concept row split over several documents."""
    long = STORY if long is None else long
    key = "Concept:" + CONCEPT_ID
    parts = [
        {"name": name},
        {"invariantName": invariant, "descriptions": [
            {"__typename": "Description", "type": "SHORT", "value": name},
            {"__typename": "Description", "type": "LONG", "subType": "NONE", "value": long}]},
        {"descriptions": [{"__typename": "Description", "type": "LONG", "value": long}],
         "platforms": list(platforms)},
        *extra,
    ]
    documents = [{"args": {"conceptId": CONCEPT_ID}, "overrides": {"locale": locale},
                  "cache": {key: {"__typename": "Concept", "id": CONCEPT_ID, **part}}} for part in parts]
    return "".join('<script type="application/json">' + json.dumps(document) + "</script>" for document in documents)


def test_linked_store_items_are_read_on_the_taiwan_chinese_storefront():
    assert descriptions.linked_playstation_urls(PS5_GAME) == [CONCEPT]
    product = "https://store.playstation.com/en-gb/product/" + PRODUCT
    assert descriptions.linked_playstation_urls({**PS5_GAME, "websites": [{"url": product}]}) == [SONY]
    # Only a PS5 game, and only an exact HTTPS store item.
    assert descriptions.linked_playstation_urls({**PS5_GAME, "platforms": [{"id": 48}]}) == []
    for url in ("http://store.playstation.com/en-us/concept/1", "https://store.playstation.com.evil.test/en-us/concept/1",
                "https://store.playstation.com/en-us/concept/1?x=1", "https://user@store.playstation.com/en-us/concept/1",
                "https://store.playstation.com/en-us/search/dragon", "https://www.playstation.com/en-us/games/dragon/"):
        assert descriptions.linked_playstation_urls({**PS5_GAME, "websites": [{"url": url}]}) == []


def test_candidates_try_the_linked_store_item_after_reviewed_products():
    assert descriptions.candidates(PS5_GAME) == [
        {"url": CONCEPT, "platform": "PS5", "titles": ["Dragon Tale II", "Dragon Tale 2: The Return"]}]
    reviewed = {"igdb:321": {"igdb_id": 321, "name_en": "Dragon Tale II", "platforms": {"PS5": {
        "region": "taiwan", "source_url": SONY, "product_id": PRODUCT, "official_title": "龍之傳說2"}}}}
    assert [row["url"] for row in descriptions.candidates(PS5_GAME, language_registry=reviewed)] == [SONY, CONCEPT]
    assert descriptions.candidates({**PS5_GAME, "websites": []}) == []


def test_sequel_numbers_match_in_roman_or_arabic_form():
    assert descriptions.title_matches("Dragon Tale 2", ["Dragon Tale II"])
    assert descriptions.title_matches("FINAL FANTASY VII", ["Final Fantasy 7"])
    assert not descriptions.title_matches("Dragon Tale 3", ["Dragon Tale II"])


def test_concept_page_gives_its_cleaned_description_and_chinese_title():
    result = descriptions.parse_description(concept_html(), CONCEPT, "Dragon Tale II", platform="PS5")
    assert result == {"text": STORY, "source_url": CONCEPT, "provider": "PlayStation",
                      "official_title": "Dragon Tale 2", "official_name_zh": "龍之傳說2"}
    # An English display name is the same game, but not a Chinese title.
    english = descriptions.parse_description(concept_html(name="Dragon Tale 2"), CONCEPT, "Dragon Tale II", platform="PS5")
    assert english["text"] == STORY and "official_name_zh" not in english
    # A concept that names no platform yet is not a different version.
    assert descriptions.parse_description(concept_html(platforms=()), CONCEPT, "Dragon Tale II", platform="PS5")


@pytest.mark.parametrize("changes", [
    {"invariant": "Dragon Tale 3"},                       # another game
    {"name": "Dragon Saga 2"},                            # an English title that disagrees
    {"platforms": ("PS4",)},                              # the PS4 version only
    {"locale": "zh-hant-hk"},                             # another storefront's documents
    {"long": "Explore a forgotten kingdom with friends."},  # English copy
    {"long": STORY + "\n本描述由人工智慧翻譯，與英文原版相比可能存在一些技術性錯誤。"},  # the store's own machine translation
    {"extra": ({"descriptions": [{"type": "LONG", "value": "另一段不同的介紹內容，與其他文件互相矛盾的說明文字。"}]},)},
])
def test_concept_page_must_be_the_same_ps5_game_with_one_chinese_description(changes):
    assert descriptions.parse_description(concept_html(**changes), CONCEPT, "Dragon Tale II", platform="PS5") is None


def test_the_exact_store_row_decides_even_when_the_page_summary_matches():
    html = concept_html(long="An English description only.") + ld(title="Dragon Tale 2", text=STORY)
    assert descriptions.parse_description(html, CONCEPT, "Dragon Tale II", platform="PS5") is None


def test_storefront_notes_are_removed_from_the_store_copy():
    long = ("​\"免費試玩《龍之傳說2》！<br/>購買《龍之傳說2》即可保留進度。<br/><br/>"
            + STORY + "<br/><br/>附註：<br/><br/>本作並非前作的續篇。<br/>-------------<br/>"
            "<b>挑戰強大的古龍</b><br/>在廣闊的大陸上與夥伴並肩作戰，擊敗沉睡的古龍。<br/><br/>"
            "※本產品包含數位特典。<br/>支援英文、日文、繁體中文及簡體中文。<br/>新增以下語言的完整配音：法語、德語。<br/>"
            "線上遊玩需要 PlayStation®Plus 訂閱服務（需單獨購買）。<br/>立即加入收藏清單。<br/>"
            "線上多人遊戲\"")
    text = descriptions.parse_description(concept_html(long=long), CONCEPT, "Dragon Tale II", platform="PS5")["text"]
    assert text == STORY + "\n\n挑戰強大的古龍\n在廣闊的大陸上與夥伴並肩作戰，擊敗沉睡的古龍。"
    copyright = STORY + " Copyright： © 2026 Dragon Studio. All rights reserved."
    assert descriptions.parse_description(concept_html(long=copyright), CONCEPT, "Dragon Tale II", platform="PS5")["text"] == STORY


@pytest.mark.parametrize("name,invariant,expected", [
    ("《雙人成行》PS4™ & PS5™", "It Takes Two PS4™ & PS5™", "雙人成行"),
    ("Tormented Souls 2 (受折磨的靈魂2)", "Tormented Souls 2", "受折磨的靈魂2"),
    ("夢幻遙控車 (Re-Volt)", "Re-Volt", "夢幻遙控車"),
    ("星際大戰：銀河賽車手™", "STAR WARS: Galactic Racer™", "星際大戰：銀河賽車手"),
    ("龍之傳說2 (簡體中文, 英文)", "Dragon Tale 2", "龍之傳說2"),
    ("Dragon Tale 2", "Dragon Tale 2", None),
    ("龍之傳說2 豪華版", "Dragon Tale 2", None),
    ("ドラゴン物語2", "Dragon Tale 2", None),
])
def test_store_chinese_title_drops_marks_labels_and_platforms(name, invariant, expected):
    assert descriptions.store_chinese_name(name, invariant) == expected
