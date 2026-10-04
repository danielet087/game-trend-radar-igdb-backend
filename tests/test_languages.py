from copy import deepcopy
from datetime import date
import json

import pytest

from nintendo_backend.catalog import build_documents
from nintendo_backend.igdb import CollectionError
from nintendo_backend.languages import load_registry, platform_editions, platform_language_support, validate_registry

CHECKED = "2026-10-04T06:10:00Z"


def document():
    return {"schema_version": 1, "games": {"igdb:1": {"igdb_id": 1, "name_en": "A game", "platforms": {
        "NS": {"region": "taiwan", "supported_languages": ["en", "ja", "zh-Hant"], "complete": True,
               "source": "任天堂台灣官方商品語言資料", "source_url": "https://www.nintendo.com/tw/games/switch/lineup",
               "checked_at": CHECKED, "evidence_type": "official_product_languages"}}}}}


def test_language_evidence_is_bound_to_exact_identity_and_native_platform():
    registry = validate_registry(document())
    platforms = [{"code": "NS"}, {"code": "NS2"}]
    result = platform_language_support({"id": 1, "name": "A game"}, platforms, registry)
    assert result["NS"]["languages"] == {"tchinese": True, "schinese": False, "english": True, "chinese": True}
    assert result["NS"]["checked_at"] == CHECKED
    assert result["NS2"]["status"] == "unknown" and set(result["NS2"]["languages"].values()) == {None}
    for raw in ({"id": 2, "name": "A game"}, {"id": 1, "name": "A game remastered"}):
        assert all(row["status"] == "unknown" for row in platform_language_support(raw, platforms, registry).values())


def test_generic_chinese_preserves_unknown_script_variants_even_for_complete_official_list():
    doc = document()
    entry = doc["games"]["igdb:1"]["platforms"]["NS"]
    entry.update(region="united_kingdom", supported_languages=["en", "zh"],
                 source_url="https://www.nintendo.com/en-gb/Games/Nintendo-Switch-games/a-game.html")
    result = platform_language_support({"id": 1, "name": "A game"}, [{"code": "NS"}], validate_registry(doc))["NS"]
    assert result["status"] == "confirmed" and result["region"] == "united_kingdom"
    assert result["languages"] == {"tchinese": None, "schinese": None, "english": True, "chinese": True}
    assert result["supported_languages"] == [{"code": "en", "name": "英文"}, {"code": "zh", "name": "中文"}]


def test_chinese_icon_does_not_assert_english_or_chinese_variants():
    doc = document()
    doc["games"]["igdb:1"]["platforms"]["NS"].update(
        supported_languages=["zh"], complete=False, evidence_type="official_chinese_unspecified")
    support = platform_language_support({"id": 1, "name": "A game"}, [{"code": "NS"}], validate_registry(doc))["NS"]
    assert support["status"] == "partial" and support["languages"] == {
        "tchinese": None, "schinese": None, "english": None, "chinese": True}


@pytest.mark.parametrize("url", [
    "https://www.nintendo.com/au/games/nintendo-switch-2/a-game/",
    "https://ec.nintendo.com/AU/en/titles/70010000114443",
])
def test_australian_evidence_requires_matching_official_store_region(url):
    doc = document()
    row = doc["games"]["igdb:1"]["platforms"]["NS"]
    row.update(region="australia", source_url=url)
    support = platform_language_support({"id": 1, "name": "A game"}, [{"code": "NS"}], validate_registry(doc))["NS"]
    assert support["region"] == "australia" and support["languages"]["tchinese"] is True
    for region in ("taiwan", "hong_kong", "north_america"):
        row["region"] = region
        with pytest.raises(CollectionError, match="invalid_nintendo_language_registry"):
            validate_registry(doc)
    row["region"] = "australia"
    for wrong_url in ("https://www.nintendo.com/us/store/products/a-game/",
                      "https://ec.nintendo.com/HK/zh/titles/70010000114443",
                      "https://asia.sega.com/a-game/"):
        row["source_url"] = wrong_url
        with pytest.raises(CollectionError, match="invalid_nintendo_language_registry"):
            validate_registry(doc)


def test_complete_language_list_can_establish_non_support():
    doc = document()
    doc["games"]["igdb:1"]["platforms"]["NS"]["supported_languages"] = ["ja"]
    support = platform_language_support({"id": 1, "name": "A game"}, [{"code": "NS"}], validate_registry(doc))["NS"]
    assert support["languages"] == {"tchinese": False, "schinese": False, "english": False, "chinese": False}


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=True),
    lambda d: d["games"]["igdb:1"].update(igdb_id=2),
    lambda d: d["games"]["igdb:1"].update(platforms={"Steam": {}}),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(source_url="https://www.nintendo.com.evil.test/tw/games"),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(source_url="https://www.nintendo.com/us/store/products/a-game/"),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(source_url="https://user@www.nintendo.com/tw/games"),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(source_url="https://www.nintendo.com:443/tw/games"),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(checked_at="2026-10-04"),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(supported_languages=["en", "en"]),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(supported_languages=["made-up"]),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(complete=1),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(evidence_type="steam_languages"),
    lambda d: d["games"]["igdb:1"]["platforms"]["NS"].update(evidence_type="official_chinese_unspecified"),
])
def test_registry_rejects_unsafe_and_ambiguous_evidence(mutate):
    doc = document()
    mutate(doc)
    with pytest.raises(CollectionError, match="invalid_nintendo_language_registry"):
        validate_registry(doc)


def test_loader_rejects_missing_invalid_json_and_validates_repository_registry(tmp_path):
    path = tmp_path / "registry.json"
    with pytest.raises(CollectionError, match="invalid_nintendo_language_registry"):
        load_registry(path)
    path.write_text("not json")
    with pytest.raises(CollectionError, match="invalid_nintendo_language_registry"):
        load_registry(path)
    path.write_text(json.dumps(document()))
    assert load_registry(path) == document()["games"]
    assert isinstance(load_registry(), dict)


def test_catalog_ignores_steam_general_igdb_and_previous_language_claims():
    raw = {"id": 1, "name": "A game", "hypes": 30, "category": 0, "summary": "A family adventure.",
           "platforms": [{"id": 130}, {"id": 508}],
           "language_supports": [{"language": {"name": "Traditional Chinese"}}],
           "languages": {"tchinese": True}, "platform_language_support": {"NS2": {"status": "confirmed"}},
           "platform_editions": {"NS2": {"type": "deluxe", "label": "An unverified edition"}},
           "release_dates": [{"platform": {"id": 130}, "category": 0, "y": 2027, "m": 2, "d": 5, "region": 8}]}
    previous = {"games": {"igdb:1": {"platform_language_support": {"NS2": {"status": "confirmed"}}}}}
    master, public, _ = build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED, previous=previous,
                                         language_registry=validate_registry(document()))
    expected = master["games"]["igdb:1"]["platform_language_support"]
    assert public["games"][0]["platform_language_support"] == expected
    assert expected["NS"]["status"] == "confirmed" and expected["NS2"]["status"] == "unknown"
    assert expected["NS"]["checked_at"] == CHECKED
    assert expected["NS"]["supported_languages"][2] == {"code": "zh-Hant", "name": "繁體中文"}
    assert master["games"]["igdb:1"]["platform_editions"] == public["games"][0]["platform_editions"] == {}


def edition_document():
    doc = document()
    row = doc["games"]["igdb:1"]["platforms"]["NS"]
    row.update(edition_type="deluxe", edition_label="Deluxe 版", official_title="A game Deluxe Edition",
               product_id="70010000114443", identity_relation="base_game_included",
               identity_evidence="官方商品描述明確包含 A game 本體與追加內容。",
               identity_source_url="https://ec.nintendo.com/TW/zh/titles/70010000114443")
    return doc


def test_reviewed_editions_are_independent_of_languages_and_bound_to_native_identity():
    doc = edition_document()
    registry = validate_registry(doc)
    tracked = [{"code": "NS"}, {"code": "NS2"}]
    raw = {"id": 1, "name": "A game"}
    edition = platform_editions(raw, tracked, registry)
    assert edition == {"NS": {"type": "deluxe", "label": "Deluxe 版", "title": "A game Deluxe Edition",
                               "product_id": "70010000114443", "region": "taiwan",
                               "source_url": doc["games"]["igdb:1"]["platforms"]["NS"]["source_url"],
                               "checked_at": CHECKED}}
    assert platform_language_support(raw, tracked, registry) == platform_language_support(raw, tracked, validate_registry(document()))
    assert platform_editions(raw, [{"code": "NS2"}], registry) == {}
    for different in ({"id": 2, "name": "A game"}, {"id": 1, "name": "A game DLC"}):
        assert platform_editions(different, tracked, registry) == {}
    assert platform_editions(raw, tracked, validate_registry(document())) == {}


def test_publisher_base_inclusion_proof_can_differ_from_regional_product_url():
    doc = edition_document()
    row = doc["games"]["igdb:1"]["platforms"]["NS"]
    row.update(region="australia", source_url="https://www.nintendo.com/au/games/nintendo-switch-2/a-game/",
               identity_source_url="https://captown.capcom.com/en/theaters/dragonsdogma/57")
    approved = validate_registry(doc)
    result = platform_editions({"id": 1, "name": "A game"}, [{"code": "NS"}], approved)["NS"]
    assert result["source_url"] == row["source_url"]
    assert "identity_source_url" not in result  # Product link and inclusion proof serve different purposes.


@pytest.mark.parametrize("change", [
    {"edition_type": "cloud"}, {"edition_label": ""}, {"product_id": 70010000114443},
    {"identity_relation": "standalone_dlc"}, {"identity_evidence": ""},
    {"identity_source_url": None}, {"identity_source_url": "https://captown.capcom.com.evil.test/en/theaters/57"},
    {"identity_source_url": "https://ec.nintendo.com/HK/zh/titles/70010000114443"},
])
def test_editions_require_reviewed_base_inclusion_product_and_source(change):
    doc = edition_document()
    doc["games"]["igdb:1"]["platforms"]["NS"].update(change)
    with pytest.raises(CollectionError, match="invalid_nintendo_language_registry"):
        validate_registry(doc)
