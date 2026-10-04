from copy import deepcopy
import json

import pytest
import requests

from nintendo_backend import playstation as ps

PRODUCT = "HP0001-PPSA12345_00-BASEGAME00000000"
URL = "https://store.playstation.com/en-tw/product/" + PRODUCT


def metadata(**changes):
    product = {"id": PRODUCT, "__typename": "Product", "name": "A game (English/Chinese Ver.)",
        "invariantName": "A game", "platforms": ["PS5"], "concept": {"__ref": "Concept:10000001"},
        "edition": {"name": "", "type": "STANDARD"}, "storeDisplayClassification": "FULL_GAME",
        "topCategory": "GAME", "type": "GAME", "releaseDate": "2027-03-01T16:00:00Z",
        "screenLanguagesByPlatform": [
            {"platform": "PS4", "screenLanguages": [{"value": "Chinese (Simplified)"}]},
            {"platform": "PS5", "screenLanguages": [{"value": "Chinese (Traditional)"}, {"value": "English"}]}]}
    product.update(changes)
    return product


def html(product=None, locale="en-tw", extra=None):
    doc = {"args": {"productId": PRODUCT}, "overrides": {"locale": locale},
           "cache": {"Product:" + PRODUCT: product or metadata()}}
    body = '<script type="application/json">' + json.dumps(doc) + '</script>'
    if extra is not None:
        body += '<script type="application/json">' + json.dumps(extra) + '</script>'
    return body


def test_exact_native_store_metadata_uses_taiwan_day_and_ps5_languages():
    original = metadata()
    result = ps.parse_store_metadata(html(original), URL, "A game")
    assert result["date"] == "2027-03-02" and result["release_time_utc"] == "2027-03-01T16:00:00Z"
    assert result["region"] == "taiwan" and result["product_id"] == PRODUCT
    assert result["source_url"] == URL.replace("en-tw", "zh-hant-tw")
    assert result["supported_languages"] == ["zh-Hant", "en"] and result["complete"] is True
    assert original == metadata()


def test_concept_metadata_is_scoped_to_its_exact_product_and_concept():
    url = "https://store.playstation.com/en-tw/concept/10000001"
    assert ps.parse_store_metadata(html(), url, "A game")["product_id"] == PRODUCT
    assert ps.parse_store_metadata(html(), url.replace("10000001", "10000002"), "A game") is None


def test_generic_chinese_suffix_retains_unknown_scripts_when_language_metadata_is_empty():
    product = metadata(screenLanguagesByPlatform=[{"platform": "PS5", "screenLanguages": []}])
    result = ps.parse_store_metadata(html(product), URL, "A game")
    assert result["supported_languages"] == ["zh"] and result["complete"] is False
    assert result["evidence_type"] == "official_chinese_unspecified"


def test_explicit_chinese_script_names_in_exact_native_product_title_are_positive_partial_evidence():
    product = metadata(name="A game (Simplified Chinese, English, Korean, Japanese, Traditional Chinese)",
                       screenLanguagesByPlatform=[{"platform": "PS5", "screenLanguages": []}])
    result = ps.parse_store_metadata(html(product), URL, "A game")
    assert result["supported_languages"] == ["zh-Hans", "en", "ko", "ja", "zh-Hant"]
    assert result["complete"] is False and result["evidence_type"] == "official_product_languages"


def test_store_title_trademark_does_not_change_exact_game_identity():
    result = ps.parse_store_metadata(html(metadata(name="A game™ (English/Chinese Ver.)", invariantName="A game®")), URL, "A game")
    assert result["product_id"] == PRODUCT


def test_ps4_general_or_unsupported_language_names_do_not_become_ps5_complete_evidence():
    product = metadata(name="A game", screenLanguagesByPlatform=[
        {"platform": "PS4", "screenLanguages": ["Chinese (Traditional)"]}])
    result = ps.parse_store_metadata(html(product), URL, "A game")
    assert result["supported_languages"] == [] and result["complete"] is False
    product["screenLanguagesByPlatform"] = [{"platform": "PS5", "screenLanguages": ["Unknown Language"]}]
    assert ps.parse_store_metadata(html(product), URL, "A game")["complete"] is False


@pytest.mark.parametrize("changes", [
    {"name": "Other game"}, {"invariantName": "Other game"},
    {"name": "A game Deluxe Edition"}, {"name": "A game (Deluxe English Version)"},
    {"name": "A game (DLC Chinese)"}, {"name": "A game Remastered (English Ver.)"},
    {"edition": {"name": "Digital Deluxe Edition", "type": "DELUXE"}},
    {"edition": {"name": "", "type": "DELUXE"}},
    {"storeDisplayClassification": "ADD_ON"}, {"topCategory": "ADD_ON"},
    {"platforms": ["PS4"]}, {"id": "HP0001-PPSA12345_00-DIFFERENT0000000"},
])
def test_other_game_edition_addon_or_native_platform_is_rejected(changes):
    assert ps.parse_store_metadata(html(metadata(**changes)), URL, "A game") is None


def test_missing_timezone_never_becomes_official_calendar_day():
    result = ps.parse_store_metadata(html(metadata(releaseDate="2027-03-01T16:00:00")), URL, "A game")
    assert "date" not in result and "release_time_utc" not in result


def test_conflicting_exact_product_metadata_is_rejected_but_partial_editions_merge():
    extra = {"args": {"productId": PRODUCT}, "overrides": {"locale": "en-tw"},
        "cache": {"Product:" + PRODUCT: {"id": PRODUCT, "__typename": "Product", "releaseDate": "2027-03-02T16:00:00Z"}}}
    assert ps.parse_store_metadata(html(extra=extra), URL, "A game") is None
    extra["cache"]["Product:" + PRODUCT].pop("releaseDate")
    extra["cache"]["Product:" + PRODUCT]["edition"] = {"name": "", "features": ["A game"]}
    assert ps.parse_store_metadata(html(extra=extra), URL, "A game")["date"] == "2027-03-02"


@pytest.mark.parametrize("url", [
    URL.replace("en-tw", "en-us"), URL.replace("https:", "http:"),
    URL.replace("store.playstation.com", "store.playstation.com.evil.test"),
    URL.replace("https://", "https://user@"), URL + "?secret=value", URL + "#other",
    URL.replace("store.playstation.com", "store.playstation.com:443"),
])
def test_unsafe_or_nonregional_urls_are_not_evidence(url):
    assert ps.official_url(url) is None
    assert ps.parse_store_metadata(html(), url, "A game") is None


class Response:
    def __init__(self, text=None, status=200, url=URL):
        self.status_code, self.url = status, url
        self.text = text or html()
        self.content = self.text.encode()


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def game(**changes):
    raw = {"id": 123, "name": "A game", "platforms": [{"id": 167}],
           "websites": [{"url": URL.replace("en-tw", "en-us")}], "hypes": 40}
    raw.update(changes)
    return raw


def test_investigation_uses_exact_id_regional_request_caches_and_never_mutates_source():
    rows = [game(), game(id=124)]
    original = deepcopy(rows)
    session = Session([Response()])
    report = ps.PlayStationStoreClient(session).investigate(rows)
    assert rows == original and report["request_count"] == 1
    assert session.calls == [(URL, {"timeout": 5, "allow_redirects": False})]
    assert report["games"]["igdb:123"]["status"] == "confirmed_product"
    assert report["games"]["igdb:124"]["evidence"]["product_id"] == PRODUCT


def test_hong_kong_fallback_remains_hong_kong_evidence():
    hk_url = URL.replace("en-tw", "en-hk")
    session = Session([Response(status=404), Response(html(locale="en-hk"), url=hk_url)])
    report = ps.PlayStationStoreClient(session).investigate([game()])
    evidence = report["games"]["igdb:123"]["evidence"]
    assert evidence["region"] == "hong_kong" and "/zh-hant-hk/" in evidence["source_url"]


@pytest.mark.parametrize("response", [Response(status=429), Response(status=302),
    Response(url="https://www.example.com"), requests.Timeout("transport details")])
def test_failed_transport_redirect_limit_or_timeout_retains_unknown(response, monkeypatch):
    monkeypatch.setattr(ps, "MAX_PAGES", 1)
    report = ps.PlayStationStoreClient(Session([response])).investigate([game()])
    assert report["games"]["igdb:123"]["evidence"] is None
    assert report["request_count"] == 1


def test_bounded_request_and_elapsed_time_stop_investigation(monkeypatch):
    monkeypatch.setattr(ps, "MAX_PAGES", 1)
    rows = [game(), game(id=124, websites=[{"url": URL.replace(PRODUCT, "HP0001-PPSA12345_00-DIFFERENT0000000")}])]
    session = Session([Response()])
    report = ps.PlayStationStoreClient(session).investigate(rows)
    assert report["request_count"] == 1 and report["games"]["igdb:124"]["status"] == "budget_exhausted"
    ticks = iter([0, 91, 92])
    monkeypatch.setattr(ps.time, "monotonic", lambda: next(ticks))
    session = Session([])
    assert ps.PlayStationStoreClient(session).investigate([game()])["request_count"] == 0


def test_no_store_link_or_non_ps5_game_requires_no_request():
    session = Session([])
    report = ps.PlayStationStoreClient(session).investigate([game(websites=[]), game(id=124, platforms=[{"id": 130}])])
    assert session.calls == [] and set(report["games"]) == {"igdb:123"}
    assert report["games"]["igdb:123"]["status"] == "no_exact_store_url"


def test_store_429_stops_this_round_instead_of_trying_other_products_or_regions():
    session = Session([Response(status=429)])
    report = ps.PlayStationStoreClient(session).investigate([game(), game(id=124)])
    assert len(session.calls) == report["request_count"] == 1
    assert all(row["status"] == "rate_limited" and row["evidence"] is None for row in report["games"].values())


def test_actual_concept_args_and_vendor_codes_use_displayed_single_native_language_list():
    product = metadata(screenLanguagesByPlatform=[{"platform": "PS5", "screenLanguages": ["ch", "en", "zh"]}])
    doc = {"args": {"conceptId": "10000001"}, "overrides": {"locale": "en-tw"},
           "cache": {"Product:" + PRODUCT: product}}
    body = '<script type="application/json">' + json.dumps(doc) + '</script>' \
           '<dd data-qa="gameInfo#releaseInformation#subtitles-value">Chinese (Simplified), Chinese (Traditional), English</dd>'
    support = ps.parse_store_metadata(body, "https://store.playstation.com/en-tw/concept/10000001", "A game")
    assert support["complete"] is True and support["supported_languages"] == ["zh-Hans", "zh-Hant", "en"]
    product["platforms"] = ["PS4", "PS5"]
    doc["cache"]["Product:" + PRODUCT] = product
    body = '<script type="application/json">' + json.dumps(doc) + '</script>' \
           '<dd data-qa="gameInfo#releaseInformation#subtitles-value">Chinese (Traditional), English</dd>'
    support = ps.parse_store_metadata(body, "https://store.playstation.com/en-tw/concept/10000001", "A game")
    assert support["complete"] is False and support["supported_languages"] == ["zh"]


CONCEPT = "10000001"
CONCEPT_URL = "https://store.playstation.com/en-tw/concept/" + CONCEPT


def concept_document():
    return {"args": {"conceptId": CONCEPT}, "overrides": {"locale": "en-tw"},
            "cache": {"Concept:" + CONCEPT: {
                "id": CONCEPT, "__typename": "Concept", "name": "A game", "invariantName": "A game",
                "isAnnounce": True, "defaultProduct": None, "products": [],
                "releaseDate": {"type": "DAY_MONTH_YEAR", "value": "2026-10-13T18:00:00Z"},
                "compatibilityNoticesByPlatform": {"PS5": [{"targetPlatforms": ["PS5"]}]}}}}


def concept_html(doc=None, *, release="13/10/2026 06:00 PM UTC", platform="PS5 Version", extra=None):
    body = '<script type="application/json">' + json.dumps(doc or concept_document()) + '</script>'
    if extra is not None:
        body += '<script type="application/json">' + json.dumps(extra) + '</script>'
    if release is not None:
        body += '<span data-qa="mfe-game-title#release-date">' + release + '</span>'
    if platform is not None:
        body += '<h3 data-qa="mfe-compatibility-notices#notices#notice0#compatTextHeader">' + platform + '</h3>'
    return body


@pytest.mark.parametrize("release,display,date,platform", [
    ("2026-10-13T18:00:00Z", "13/10/2026 06:00 PM UTC", "2026-10-14", "PS5 Version"),
    ("2026-10-13T00:00:00Z", "13/10/2026 12:00 AM UTC", "2026-10-13", "PS5版本"),
])
def test_announced_native_ps5_concept_converts_verified_instant_without_inventing_product_or_languages(
        release, display, date, platform):
    doc = concept_document()
    doc["cache"]["Concept:" + CONCEPT]["releaseDate"]["value"] = release
    original = deepcopy(doc)
    result = ps.parse_store_metadata(concept_html(doc, release=display, platform=platform), CONCEPT_URL, "A game")
    assert result is not None
    assert result["concept_id"] == CONCEPT and result["product_id"] is None
    assert result["date"] == date and result["release_time_utc"] == release
    assert result["region"] == "taiwan"
    assert result["source_url"] == CONCEPT_URL.replace("en-tw", "zh-hant-tw")
    assert result["evidence_type"] == "official_concept_release_time"
    assert result["supported_languages"] == [] and result["complete"] is False
    assert doc == original


@pytest.mark.parametrize("mutation", [
    lambda doc: doc["args"].update(conceptId="10000002"),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(id="10000002"),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(__typename="Product"),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(name="Other game"),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(invariantName="Other game"),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(isAnnounce=False),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(defaultProduct={"__ref": "Product:" + PRODUCT}),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(products=[{"__ref": "Product:" + PRODUCT}]),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(compatibilityNoticesByPlatform={"PS4": [{"targetPlatforms": ["PS4"]}]}),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(compatibilityNoticesByPlatform={"PS5": [{"targetPlatforms": ["PS4"]}]}),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(compatibilityNoticesByPlatform={"PS5": [{"targetPlatforms": ["PS5", "PS4"]}]}),
    lambda doc: doc["cache"]["Concept:" + CONCEPT].update(compatibilityNoticesByPlatform={"PS5": [{"targetPlatforms": ["PS5"]}], "PC": [{"targetPlatforms": ["PC"]}]}),
])
def test_announced_concept_requires_exact_announced_game_identity_and_single_native_ps5_version(mutation):
    doc = concept_document()
    mutation(doc)
    assert ps.parse_store_metadata(concept_html(doc), CONCEPT_URL, "A game") is None


@pytest.mark.parametrize("release", [None, "PS4 Version", "PS5 and PS4 Version"])
def test_concept_native_ps5_cache_alone_does_not_replace_displayed_exact_version(release):
    assert ps.parse_store_metadata(concept_html(platform=release), CONCEPT_URL, "A game") is None


def test_generic_site_ps5_wording_cannot_authenticate_concept_platform():
    doc = concept_document()
    doc["cache"]["Concept:" + CONCEPT].pop("compatibilityNoticesByPlatform")
    body = concept_html(doc, platform=None) + "<nav>PS5 Version</nav>"
    assert ps.parse_store_metadata(body, CONCEPT_URL, "A game") is None


@pytest.mark.parametrize("value,type_,display", [
    ("2026-10-13T18:00:00", "DAY_MONTH_YEAR", "13/10/2026 06:00 PM UTC"),
    ("2026-10-13", "DAY_MONTH_YEAR", "13/10/2026 06:00 PM UTC"),
    ("2026-10-13T18:00:00Z", "MONTH_YEAR", "13/10/2026 06:00 PM UTC"),
    ("2026-10-13T18:00:00Z", "DAY_MONTH_YEAR", None),
    ("2026-10-13T18:00:00Z", "DAY_MONTH_YEAR", "13/10/2026 05:00 PM UTC"),
    ("2026-10-13T18:00:00Z", "DAY_MONTH_YEAR", "13/10/2026"),
])
def test_concept_release_requires_precise_aware_cache_instant_matching_rendered_utc(value, type_, display):
    doc = concept_document()
    doc["cache"]["Concept:" + CONCEPT]["releaseDate"] = {"type": type_, "value": value}
    assert ps.parse_store_metadata(concept_html(doc, release=display), CONCEPT_URL, "A game") is None


def test_conflicting_concept_release_caches_are_rejected():
    extra = concept_document()
    extra["cache"]["Concept:" + CONCEPT]["releaseDate"]["value"] = "2026-10-14T18:00:00Z"
    assert ps.parse_store_metadata(concept_html(extra=extra), CONCEPT_URL, "A game") is None


def test_concept_release_does_not_bypass_ambiguous_existing_product_identity():
    doc = concept_document()
    other_id = "HP0001-PPSA12345_00-DIFFERENT0000000"
    doc["cache"]["Product:" + PRODUCT] = metadata()
    doc["cache"]["Product:" + other_id] = metadata(id=other_id)
    assert ps.parse_store_metadata(concept_html(doc), CONCEPT_URL, "A game") is None
