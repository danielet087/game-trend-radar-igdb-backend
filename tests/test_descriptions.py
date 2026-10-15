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
    # Same English, a later official-page update must still be fetched.
    client.found = True
    resumed, new_public, _ = documents(previous=master, checked_at="2026-10-04T16:05:00Z")
    enrich_documents(resumed, new_public, official_client=client)
    assert client.request_count == 2
    assert new_public["games"][0]["short_description_source"] == "official_zh_tw"
    assert new_public["games"][0]["short_description_en"] == game()["summary"]
    assert new_public["games"][0]["description_source_url"] == source["url"]
    later, later_public, _ = documents(previous=resumed, checked_at="2026-10-05T16:05:00Z")
    enrich_documents(later, later_public, official_client=client)
    assert client.request_count == 2  # Existing official text refreshes weekly.
    client.fail = True
    later, later_public, _ = documents(previous=later, checked_at="2026-10-11T16:05:00Z")
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
