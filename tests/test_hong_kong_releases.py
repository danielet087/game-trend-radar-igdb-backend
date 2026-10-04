"""Nintendo HK calendar evidence keeps its region and cannot self-authorize."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json

import pytest

from nintendo_backend.catalog import build_documents, normalize_game
from nintendo_backend.igdb import CollectionError
from nintendo_backend.taiwan_releases import validate_registry
import scripts.publish as P
from scripts.publish import FILES, PublishError, publish, validate_bundle

START = date(2026, 10, 4)
END = date(2027, 10, 4)
CHECKED = "2026-10-03T16:05:00Z"
NOW = datetime(2026, 10, 3, 16, 15, tzinfo=timezone.utc)


def raw_game(platform=508, day="2027-03-01"):
    return {"id": 123, "name": "A regional adventure", "hypes": 30, "category": 0,
            "platforms": [{"id": platform}], "summary": "Explore an island with your friends.",
            "release_dates": [{"id": 10, "platform": {"id": platform}, "category": 0,
                               "date": int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()),
                               "region": 8}]}


def registry_document(platform="NS2", **changes):
    row = {"platform": platform, "region": "hong_kong", "date": "2027-03-01",
           "source": "Nintendo Hong Kong official release schedule",
           "url": "https://www.nintendo.com/hk/schedule",
           "verified_source_date": "2027-03-01", "verified_at": CHECKED}
    row.update(changes)
    return {"schema_version": 1, "games": {"igdb:123": {
        "igdb_id": 123, "name_en": "A regional adventure", "releases": [row]}}}


@pytest.mark.parametrize("platform,platform_id", [("NS", 130), ("NS2", 508)])
@pytest.mark.parametrize("url", [
    "https://nintendo.com.hk/schedule/", "https://www.nintendo.com.hk/software/example.html",
    "https://store.nintendo.com.hk/70010000000001", "https://www.nintendo.com/hk/schedule",
])
def test_reviewed_hk_native_day_uses_same_day_in_taipei_without_claiming_taiwan(platform, platform_id, url):
    document = registry_document(platform, url=url)
    original = deepcopy(document)
    registry = validate_registry(document)
    game = normalize_game(raw_game(platform_id), START, END, CHECKED, release_registry=registry)
    public = game["releases"][0]
    assert public["date"] == public["source_date"] == "2027-03-01"
    assert public["platform"] == platform and public["region"] == "hong_kong"
    assert public["source"] == "official_registry" and public["date_basis"] == "hong_kong_official_calendar_day"
    assert public["timezone_status"] == "hong_kong_official_date" and public["taiwan_release_confirmed"] is False
    assert public["time_zone"] == "Asia/Taipei" and public["official_release_time_utc"] is None
    assert public["official_source_url"] == url and public["official_verified_at"] == CHECKED
    assert public["source_region"] == "worldwide" and public["source_timestamp"] == raw_game(platform_id)["release_dates"][0]["date"]
    assert game["release_records"][0]["source"] == "IGDB" and game["calendar_eligible"] is True
    assert document == original


@pytest.mark.parametrize("changes", [
    {"platform": "PS5"}, {"region": "worldwide"}, {"region": None},
    {"url": "https://store.playstation.com/zh-hant-hk/concept/10000001"},
    {"url": "https://asia.sega.com/game/"}, {"url": "https://www.nintendo.com/tw/schedule"},
    {"url": "https://www.nintendo.com/us/store/example/"}, {"url": "https://www.nintendo.com/"},
    {"url": "https://www.nintendo.com/hk/"}, {"url": "https://www.nintendo.com/hk/index.html"},
    {"url": "https://www.nintendo.com/hk/../us/schedule"},
    {"url": "https://www.nintendo.com/hk/%2e%2e/tw/schedule"},
    {"url": "https://www.nintendo.com/hk/%2e%2e%2fus/schedule"},
    {"url": "https://www.nintendo.com.hk/"}, {"url": "https://www.nintendo.com.hk/index.html"},
    {"url": "https://www.nintendo.com.hk.evil.test/schedule/"},
    {"url": "https://ec.nintendo.com/HK/en/titles/70010000000001"},
    {"url": "http://www.nintendo.com.hk/schedule/"},
    {"url": "https://user@www.nintendo.com.hk/schedule/"},
    {"url": "https://www.nintendo.com.hk:443/schedule/"},
    {"url": "https://www.nintendo.com.hk/schedule/?date=2027-03-01"},
    {"url": "https://www.nintendo.com.hk/schedule/#game"},
    {"verified_at": "2026-10-04T04:00:00"},
])
def test_hk_registry_rejects_wrong_platform_region_url_and_unaudited_time(changes):
    with pytest.raises(CollectionError, match="invalid_taiwan_release_registry"):
        validate_registry(registry_document(**changes))


def test_hk_url_requires_explicit_hong_kong_region_and_ps5_cannot_enter_hk_backup():
    document = registry_document()
    document["games"]["igdb:123"]["releases"][0].pop("region")
    with pytest.raises(CollectionError, match="invalid_taiwan_release_registry"):
        validate_registry(document)
    document = registry_document(platform="PS5", url="https://store.playstation.com/zh-hant-hk/concept/10000001")
    game = normalize_game(raw_game(167), START, END, CHECKED, release_registry=document["games"])
    assert game["releases"][0]["source"] == "IGDB" and not game["releases"][0]["taiwan_release_confirmed"]


@pytest.mark.parametrize("source_region", [8, 99])
def test_taiwan_and_hk_snapshots_can_coexist_but_taiwan_native_day_wins(source_region):
    document = registry_document(date="2027-03-03")
    rows = document["games"]["igdb:123"]["releases"]
    rows.append({**rows[0], "region": "taiwan", "date": "2027-03-02",
                 "url": "https://www.nintendo.com/tw/schedule", "source": "Nintendo Taiwan official schedule"})
    raw = raw_game()
    raw["release_dates"][0]["region"] = source_region
    game = normalize_game(raw, START, END, CHECKED, release_registry=validate_registry(document))
    assert game["releases"][0]["region"] == "taiwan" and game["releases"][0]["date"] == "2027-03-02"
    assert game["releases"][0]["taiwan_release_confirmed"] is True
    assert {row["region"] for row in game["release_records"] if row["source"] == "official_registry"} == {"taiwan", "hong_kong"}
    assert all(row["source_date"] == "2027-03-01" for row in game["release_records"])
    rows.append(deepcopy(rows[0]))
    with pytest.raises(CollectionError, match="invalid_taiwan_release_registry"):
        validate_registry(document)


def test_hk_official_day_overrides_igdb_asia_without_arbitrary_day_offset():
    raw = raw_game()
    raw["release_dates"][0]["region"] = 7
    registry = validate_registry(registry_document(date="2027-03-03"))
    game = normalize_game(raw, START, END, CHECKED, release_registry=registry)
    assert game["releases"][0]["date"] == "2027-03-03" and game["releases"][0]["source_region"] == "asia"
    assert game["releases"][0]["official_release_time_utc"] is None


def test_igdb_hk_day_does_not_gain_official_hk_priority_over_asia():
    raw = raw_game()
    raw["release_dates"][0]["region"] = 7
    hk = deepcopy(raw_game(day="2027-03-02")["release_dates"][0])
    hk.update(id=11, release_region={"region": "Hong Kong"})
    raw["release_dates"].append(hk)
    unreviewed = normalize_game(raw, START, END, CHECKED)
    assert unreviewed["releases"][0]["region"] == "asia" and unreviewed["releases"][0]["date"] == "2027-03-01"
    reviewed = normalize_game(raw, START, END, CHECKED,
                              release_registry=validate_registry(registry_document(date="2027-03-03")))
    assert reviewed["releases"][0]["region"] == "hong_kong" and reviewed["releases"][0]["date"] == "2027-03-03"
    assert reviewed["releases"][0]["source"] == "official_registry"


def test_hk_snapshot_withdraws_on_changed_igdb_day_or_wrong_native_identity():
    registry = validate_registry(registry_document())
    changed = normalize_game(raw_game(day="2027-04-01"), START, END, CHECKED, release_registry=registry)
    assert changed["releases"][0]["source"] == "IGDB" and changed["releases"][0]["date"] == "2027-04-01"
    raw = raw_game()
    raw["name"] += " Deluxe"
    renamed = normalize_game(raw, START, END, CHECKED, release_registry=registry)
    wrong_platform = normalize_game(raw_game(130), START, END, CHECKED, release_registry=registry)
    assert renamed["releases"][0]["source"] == wrong_platform["releases"][0]["source"] == "IGDB"


def test_optional_actual_hk_instant_is_checked_in_taipei_and_keeps_igdb_timestamp():
    registry = validate_registry(registry_document(date="2027-03-02", release_time_utc="2027-03-01T16:00:00Z"))
    game = normalize_game(raw_game(), START, END, CHECKED, release_registry=registry)
    public = game["releases"][0]
    assert public["date"] == "2027-03-02" and public["official_release_time_utc"] == "2027-03-01T16:00:00Z"
    assert public["source_date"] == public["timestamp_taipei_date"] == "2027-03-01"
    assert public["source_timestamp"] == raw_game()["release_dates"][0]["date"]
    for stamp in ("2027-03-01T15:59:59Z", "2027-03-01T16:00:00", "2027-03-01"):
        with pytest.raises(CollectionError, match="invalid_taiwan_release_registry"):
            validate_registry(registry_document(date="2027-03-02", release_time_utc=stamp))


@pytest.fixture
def hk_bundle(tmp_path, monkeypatch):
    document = registry_document()
    path = tmp_path / "nintendo_release_dates.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(P, "TAIWAN_RELEASE_REGISTRY", path)
    docs = build_documents([raw_game()], start=START, checked_at=CHECKED,
                           release_registry=validate_registry(document))
    return dict(zip(FILES, docs)), path


def test_hk_publication_gate_rebuilds_the_repository_registry_from_another_directory(hk_bundle, tmp_path, monkeypatch):
    data, _ = hk_bundle
    original = deepcopy(data)
    outside = tmp_path / "another-directory"
    outside.mkdir()
    monkeypatch.chdir(outside)
    validate_bundle(data, now=NOW)
    assert data == original


class NoWritesClient:
    def __init__(self):
        self.calls = []

    def preflight(self, repo):
        self.calls.append(repo)
        raise AssertionError("Untrusted evidence reached remote write preparation")


@pytest.mark.parametrize("field,value", [
    ("date", "2027-03-02"), ("region", "taiwan"), ("source", "IGDB"),
    ("date_basis", "taiwan_official_calendar_day"), ("timezone_status", "same_calendar_day"),
    ("taiwan_release_confirmed", True), ("official_source_url", "https://www.nintendo.com/us/game/"),
    ("official_verified_at", "2026-10-03T16:05:00"), ("source_region", "hong_kong"),
    ("source_timestamp", 1000000000), ("official_release_time_utc", "2027-02-28T16:00:00Z"),
])
def test_matching_master_and_public_cannot_forge_or_relabel_hk_evidence(hk_bundle, field, value):
    data, _ = hk_bundle
    for row in (data["nintendo_master.json"]["games"]["igdb:123"], data["nintendo_upcoming.json"]["games"][0]):
        row["releases"][0][field] = value
    client = NoWritesClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


def test_hk_publication_cannot_self_authorize_after_trusted_registry_is_removed(hk_bundle):
    data, path = hk_bundle
    path.write_text('{"schema_version":1,"games":{}}')
    client = NoWritesClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []
