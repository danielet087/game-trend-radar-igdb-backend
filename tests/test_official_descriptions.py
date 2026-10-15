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
    times = iter([0, 0, 91])
    response = Response()
    client = descriptions.OfficialDescriptionClient(Session([response]), clock=lambda: next(times))
    assert client.lookup(GAME, pending()) is None and client.failure_count == 1 and response.closed


def test_chinese_middle_dot_does_not_invalidate_official_chinese_description():
    text = "《真・三國無雙》帶來全新的戰場冒險。"
    assert descriptions.parse_description(ld(text=text), NINTENDO, "A Game")["text"] == text
    assert descriptions.parse_description(ld(text="《真・三國無雙》の冒険です。"), NINTENDO, "A Game") is None
