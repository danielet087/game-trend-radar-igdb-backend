from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from nintendo_backend.catalog import build_documents, normalize_game
from nintendo_backend.collect import collect
from nintendo_backend.igdb import CollectionError
from scripts.publish import FILES, PublishError, publish, validate_bundle

START = date(2026, 10, 4)
END = date(2027, 10, 4)
CHECKED = "2026-10-03T16:05:00Z"
NOW = datetime(2026, 10, 3, 16, 15, tzinfo=timezone.utc)


def raw_game(**changes):
    raw = {"id": 123, "name": "Tactical adventure", "hypes": 30, "category": 0,
           "summary": "Lead your heroes across an island.",
           "platforms": [{"id": 130}, {"id": 508}],
           "release_dates": [{"platform": {"id": 508}, "category": 0,
                              "y": 2027, "m": 3, "d": 1, "region": 8}]}
    raw.update(changes)
    return raw


def test_collect_requests_named_modes_and_platform_bound_multiplayer_evidence():
    raw = raw_game(game_modes=[{"id": 1, "name": "Single player"}, {"id": 3, "name": "Co-operative"}],
                   multiplayer_modes=[{"platform": {"id": 508}, "onlinecoop": True, "onlinecoopmax": 4}])

    class Client:
        request_count, retry_count = 4, 0

        def __init__(self):
            self.calls = []
            self.results = iter([{"count": 1}, [{"id": 77, "game": 123, "platform": 508}],
                                 {"count": 1}, [raw]])

        def verify_platforms(self):
            pass

        def query(self, endpoint, query):
            self.calls.append((endpoint, query))
            return next(self.results)

    client = Client()
    master, public, _ = collect(client, start=START, checked_at=CHECKED)
    fields = client.calls[-1][1].split(";", 1)[0].removeprefix("fields ").split(",")
    assert {"game_modes.id", "game_modes.name", "multiplayer_modes.platform.id",
            "multiplayer_modes.campaigncoop", "multiplayer_modes.dropin", "multiplayer_modes.lancoop",
            "multiplayer_modes.offlinecoop", "multiplayer_modes.offlinecoopmax", "multiplayer_modes.offlinemax",
            "multiplayer_modes.onlinecoop", "multiplayer_modes.onlinecoopmax", "multiplayer_modes.onlinemax",
            "multiplayer_modes.splitscreen", "multiplayer_modes.splitscreenonline"} <= set(fields)
    assert public["games"][0]["game_modes"] == raw["game_modes"]
    assert public["games"][0]["multiplayer_modes"] == raw["multiplayer_modes"]
    assert master["games"]["igdb:123"]["raw"] == raw


def test_modes_preserve_platform_scope_negative_evidence_and_unscoped_evidence():
    evidence = [{"platform": {"id": 130, "name": "Nintendo Switch"}, "onlinecoop": False, "onlinemax": 1},
                {"platform": {"id": 508}, "offlinecoop": True, "offlinecoopmax": 2, "splitscreen": True},
                {"platform": 6, "onlinecoop": True, "onlinecoopmax": 4},
                {"lancoop": True, "offlinemax": 0}]
    raw = raw_game(multiplayer_modes=evidence)
    original = deepcopy(raw)
    row = normalize_game(raw, START, END, CHECKED)
    assert row["multiplayer_modes"] == [
        {"platform": {"id": 130}, "onlinecoop": False, "onlinemax": 1},
        {"platform": {"id": 508}, "offlinecoop": True, "offlinecoopmax": 2, "splitscreen": True},
        {"platform": {"id": 6}, "onlinecoop": True, "onlinecoopmax": 4},
        {"lancoop": True, "offlinemax": 0}]
    assert raw == original
    assert row["calendar_eligible"] is True


def test_unknown_data_and_unsupported_modes_never_create_a_multiplayer_verdict():
    raw = raw_game(game_modes=[{"id": 42, "name": "Future unrecognized mode"}, 99],
                   multiplayer_modes=[{"platform": None, "onlinecoop": None, "onlinemax": None},
                                      {"campaigncoop": False, "unsupported": True}],
                   genres=[{"name": "Multiplayer"}], themes=[{"name": "Co-operative"}],
                   summary="A multiplayer cooperative adventure.")
    row = normalize_game(raw, START, END, CHECKED)
    assert row["game_modes"] == [{"id": 42, "name": "Future unrecognized mode"}, {"id": 99, "name": None}]
    assert row["multiplayer_modes"] == [{"campaigncoop": False}]
    assert "multiplayer" not in row
    empty = normalize_game(raw_game(), START, END, CHECKED)
    assert empty["game_modes"] == empty["multiplayer_modes"] == []


@pytest.mark.parametrize("changes,error", [
    ({"game_modes": {}}, "invalid_game_modes_metadata"),
    ({"game_modes": [{"id": True, "name": "Multiplayer"}]}, "invalid_game_modes_metadata"),
    ({"game_modes": [{"id": 2, "name": 2}]}, "invalid_game_modes_metadata"),
    ({"multiplayer_modes": {}}, "invalid_multiplayer_modes_metadata"),
    ({"multiplayer_modes": [2]}, "invalid_multiplayer_modes_metadata"),
    ({"multiplayer_modes": [{"platform": {"id": True}, "onlinecoop": True}]}, "invalid_multiplayer_modes_platform"),
    ({"multiplayer_modes": [{"platform": -1}]}, "invalid_multiplayer_modes_platform"),
    ({"multiplayer_modes": [{"onlinecoop": 1}]}, "invalid_multiplayer_modes_boolean"),
    ({"multiplayer_modes": [{"splitscreen": "true"}]}, "invalid_multiplayer_modes_boolean"),
    ({"multiplayer_modes": [{"onlinemax": True}]}, "invalid_multiplayer_modes_count"),
    ({"multiplayer_modes": [{"offlinemax": -1}]}, "invalid_multiplayer_modes_count"),
    ({"multiplayer_modes": [{"offlinecoopmax": 2.0}]}, "invalid_multiplayer_modes_count"),
])
def test_malformed_mode_metadata_fails_without_coercion(changes, error):
    with pytest.raises(CollectionError, match=error):
        normalize_game(raw_game(**changes), START, END, CHECKED)


def test_publication_rebuild_rejects_matching_master_and_public_forged_modes_before_writes():
    raw = raw_game(game_modes=[{"id": 1, "name": "Single player"}],
                   multiplayer_modes=[{"platform": {"id": 130}, "onlinemax": 1}])
    bundle = dict(zip(FILES, build_documents([raw], start=START, checked_at=CHECKED)))
    validate_bundle(bundle, now=NOW)

    class Client:
        def __init__(self):
            self.calls = []

        def preflight(self, repo):
            self.calls.append(repo)

        def commit_files(self, repo, files, message):
            self.calls.append(repo)

    for field, forged in (("game_modes", [{"id": 2, "name": "Multiplayer"}]),
                          ("multiplayer_modes", [{"platform": {"id": 508}, "onlinemax": 4}])):
        changed = deepcopy(bundle)
        changed["nintendo_master.json"]["games"]["igdb:123"][field] = forged
        changed["nintendo_upcoming.json"]["games"][0][field] = forged
        client = Client()
        with pytest.raises(PublishError, match="master_qualification_gate_failed"):
            publish(changed, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
        assert client.calls == []
