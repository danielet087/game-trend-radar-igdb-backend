from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from nintendo_backend.catalog import build_documents, normalize_game, release_record
from nintendo_backend.igdb import CollectionError

START = date(2026, 10, 4)
END = date(2027, 10, 4)
CHECKED = "2026-10-03T16:10:00Z"


def release(day="2027-03-01", platform=508, region=8, **changes):
    dt = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    row = {"id": platform, "platform": {"id": platform}, "date": int(dt.timestamp()),
           "category": 0, "y": dt.year, "m": dt.month, "d": dt.day, "region": region}
    row.update(changes)
    return row


def game(**changes):
    row = {"id": 123, "name": "A normal tactical game", "hypes": 30,
           "platforms": [{"id": 508, "name": "Nintendo Switch 2"}], "category": 0,
           "release_dates": [release()], "summary": "Lead your heroes through a tactical adventure.",
           "url": "https://www.igdb.com/games/example", "cover": {"url": "//images.igdb.com/igdb/image/upload/t_thumb/example.jpg"}}
    row.update(changes)
    return row


@pytest.mark.parametrize("hypes,status,eligible", [(30, "qualified", True), (29, "observe", False),
                                                   (20, "observe", False), (19, "below_threshold", False),
                                                   (0, "below_threshold", False), (None, "unknown", False)])
def test_hypes_threshold_unknown_and_actual_zero(hypes, status, eligible):
    row = normalize_game(game(hypes=hypes), START, END, CHECKED)
    assert row["hypes"] is hypes
    assert row["popularity_status"] == status
    assert row["calendar_eligible"] is eligible
    assert row["hypes_status"] == ("missing" if hypes is None else "available")


@pytest.mark.parametrize("bad", [-1, True, 30.0, "30"])
def test_invalid_hypes_not_coerced(bad):
    with pytest.raises(CollectionError, match="invalid_hypes"):
        normalize_game(game(hypes=bad), START, END, CHECKED)


def test_platform_dates_track_later_port_without_global_first_release():
    raw = game(platforms=[{"id": 130}, {"id": 508}, {"id": 6, "name": "PC"}],
               first_release_date=1000000000,
               release_dates=[release("2025-04-01", 130), release("2027-03-01", 508)])
    row = normalize_game(raw, START, END, CHECKED)
    published = row["releases"][0]
    assert [(item["date"], item["platform"]) for item in row["releases"]] == [("2027-03-01", "NS2")]
    assert published["region"] == "worldwide" and published["source"] == "IGDB"
    assert published["date_basis"] == "regional_calendar_day"
    assert [p["code"] for p in row["platforms"]] == ["NS", "NS2"]
    assert row["exclusivity"]["status"] == "multi_platform"


def test_two_nintendo_platforms_preserve_independent_dates():
    row = normalize_game(game(platforms=[{"id": 130}, {"id": 508}],
                              release_dates=[release("2027-01-01", 130), release("2027-02-01", 508)]), START, END, CHECKED)
    assert [(r["platform"], r["date"]) for r in row["releases"]] == [("NS", "2027-01-01"), ("NS2", "2027-02-01")]
    assert row["exclusivity"] == {"status": "listed_only", "platform": None, "source": "IGDB", "url": row["url"]}


def test_igdb_single_platform_is_listed_only_not_official_exclusive():
    row = normalize_game(game(), START, END, CHECKED)
    assert row["exclusivity"]["status"] == "listed_only"
    assert row["exclusivity"]["platform"] == "NS2"


def test_missing_and_conflicting_platform_metadata_never_proves_exclusive():
    for platforms in (None, [], [{"id": 130}]):
        row = normalize_game(game(platforms=platforms), START, END, CHECKED)
        assert row["platform_data_complete"] is False
        assert row["exclusivity"]["status"] == "unknown"


@pytest.mark.parametrize("category,y,m,expected", [(1, 2027, 3, "month"), (2, 2027, None, "year"),
                                                   (3, 2027, None, "quarter"), (7, None, None, "unknown")])
def test_imprecise_dates_retained_without_fake_calendar_day(category, y, m, expected):
    row = normalize_game(game(release_dates=[release(category=category, y=y, m=m, d=None)]), START, END, CHECKED)
    assert row["release_records"][0]["precision"] == expected
    assert row["release_records"][0]["date"] is None
    assert row["calendar_eligible"] is False
    assert row["possible_in_window"] is True


def test_new_date_format_and_region_override_legacy_fields():
    row = normalize_game(game(release_dates=[release(category=2, region=2,
                               date_format={"id": 1, "format": "YYYYMMMMDD"},
                               release_region={"id": 42, "region": "Asia"})]), START, END, CHECKED)
    assert row["releases"][0]["precision"] == "day"
    assert row["releases"][0]["region"] == "asia"


def test_igdb_calendar_timestamp_is_audited_in_taipei_without_inventing_taiwan_availability():
    raw = release("2027-03-01", region=8)
    original = deepcopy(raw)
    row = release_record(raw)
    assert row["date"] == row["source_date"] == row["timestamp_taipei_date"] == "2027-03-01"
    assert row["source_timestamp"] == raw["date"]
    assert row["source_region"] == "worldwide"
    assert row["time_zone"] == "Asia/Taipei"
    assert row["timezone_status"] == "same_calendar_day"
    assert row["taiwan_release_confirmed"] is False
    assert row["official_source_url"] is None
    assert raw == original


def test_date_parts_without_timestamp_remain_calendar_day_not_synthetic_instant():
    row = normalize_game(game(release_dates=[release("2027-03-01", date=None)]), START, END, CHECKED)
    published = row["releases"][0]
    assert published["date"] == published["source_date"] == "2027-03-01"
    assert published["source_timestamp"] is None and published["timestamp_taipei_date"] is None
    assert published["timezone_status"] == "date_only"
    assert published["taiwan_release_confirmed"] is False


def test_unknown_day_precision_timestamp_crossing_taipei_day_requires_review():
    stamp = int(datetime(2027, 3, 1, 20, tzinfo=timezone.utc).timestamp())
    row = normalize_game(game(release_dates=[release("2027-03-01", date=stamp)]), START, END, CHECKED)
    original = row["release_records"][0]
    assert original["date"] == original["source_date"] == "2027-03-01"
    assert original["timestamp_taipei_date"] == "2027-03-02"
    assert original["timezone_status"] == "requires_time_evidence"
    assert row["releases"] == [] and row["calendar_eligible"] is False


def official_registry(day="2027-03-02", platform="NS2"):
    return {"igdb:123": {"igdb_id": 123, "name_en": "A normal tactical game", "releases": [{
        "platform": platform, "date": day, "source": "Official Taiwan product page",
        "url": "https://www.nintendo.com/tw/schedule", "verified_source_date": "2027-03-01",
        "verified_at": CHECKED}]}}


def test_official_taiwan_date_prioritizes_regional_availability_and_keeps_igdb_original():
    row = normalize_game(game(), START, END, CHECKED, release_registry=official_registry())
    published = row["releases"][0]
    assert published["date"] == "2027-03-02"
    assert published["region"] == "taiwan"
    assert published["source_date"] == "2027-03-01" and published["source_region"] == "worldwide"
    assert published["timestamp_taipei_date"] == "2027-03-01"
    assert published["date_basis"] == "taiwan_official_calendar_day"
    assert published["timezone_status"] == "taiwan_official_date"
    assert published["taiwan_release_confirmed"] is True
    assert published["official_source_url"] == "https://www.nintendo.com/tw/schedule"
    assert row["release_records"][0]["date"] == "2027-03-01"


def test_official_taiwan_date_resolves_ambiguous_timestamp_without_faking_unlock_time():
    stamp = int(datetime(2027, 3, 1, 20, tzinfo=timezone.utc).timestamp())
    raw = game(release_dates=[release("2027-03-01", date=stamp)])
    row = normalize_game(raw, START, END, CHECKED, release_registry=official_registry())
    assert row["calendar_eligible"] is True and row["releases"][0]["date"] == "2027-03-02"
    assert row["release_records"][0]["timezone_status"] == "requires_time_evidence"
    assert row["releases"][0]["source_timestamp"] == stamp


def test_official_taiwan_date_binds_exact_identity_and_platform():
    for changes in ({"id": 124}, {"name": "A normal tactical game: Remastered"},
                    {"platforms": [{"id": 130}], "release_dates": [release(platform=130)]}):
        row = normalize_game(game(**changes), START, END, CHECKED, release_registry=official_registry())
        assert all(item["taiwan_release_confirmed"] is False for item in row["releases"])


def test_past_official_taiwan_release_does_not_fall_back_to_future_worldwide_date():
    row = normalize_game(game(), START, END, CHECKED, release_registry=official_registry("2026-09-01"))
    assert row["releases"] == [] and row["calendar_eligible"] is False


def test_changed_igdb_date_invalidates_old_official_snapshot_without_freezing_postponements():
    row = normalize_game(game(release_dates=[release("2027-04-01")]), START, END, CHECKED,
                         release_registry=official_registry())
    assert row["releases"][0]["date"] == "2027-04-01"
    assert row["releases"][0]["taiwan_release_confirmed"] is False
    aligned = normalize_game(game(release_dates=[release("2027-03-02")]), START, END, CHECKED,
                             release_registry=official_registry())
    assert aligned["releases"][0]["taiwan_release_confirmed"] is True


def test_regional_priority_and_equal_priority_conflict():
    row = normalize_game(game(release_dates=[release("2027-02-01", region=2),
                                            release("2027-03-01", region=8),
                                            release("2027-04-01", region=7)]), START, END, CHECKED)
    assert row["releases"][0]["date"] == "2027-04-01"
    row = normalize_game(game(release_dates=[release("2027-02-01", region=7),
                                            release("2027-03-01", region=7)]), START, END, CHECKED)
    assert not row["releases"]
    assert not row["calendar_eligible"]


def test_past_asia_date_does_not_use_future_us_to_fake_upcoming():
    row = normalize_game(game(release_dates=[release("2026-09-01", region=7),
                                            release("2027-03-01", region=2)]), START, END, CHECKED)
    assert not row["calendar_eligible"]


@pytest.mark.parametrize("day,expected", [("2026-10-03", False), ("2026-10-04", True),
                                         ("2027-10-03", True), ("2027-10-04", False)])
def test_exact_365_day_window(day, expected):
    row = normalize_game(game(release_dates=[release(day)]), START, END, CHECKED)
    assert row["calendar_eligible"] is expected


def test_mature_rating_and_lone_sexual_theme_do_not_exclude_game():
    ratings = [{"rating_category": {"rating": "M"}, "rating_content_descriptions": [{"description": "Nudity"}, {"description": "Sexual Themes"}]}]
    row = normalize_game(game(themes=[{"name": "Fantasy"}], age_ratings=ratings), START, END, CHECKED)
    assert row["sexual_content_screened"] is True
    assert row["calendar_eligible"] is True


def test_combined_primary_sexual_evidence_and_explicit_descriptor_exclude():
    cases = [game(themes=[{"name": "Erotic"}], summary="An explicit sexual game."),
             game(age_ratings=[{"rating_content_descriptions": [{"description_type": {"name": "Strong Sexual Content"}}]}])]
    for raw in cases:
        row = normalize_game(raw, START, END, CHECKED)
        assert row["content_screening"]["status"] == "excluded"
        assert row["calendar_eligible"] is False


def test_lone_erotic_theme_and_no_content_evidence_require_review():
    for raw in (game(themes=[{"name": "Erotic"}]), game(summary="", age_ratings=[])):
        row = normalize_game(raw, START, END, CHECKED)
        assert row["content_screening"]["status"] == "review"
        assert row["calendar_eligible"] is False


def test_dlc_cancelled_and_rumored_never_calendar():
    for changes in ({"category": 1}, {"status": 6}, {"game_status": {"status": "rumored"}}):
        row = normalize_game(game(**changes), START, END, CHECKED)
        assert row["calendar_eligible"] is False


def test_confirmed_chinese_locale_preferred_and_simplified_converted():
    for localized, expected in ([{"name": "遊戲測試", "region": {"identifier": "zh-TW"}}], "遊戲測試"), ([{"name": "游戏测试", "region": {"identifier": "zh-CN"}}], "遊戲測試"):
        row = normalize_game(game(game_localizations=localized), START, END, CHECKED)
        assert row["display_name"] == expected


def test_master_preserves_full_low_hypes_ledger_and_first_observed_time():
    raw = [game(), game(id=456, hypes=None)]
    previous = {"games": {"igdb:123": {"first_observed_at": "2026-01-01T00:00:00Z"}}}
    original = deepcopy(raw)
    master, public, status = build_documents(raw, start=START, checked_at=CHECKED, previous=previous)
    assert set(master["games"]) == {"igdb:123", "igdb:456"}
    assert [row["id"] for row in public["games"]] == ["igdb:123"]
    assert master["games"]["igdb:123"]["first_observed_at"] == "2026-01-01T00:00:00Z"
    assert status["popularity_counts"]["unknown"] == 1
    assert status["complete"] and public["source"]["complete"]
    assert raw == original
    assert "raw" not in public["games"][0]


def test_missing_previous_game_and_duplicate_identity_fail_full_snapshot():
    with pytest.raises(CollectionError, match="previous_game_lookup_incomplete"):
        build_documents([game()], start=START, checked_at=CHECKED,
                        previous={"games": {"igdb:456": {"igdb_id": 456}}})
    with pytest.raises(CollectionError, match="duplicate_game_identity"):
        build_documents([game(), game()], start=START, checked_at=CHECKED)


def test_official_urls_safe_and_igdb_cover_uses_larger_image():
    row = normalize_game(game(websites=[{"url": "javascript:alert(1)"},
                                       {"url": "https://evil.test/nintendo.com/game"},
                                       {"url": "https://www.nintendo.com/us/store/products/example-switch-2/"}]), START, END, CHECKED)
    assert row["nintendo_url"].startswith("https://www.nintendo.com/")
    assert "/t_cover_big/" in row["cover_image"]


def test_platform_added_later_records_history_and_preserves_original_release():
    old = normalize_game(game(platforms=[{"id": 130}], release_dates=[release("2027-01-01", 130)]), START, END, CHECKED)
    new = normalize_game(game(platforms=[{"id": 130}, {"id": 508}],
                              release_dates=[release("2027-01-01", 130), release("2027-03-01", 508)]),
                         START, END, "2026-10-04T16:00:00Z", previous=old)
    assert len(new["platform_history"]) == 2
    assert [row["date"] for row in new["releases"]] == ["2027-01-01", "2027-03-01"]
    unchanged = normalize_game(new["raw"], START, END, "2026-10-05T16:00:00Z", previous=new)
    assert len(unchanged["platform_history"]) == 2
