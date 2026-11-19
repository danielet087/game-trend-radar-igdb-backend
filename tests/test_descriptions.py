from copy import deepcopy
from datetime import date

import pytest

from nintendo_backend.catalog import build_documents
from nintendo_backend.descriptions import PUBLIC_FIELDS, clean_text, describe, enrich_documents
from scripts.publish import FILES, PublishError, validate_bundle

CHECKED = "2026-10-03T16:05:00Z"


def game(**changes):
    return {"id": 12345, "name": "A family adventure", "category": 0, "hypes": 30,
        "platforms": [{"id": 130}], "summary": "Explore an island with your friends.",
        "url": "https://www.igdb.com/games/a-family-adventure",
        "release_dates": [{"id": 1, "platform": {"id": 130}, "category": 0,
                           "y": 2026, "m": 10, "d": 15, "region": 8}], **changes}


def documents(raw=None, previous=None, checked_at=CHECKED):
    return build_documents([raw or game()], start=date(2026, 10, 4), checked_at=checked_at, previous=previous)


def test_igdb_summary_then_storyline_are_public_with_original_source_and_no_private_state():
    master, public, _ = documents(game(summary="", storyline="A tale of friendship.", age_ratings=[{"synopsis": "A family game."}]))
    row = public["games"][0]
    assert row["short_description"] == row["short_description_en"] == "A tale of friendship."
    assert row["short_description_language"] == "en" and row["short_description_source"] == "igdb_original"
    assert row["description_source_url"] == game()["url"]
    assert all(key not in row for key in ("description_official", "description_lookup"))
    assert all(key in master["games"]["igdb:12345"] for key in PUBLIC_FIELDS)


def test_missing_official_chinese_displays_current_english_without_translation_state():
    master, public, _ = documents()
    report = enrich_documents(master, public)
    row = public["games"][0]
    assert row["short_description"] == row["short_description_en"] == game()["summary"]
    assert (row["short_description_language"], row["short_description_source"]) == ("en", "igdb_original")
    assert report["english_fallback_count"] == 1 and report["official_chinese_count"] == 0
    changed = game(summary="Discover a new island and recruit heroes.")
    resumed, new_public, _ = documents(changed, previous=master)
    enrich_documents(resumed, new_public)
    assert new_public["games"][0]["short_description"] == changed["summary"]
    assert all(not key.startswith("translation_") for key in report)


def test_legacy_machine_text_is_ignored_and_not_preserved_in_new_master():
    previous = {"games": {"igdb:12345": {"igdb_id": 12345,
        "short_description": "舊機器翻譯的中文。", "short_description_source": "igdb_machine_zh_tw",
        "description_translation": {"text": "舊機器翻譯的中文。"}}}}
    master, public, _ = documents(previous=previous)
    enrich_documents(master, public)
    row = public["games"][0]
    assert row["short_description"] == game()["summary"] and row["short_description_language"] == "en"
    assert "description_translation" not in row and "description_translation" not in master["games"][row["id"]]
    assert describe(game(), previous=previous["games"]["igdb:12345"])["short_description"] == game()["summary"]


def test_original_chinese_does_not_claim_platform_chinese_support():
    master, public, _ = documents(game(summary="与朋友一起探索这个岛屿。"))
    before = deepcopy(public["games"][0]["platform_language_support"])
    enrich_documents(master, public)
    row = public["games"][0]
    assert row["short_description"] == "與朋友一起探索這個島嶼。"
    assert row["short_description_language"] == "zh-TW" and row["short_description_en"] == ""
    assert row["platform_language_support"] == before and before["NS"]["languages"]["tchinese"] is None


def test_japanese_original_is_not_mislabeled_as_english():
    raw = game(summary="仲間と一緒に島を探索する冒険です。")
    master, public, _ = documents(raw)
    enrich_documents(master, public)
    row = public["games"][0]
    assert row["short_description_language"] == "ja" and row["short_description_en"] == ""
    assert row["short_description"] == raw["summary"]


def test_text_is_sanitized_and_empty_or_non_game_source_urls_are_not_invented():
    assert clean_text('<p>Hello &amp; friends.</p><script>bad()</script><p>Explore.</p>') == "Hello & friends.\n\nExplore."
    assert describe(game(summary=None, storyline=None))["short_description"] == ""
    assert describe(game(url="https://www.igdb.com.evil.example/games/test"))["description_source_url"] is None


def test_future_official_chinese_upgrades_unchanged_english_and_failed_refresh_keeps_it(monkeypatch):
    source = {"url": "https://ec.nintendo.com/HK/zh/titles/70010000000001", "platform": "NS", "titles": ["A family adventure"]}
    monkeypatch.setattr("nintendo_backend.descriptions.candidates", lambda *_args, **_kwargs: [source])
    monkeypatch.setattr("nintendo_backend.catalog.description_candidates", lambda *_args, **_kwargs: [source])

    class Official:
        request_count, found, fail = 0, False, False

        def lookup(self, raw, sources):
            self.request_count += 1
            if self.fail:
                raise RuntimeError("temporary optional-stage failure")
            if self.found:
                return {"text": "與朋友一起踏上全新的冒險旅程。", "source_url": source["url"],
                        "provider": "Nintendo", "official_title": "A family adventure"}
            return None

    client = Official()
    master, public, _ = documents()
    enrich_documents(master, public, official_client=client)
    assert public["games"][0]["short_description_source"] == "igdb_original"
    # Same English, a later official-page update must still be fetched; an
    # unreleased game is looked at again every three days.
    client.found = True
    resumed, new_public, _ = documents(previous=master, checked_at="2026-10-05T16:05:00Z")
    enrich_documents(resumed, new_public, official_client=client)
    assert client.request_count == 1
    resumed, new_public, _ = documents(previous=master, checked_at="2026-10-06T16:05:00Z")
    enrich_documents(resumed, new_public, official_client=client)
    assert client.request_count == 2
    assert new_public["games"][0]["short_description_source"] == "official_zh_tw"
    assert new_public["games"][0]["short_description_en"] == game()["summary"]
    assert new_public["games"][0]["description_source_url"] == source["url"]
    later, later_public, _ = documents(previous=resumed, checked_at="2026-10-08T16:05:00Z")
    enrich_documents(later, later_public, official_client=client)
    assert client.request_count == 2  # Existing official text refreshes weekly.
    client.fail = True
    later, later_public, _ = documents(previous=later, checked_at="2026-10-13T16:05:00Z")
    report = enrich_documents(later, later_public, official_client=client)
    assert client.request_count == 3 and later_public["games"][0]["short_description_source"] == "official_zh_tw"
    assert report["official_failure_count"] == 1
    # Revoked product mappings must not continue authorizing an official claim.
    row = describe(game(), previous=later["games"]["igdb:12345"], official_candidates=[])
    assert row["short_description"] == game()["summary"] and row["short_description_source"] == "igdb_original"


def test_english_roundtrip_passes_publisher_gate_and_unauthorized_public_chinese_is_rejected():
    from datetime import datetime, timezone
    master, public, status = documents()
    enrich_documents(master, public)
    bundle = dict(zip(FILES, (master, public, status)))
    now = datetime(2026, 10, 3, 16, 15, tzinfo=timezone.utc)
    validate_bundle(bundle, now=now)
    for mutation in (
        {"short_description": "捏造的簡介"},
        {"short_description": "舊機器翻譯。", "short_description_source": "igdb_machine_zh_tw", "short_description_language": "zh-TW"},
    ):
        corrupted = deepcopy(bundle)
        corrupted["nintendo_upcoming.json"]["games"][0].update(mutation)
        with pytest.raises(PublishError):
            validate_bundle(corrupted, now=now)


def test_reviewed_official_product_roundtrip_passes_gate_and_title_tampering_fails(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    import scripts.publish as publisher
    url = "https://ec.nintendo.com/HK/zh/titles/70010000000001"
    raw = game()
    registry = {"igdb:12345": {"igdb_id": 12345, "name_en": raw["name"], "platforms": {"NS": {
        "region": "hong_kong", "supported_languages": ["en"], "complete": True,
        "source": "Nintendo HK", "source_url": url, "checked_at": CHECKED,
        "evidence_type": "official_product_languages", "product_id": "70010000000001",
        "official_title": raw["name"]}}}}
    path = tmp_path / "languages.json"
    path.write_text(json.dumps({"schema_version": 1, "games": registry}), encoding="utf-8")
    monkeypatch.setattr(publisher, "NINTENDO_LANGUAGE_REGISTRY", path)

    class Official:
        request_count = 0

        def lookup(self, raw, sources):
            self.request_count += 1
            return {"text": "與朋友一起探索未知島嶼裡的祕密。", "source_url": url,
                    "provider": "Nintendo", "official_title": raw["name"]}

    master, public, status = build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED,
                                             language_registry=registry)
    enrich_documents(master, public, official_client=Official(), language_registry=registry)
    bundle = dict(zip(FILES, (master, public, status)))
    now = datetime(2026, 10, 3, 16, 15, tzinfo=timezone.utc)
    validate_bundle(bundle, now=now)
    assert public["games"][0]["short_description_source"] == "official_zh_tw"
    assert master["games"]["igdb:12345"]["description_official"]["source_language"] == "zh-Hant"
    assert public["games"][0]["platform_language_support"]["NS"]["languages"]["tchinese"] is False
    master["games"]["igdb:12345"]["description_official"]["official_title"] = "Other game Deluxe Edition"
    with pytest.raises(PublishError):
        validate_bundle(bundle, now=now)


def test_chinese_middle_dot_is_not_japanese_but_actual_kana_is():
    row = describe(game(summary="《真・三國無雙》帶來全新的戰場冒險。"))
    assert row["short_description_language"] == "zh-TW"
    assert row["short_description_en"] == ""
    assert describe(game(summary="《真・三國無雙》の冒険です。"))["short_description_language"] == "ja"


def test_independent_official_refresh_keeps_collection_time_and_uses_actual_check_time(monkeypatch):
    url = "https://ec.nintendo.com/HK/zh/titles/70010000000001"
    source = {"url": url, "platform": "NS", "titles": ["A family adventure"]}
    monkeypatch.setattr("nintendo_backend.descriptions.candidates", lambda *_args, **_kwargs: [source])
    checked = "2026-10-03T20:00:00Z"

    class Official:
        request_count = 0

        def lookup(self, raw, sources):
            self.request_count += 1
            return {"text": "與朋友一起探索未知島嶼。", "source_url": url,
                    "provider": "Nintendo", "official_title": raw["name"]}

    master, public, _ = documents()
    enrich_documents(master, public, official_client=Official(), checked_at=checked)
    row = master["games"]["igdb:12345"]
    assert master["generated_at"] == public["generated_at"] == CHECKED
    assert row["description_official"]["checked_at"] == checked
    assert row["description_lookup"]["checked_at"] == checked
    assert public["games"][0]["short_description_source"] == "official_zh_tw"


def test_verified_traditional_official_copy_preserves_proper_names(monkeypatch):
    url = "https://ec.nintendo.com/HK/zh/titles/70010000000001"
    source = {"url": url, "platform": "NS", "titles": ["A family adventure"]}
    monkeypatch.setattr("nintendo_backend.descriptions.candidates", lambda *_args, **_kwargs: [source])
    text = "居住在科奇里森林的少年林克，踏上全新的冒險。"

    class Official:
        request_count = 0

        def lookup(self, raw, sources):
            self.request_count += 1
            return {"text": text, "source_url": url, "provider": "Nintendo", "official_title": raw["name"]}

    master, public, _ = documents()
    enrich_documents(master, public, official_client=Official())
    assert public["games"][0]["short_description"] == text
    assert public["games"][0]["short_description_source"] == "official_zh_tw"
    assert master["games"]["igdb:12345"]["description_official"]["text"] == text
    assert "科奇裡" not in public["games"][0]["short_description"]


@pytest.mark.parametrize(("language", "original", "saved", "expected", "source"), [
    ("zh-Hant", "居住在科奇里森林的少年林克。", "居住在科奇裡森林的少年林克。", "居住在科奇里森林的少年林克。", "official_zh_tw"),
    ("zh-Hant", "居住在科奇里森林的少年林克。", "居住在科奇里森林的少年林克。", "居住在科奇里森林的少年林克。", "official_zh_tw"),
    ("zh-Hans", "与朋友一起探索这个岛屿。", "與朋友一起探索這個島嶼。", "與朋友一起探索這個島嶼。", "official_schinese_converted"),
])
def test_cached_official_script_handling_repairs_legacy_hant_and_keeps_hans_conversion(language, original, saved, expected, source):
    url = "https://ec.nintendo.com/HK/zh/titles/70010000000001"
    candidate = {"url": url, "platform": "NS", "titles": ["A family adventure"]}
    state = {"igdb_id": 12345, "name_en": game()["name"], "text": saved, "original_text": original,
             "source_url": url, "provider": "Nintendo", "official_title": game()["name"],
             "source_language": language, "checked_at": CHECKED}
    row = describe(game(), previous={"description_official": state}, official_candidates=[candidate])
    assert row["short_description"] == row["description_official"]["text"] == expected
    assert row["short_description_source"] == source
    invalid = deepcopy(state)
    invalid["text"] = "任意變更的其他介紹。"
    assert "description_official" not in describe(game(), previous={"description_official": invalid}, official_candidates=[candidate])


def test_failed_official_refresh_keeps_and_repairs_legacy_traditional_cache(monkeypatch):
    url = "https://ec.nintendo.com/HK/zh/titles/70010000000001"
    candidate = {"url": url, "platform": "NS", "titles": ["A family adventure"]}
    monkeypatch.setattr("nintendo_backend.descriptions.candidates", lambda *_args, **_kwargs: [candidate])
    monkeypatch.setattr("nintendo_backend.catalog.description_candidates", lambda *_args, **_kwargs: [candidate])
    master, public, _ = documents()
    state = {"igdb_id": 12345, "name_en": game()["name"], "text": "居住在科奇裡森林的少年林克。",
             "original_text": "居住在科奇里森林的少年林克。", "source_url": url, "provider": "Nintendo",
             "official_title": game()["name"], "source_language": "zh-Hant", "checked_at": CHECKED}
    master["games"]["igdb:12345"]["description_official"] = state

    class Unavailable:
        request_count = 0

        def lookup(self, raw, sources):
            self.request_count += 1
            raise RuntimeError("temporary optional-stage failure")

    enrich_documents(master, public, official_client=Unavailable(), checked_at="2026-10-11T16:05:00Z")
    assert public["games"][0]["short_description"] == state["original_text"]
    assert public["games"][0]["short_description_source"] == "official_zh_tw"
    assert master["games"]["igdb:12345"]["description_official"]["text"] == state["original_text"]


# A PS5 game whose IGDB-linked Taiwan PlayStation Store item has Chinese copy.
STORE_URL = "https://store.playstation.com/zh-hant-tw/concept/10012345"
STORE_TEXT = "在被遺忘的王國裡，年輕的騎士踏上旅程，尋找失落的龍之心。"


def ps5_game(day=(2026, 12, 3), **changes):
    return game(**{"platforms": [{"id": 167}],
                   "release_dates": [{"id": 2, "platform": {"id": 167}, "category": 0,
                                      "y": day[0], "m": day[1], "d": day[2], "region": 8}],
                   "websites": [{"url": "https://store.playstation.com/en-us/concept/10012345"}], **changes})


class StoreClient:
    def __init__(self, text=STORE_TEXT, name="龍之家族冒險"):
        self.text, self.name, self.request_count, self.urls = text, name, 0, []

    def lookup(self, raw, sources):
        self.request_count += 1
        self.urls.append([row["url"] for row in sources])
        result = {"text": self.text, "source_url": STORE_URL, "provider": "PlayStation",
                  "official_title": "A Family Adventure"}
        if self.name:
            result["official_name_zh"] = self.name
        return result


def test_linked_store_copy_and_chinese_title_fill_a_ps5_game():
    master, public, _ = documents(ps5_game())
    client = StoreClient()
    report = enrich_documents(master, public, official_client=client)
    row = public["games"][0]
    assert client.urls == [[STORE_URL]]
    assert row["short_description"] == STORE_TEXT
    assert row["short_description_source"] == "official_zh_tw"
    assert row["description_source_url"] == STORE_URL
    assert row["short_description_en"] == game()["summary"]
    assert (row["display_name"], row["name_zh_tw"], row["name_source"], row["name_url"]) == (
        "龍之家族冒險", "龍之家族冒險", "PlayStation Store 台灣", STORE_URL)
    assert row["name_evidence"]["provider"] == "playstation_store"
    assert report["store_name_count"] == 1
    # The next collection keeps the title while IGDB still links that item.
    later, later_public, _ = documents(ps5_game(), previous=master, checked_at="2026-10-04T16:05:00Z")
    assert later_public["games"][0]["name_zh_tw"] == "龍之家族冒險"
    unlinked, unlinked_public, _ = documents(ps5_game(websites=[]), previous=master, checked_at="2026-10-04T16:05:00Z")
    assert unlinked_public["games"][0]["name_zh_tw"] is None


def test_a_game_with_its_own_chinese_name_keeps_it():
    raw = ps5_game(game_localizations=[{"name": "家族冒險", "region": {"identifier": "zh-TW"}}])
    master, public, _ = documents(raw)
    enrich_documents(master, public, official_client=StoreClient())
    assert public["games"][0]["name_zh_tw"] == "家族冒險"
    assert public["games"][0]["short_description"] == STORE_TEXT


def test_simplified_store_copy_is_converted_and_labelled():
    master, public, _ = documents(ps5_game())
    enrich_documents(master, public, official_client=StoreClient(text="提前规划以智取敌人，并在充满黑暗生物的世界中前行。", name=None))
    row = public["games"][0]
    assert row["short_description"] == "提前規劃以智取敵人，並在充滿黑暗生物的世界中前行。"
    assert row["short_description_source"] == "official_schinese_converted"
    assert row["name_zh_tw"] is None


def test_unreleased_games_are_looked_at_every_three_days_and_released_ones_once():
    class Missing(StoreClient):
        def lookup(self, raw, sources):
            self.request_count += 1
            return None

    client = Missing()
    master, public, _ = documents(ps5_game())
    enrich_documents(master, public, official_client=client)
    for checked_at, expected in (("2026-10-05T16:05:00Z", 1), ("2026-10-06T16:05:00Z", 2)):
        master, public, _ = documents(ps5_game(), previous=master, checked_at=checked_at)
        enrich_documents(master, public, official_client=client)
        assert client.request_count == expected
    # Once it is out (2026-12-03), there are no further checks.
    for checked_at in ("2026-12-20T16:05:00Z", "2027-01-20T16:05:00Z"):
        master, public, _ = documents(ps5_game(), previous=master, checked_at=checked_at)
        enrich_documents(master, public, official_client=client)
        assert client.request_count == 2
    # A game first seen after its release is still checked once.
    client = Missing()
    master, public, _ = documents(ps5_game(day=(2026, 10, 10)), checked_at="2026-10-20T16:05:00Z")
    enrich_documents(master, public, official_client=client)
    master, public, _ = documents(ps5_game(day=(2026, 10, 10)), previous=master, checked_at="2026-10-30T16:05:00Z")
    enrich_documents(master, public, official_client=client)
    assert client.request_count == 1
