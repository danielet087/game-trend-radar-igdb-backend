from copy import deepcopy
import json

import pytest

from nintendo_backend.igdb import CollectionError
from nintendo_backend.taiwan_releases import load_registry, official_releases, validate_registry


def document():
    return {"schema_version": 1, "games": {"igdb:123": {"igdb_id": 123, "name_en": "A tactical adventure",
            "releases": [{"platform": "NS2", "date": "2027-03-02", "source": "Nintendo Taiwan",
                          "url": "https://www.nintendo.com/tw/schedule", "verified_source_date": "2027-03-01",
                          "verified_at": "2026-10-04T04:00:00Z"}]}}}


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(schema_version=True),
    lambda value: value["games"]["igdb:123"].update(igdb_id=True),
    lambda value: value["games"]["igdb:123"].update(igdb_id=124),
    lambda value: value["games"]["igdb:123"].update(name_en=""),
    lambda value: value["games"]["igdb:123"].update(releases=[]),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(date="2027-02-30"),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(date="20270302"),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(verified_source_date="20270301"),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(verified_at="2026-10-04T04:00:00"),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(platform="PC"),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(source=""),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(url="http://www.nintendo.com/tw/schedule"),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(url="https://www.nintendo.com/us/store/"),
    lambda value: value["games"]["igdb:123"]["releases"][0].update(url="https://www.nintendo.com.evil.test/tw/schedule"),
    lambda value: value["games"]["igdb:123"]["releases"].append(deepcopy(value["games"]["igdb:123"]["releases"][0])),
])
def test_invalid_taiwan_registry_is_rejected(mutation):
    value = document()
    mutation(value)
    with pytest.raises(CollectionError, match="invalid_taiwan_release_registry"):
        validate_registry(value)


def test_registry_loading_and_exact_identity_binding(tmp_path):
    value = document()
    path = tmp_path / "dates.json"
    path.write_text(json.dumps(value))
    registry = load_registry(path)
    raw = {"id": 123, "name": "A tactical adventure"}
    assert official_releases(raw, registry) == value["games"]["igdb:123"]["releases"]
    assert official_releases({**raw, "id": 124}, registry) == []
    assert official_releases({**raw, "name": "A tactical adventure Remastered"}, registry) == []
    output = official_releases(raw, registry)
    output[0]["date"] = "2027-04-01"
    assert registry == value["games"]


def test_missing_or_broken_registry_fails_closed(tmp_path):
    for text in (None, "{broken}", '{"schema_version":1,"games":[]}'):
        path = tmp_path / "dates.json"
        if text is not None:
            path.write_text(text)
        with pytest.raises(CollectionError, match="invalid_taiwan_release_registry"):
            load_registry(path)


def test_trusted_repository_dates_use_taiwan_sources_and_exact_tw_regional_changes():
    registry = load_registry()
    assert len(registry) == 10
    assert registry["igdb:381222"]["releases"][0]["date"] == "2026-10-09"
    assert registry["igdb:378225"]["releases"][0]["date"] == "2027-01-08"
    assert registry["igdb:381235"]["releases"][0]["date"] == "2027-02-13"
