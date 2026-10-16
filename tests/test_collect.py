from collections import deque
from datetime import date, datetime, timezone
import json

import pytest

from nintendo_backend.collect import GameLookupError, collect, load_existing, lookup_games, main
from nintendo_backend.chinese_names import SteamNameClient
from nintendo_backend.igdb import CollectionError


class Client:
    request_count, retry_count = 4, 0

    def __init__(self, results):
        self.results, self.calls = deque(results), []

    def verify_platforms(self):
        self.calls.append(("verify_platforms", ""))

    def query(self, endpoint, query):
        self.calls.append((endpoint, query))
        return self.results.popleft()


def test_collect_does_not_pre_filter_hypes_and_rehydrates_previous_games():
    raw1 = {"id": 1, "name": "One", "hypes": None, "platforms": [{"id": 508}], "category": 0}
    raw2 = {"id": 2, "name": "Two", "hypes": 0, "platforms": [{"id": 130}], "category": 0}
    api = Client([{"count": 1}, [{"id": 4, "game": 1, "platform": 508}], {"count": 1}, [raw1, raw2]])
    master, public, status = collect(api, start=date(2026, 10, 4), checked_at="2026-10-03T16:10:00Z",
                                   previous={"games": {"igdb:2": {"igdb_id": 2}}})
    assert set(master["games"]) == {"igdb:1", "igdb:2"}
    assert not public["games"]
    assert status["complete"] is True
    assert all("hypes >=" not in query for _, query in api.calls)
    assert "id = (1,2)" in api.calls[-1][1]
    assert "platform = (130,167,508)" in api.calls[1][1]
    assert status["source"]["platform_ids_verified"] == [130, 167, 508]


def test_collect_discovers_ps5_without_dropping_previous_nintendo_ledger():
    raw1 = {"id": 1, "name": "PS5 adventure", "hypes": 30, "platforms": [{"id": 167}], "category": 0,
        "summary": "An adventure.", "release_dates": [{"platform": {"id": 167}, "category": 0, "y": 2027, "m": 3, "d": 1, "region": 8}]}
    raw2 = {"id": 2, "name": "Previous NS", "hypes": 0, "platforms": [{"id": 130}], "category": 0}
    api = Client([{"count": 1}, [{"id": 4, "game": 1, "platform": 167}], {"count": 1}, [raw1, raw2]])
    master, public, status = collect(api, start=date(2026, 10, 4), checked_at="2026-10-04T03:00:00Z",
        previous={"games": {"igdb:2": {"igdb_id": 2}}})
    assert set(master["games"]) == {"igdb:1", "igdb:2"}
    assert public["games"][0]["releases"][0]["platform"] == "PS5"
    assert status["complete"] is True


def test_discovery_window_includes_previous_utc_year_that_is_new_year_in_taipei():
    stamp = int(datetime(2026, 12, 31, 16, tzinfo=timezone.utc).timestamp())
    raw = {"id": 1, "name": "New year adventure", "hypes": 30, "platforms": [{"id": 167}],
           "category": 0, "summary": "An adventure.", "release_dates": [
               {"platform": {"id": 167}, "category": 0, "date": stamp,
                "y": 2026, "m": 12, "d": 31, "region": 8}]}
    api = Client([{"count": 1}, [{"id": 4, "game": 1, "platform": 167}], {"count": 1}, [raw]])
    _, public, _ = collect(api, start=date(2027, 1, 1), checked_at="2026-12-31T16:00:00Z")
    query = api.calls[1][1]
    upper = int(datetime(2027, 12, 31, 16, tzinfo=timezone.utc).timestamp())
    assert f"date >= {stamp} & date < {upper}" in query
    assert public["games"][0]["releases"][0]["date"] == "2027-01-01"


def test_store_investigation_prioritizes_future_ps5_and_cannot_supply_public_proof():
    def release(platform, year):
        return {"platform": {"id": platform}, "category": 0, "y": year, "m": 3, "d": 1, "region": 8}
    raw1 = {"id": 1, "name": "Existing PS5 port", "hypes": 100, "platforms": [{"id": 167}, {"id": 508}],
        "category": 0, "summary": "An adventure.", "release_dates": [release(167, 2025), release(508, 2027)]}
    raw2 = {"id": 2, "name": "Future PS5 game", "hypes": 30, "platforms": [{"id": 167}],
        "category": 0, "summary": "An adventure.", "release_dates": [release(167, 2027)]}
    api = Client([{"count": 2}, [{"id": 4, "game": 1, "platform": 508}, {"id": 5, "game": 2, "platform": 167}],
                  {"count": 2}, [raw1, raw2]])
    class Store:
        def investigate(self, games):
            assert [row["id"] for row in games] == [2, 1]
            return {"evidence": {"date": "2027-09-01", "supported_languages": ["zh-Hant"]}}
    master, public, status = collect(api, start=date(2026, 10, 4), checked_at="2026-10-04T03:00:00Z", playstation_client=Store())
    future = next(row for row in public["games"] if row["igdb_id"] == 2)
    assert future["releases"][0]["date"] == "2027-03-01"
    assert future["platform_language_support"]["PS5"]["languages"]["tchinese"] is None
    assert status["playstation_investigation"]["review_only"] is True
    assert master["playstation_investigation"] == status["playstation_investigation"]


def test_store_investigation_resumes_unattempted_future_games_before_recent_high_hypes():
    def raw(id_, hypes):
        return {"id": id_, "name": "Game " + str(id_), "hypes": hypes, "platforms": [{"id": 167}],
            "category": 0, "summary": "An adventure.", "release_dates": [
                {"platform": {"id": 167}, "category": 0, "y": 2027, "m": 3, "d": 1, "region": 8}]}
    games = [raw(1, 100), raw(2, 30)]
    api = Client([{"count": 2}, [{"id": 4, "game": 1, "platform": 167}, {"id": 5, "game": 2, "platform": 167}], {"count": 2}, games])
    class Store:
        def investigate(self, rows):
            assert [row["id"] for row in rows] == [2, 1]
            return {"games": {}}
    previous = {"games": {}, "playstation_investigation": {"games": {
        "igdb:1": {"attempted_urls": ["https://store.playstation.com/en-tw/concept/10000001"],
                   "checked_at": "2026-10-04T02:00:00Z"},
        "igdb:2": {"attempted_urls": [], "checked_at": "2026-10-04T02:00:00Z", "status": "budget_exhausted"}}}}
    collect(api, start=date(2026, 10, 4), checked_at="2026-10-04T03:00:00Z", previous=previous, playstation_client=Store())


def test_missing_batch_game_prevents_complete_snapshot():
    api = Client([{"count": 1}, [{"id": 4, "game": 1, "platform": 508}], {"count": 1}, [], [], []])
    with pytest.raises(GameLookupError, match="game_lookup_incomplete") as error:
        collect(api, start=date(2026, 10, 4))
    assert error.value.lookup_details["missing_ids"] == [1]
    assert error.value.lookup_details["recovery_requests"] == 2


def test_short_game_batch_recovers_only_missing_ids_with_individual_final_check():
    one, two, three = ({"id": id_, "name": str(id_)} for id_ in (1, 2, 3))
    api = Client([[one, three], [], [two]])
    rows, report = lookup_games(api, [1, 2, 3])
    assert rows == [one, two, three]
    assert "id = (1,2,3)" in api.calls[0][1]
    assert all("id = (2)" in query for _, query in api.calls[1:])
    assert report["initial_missing_count"] == report["recovered_count"] == 1
    assert report["recovery_requests"] == 2


def test_recovery_uses_small_batches_and_has_twenty_request_collection_bound():
    api = Client([[]] * 21)
    with pytest.raises(GameLookupError, match="game_lookup_incomplete") as error:
        lookup_games(api, list(range(1, 101)))
    assert len(api.calls) == 21  # one normal batch plus twenty bounded recovery queries
    assert "id = (1,2,3,4,5,6,7,8,9,10)" in api.calls[1][1]
    assert "id = (1)" in api.calls[11][1]
    assert error.value.lookup_details["missing_ids"] == list(range(1, 101))
    assert error.value.lookup_details["recovery_requests"] == 20


def test_recovery_budget_is_shared_by_all_original_batches():
    api = Client([[{"id": id_} for id_ in range(1, 100)], [{"id": 100}], []])
    with pytest.raises(GameLookupError) as error:
        lookup_games(api, list(range(1, 102)), max_recovery_requests=1)
    assert len(api.calls) == 3
    assert error.value.lookup_details["missing_ids"] == [101]
    assert error.value.lookup_details["recovery_requests"] == 1


@pytest.mark.parametrize("rows,unexpected,duplicates,invalid", [
    ([{"id": 1}, {"id": 99}], [99], [], 0),
    ([{"id": 1}, {"id": 1}], [], [1], 0),
    ([{"id": "1"}], [], [], 1),
    ({"sensitive": "raw-response-must-not-be-exposed"}, [], [], 1),
])
def test_corrupt_game_batch_remains_fatal_with_safe_id_diagnostics(rows, unexpected, duplicates, invalid):
    api = Client([rows])
    with pytest.raises(GameLookupError) as error:
        lookup_games(api, [1, 2])
    assert len(api.calls) == 1
    details = error.value.lookup_details
    assert details["requested_ids"] == [1, 2]
    assert details["unexpected_ids"] == unexpected
    assert details["duplicate_ids"] == duplicates
    assert details["invalid_rows"] == invalid
    assert "raw-response-must-not-be-exposed" not in json.dumps(details)


@pytest.mark.parametrize("reason,http_status", [("collection_deadline", None), ("http_error", 429)])
def test_recovery_preserves_http_or_deadline_failure_reason(reason, http_status):
    class FailingClient(Client):
        def query(self, endpoint, query):
            if self.calls:
                raise CollectionError(reason, http_status)
            return super().query(endpoint, query)

    api = FailingClient([[]])
    with pytest.raises(GameLookupError) as error:
        lookup_games(api, [23])
    assert error.value.reason == reason
    assert error.value.http_status == http_status
    assert error.value.lookup_details["missing_ids"] == [23]
    assert error.value.lookup_details["recovery_requests"] == 1


def test_game_lookup_diagnostics_are_written_without_replacing_existing_catalog(tmp_path, monkeypatch, capsys):
    output = tmp_path / "output"
    output.mkdir()
    old = '{"old":"catalog"}\n'
    (output / "nintendo_upcoming.json").write_text(old)
    (output / "nintendo_master.json").write_text(old)
    monkeypatch.setenv("TWITCH_CLIENT_ID", "test-client")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "sensitive-test-secret")

    def fail(*args, **kwargs):
        raise GameLookupError(requested=[17, 23], missing=[23], unexpected=[91], recovery_requests=2)

    monkeypatch.setattr("nintendo_backend.collect.collect", fail)
    assert main(["--output-dir", str(output), "--existing", str(tmp_path / "missing.json")]) == 1
    assert (output / "nintendo_upcoming.json").read_text() == old
    assert (output / "nintendo_master.json").read_text() == old
    failure = json.loads((output / "nintendo_refresh_status.json").read_text())
    assert failure["complete"] is False
    assert failure["game_lookup"]["missing_ids"] == [23]
    assert failure["game_lookup"]["unexpected_ids"] == [91]
    assert "sensitive-test-secret" not in capsys.readouterr().out


def test_failure_preserves_existing_output_catalog_and_never_prints_secret(tmp_path, monkeypatch, capsys):
    output = tmp_path / "output"
    output.mkdir()
    old_public = '{"old":"catalog"}\n'
    old_master = '{"old":"master"}\n'
    (output / "nintendo_upcoming.json").write_text(old_public)
    (output / "nintendo_master.json").write_text(old_master)
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "sensitive-test-secret")
    assert main(["--output-dir", str(output), "--existing", str(tmp_path / "missing.json")]) == 1
    assert (output / "nintendo_upcoming.json").read_text() == old_public
    assert (output / "nintendo_master.json").read_text() == old_master
    failure = json.loads((output / "nintendo_refresh_status.json").read_text())
    assert failure["complete"] is False and failure["reason"] == "missing_credentials"
    assert "sensitive-test-secret" not in capsys.readouterr().out


def test_malformed_previous_state_not_silently_replaced(tmp_path):
    path = tmp_path / "master.json"
    path.write_text('{"schema_version":1,"games":{"igdb:1":{"igdb_id":2}}}')
    with pytest.raises(CollectionError, match="invalid_previous_master"):
        load_existing(path)


def test_collect_enriches_only_qualified_names_with_verified_steam_identity():
    class Response:
        status_code = 200

        def json(self):
            return {"789": {"success": True, "data": {"steam_appid": 789, "type": "game", "name": "Steam 繁中遊戲"}}}

    class Session:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return Response()

    raw = {"id": 1, "name": "One", "hypes": 30, "platforms": [{"id": 508}], "category": 0,
           "summary": "A tactical adventure.", "websites": [{"url": "https://store.steampowered.com/app/789/"}],
           "release_dates": [{"id": 9, "platform": {"id": 508}, "category": 0, "y": 2027, "m": 3, "d": 1, "region": 8}]}
    unqualified = {**raw, "id": 2, "name": "Two", "hypes": 29}
    api = Client([{"count": 2}, [{"id": 4, "game": 1, "platform": 508}, {"id": 5, "game": 2, "platform": 508}],
                  {"count": 2}, [raw, unqualified]])
    session = Session()
    names_api = SteamNameClient(session=session, clock=lambda: 0, sleep=lambda _: None)
    master, public, status = collect(api, start=date(2026, 10, 4), checked_at="2026-10-04T03:00:00Z", name_client=names_api)
    assert public["games"][0]["display_name"] == master["games"]["igdb:1"]["display_name"] == "Steam 繁中遊戲"
    assert session.calls == status["name_enrichment"]["steam_request_count"] == 1
    assert master["games"]["igdb:2"]["name_zh_tw"] is None
    assert "alternative_names.comment" in api.calls[-1][1] and "external_games.external_game_source.name" in api.calls[-1][1]


def test_invalid_name_registry_preserves_existing_outputs(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    old = '{"old":"catalog"}\n'
    (output / "nintendo_upcoming.json").write_text(old)
    registry = tmp_path / "chinese_names.json"
    registry.write_text('{"schema_version":1,"games":{"igdb:1":{"igdb_id":2}}}')
    assert main(["--output-dir", str(output), "--existing", str(tmp_path / "missing.json"),
                 "--chinese-names", str(registry)]) == 1
    assert (output / "nintendo_upcoming.json").read_text() == old
    assert json.loads((output / "nintendo_refresh_status.json").read_text())["reason"] == "invalid_chinese_name_registry"


def test_invalid_language_registry_preserves_existing_outputs_before_api_calls(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    old = '{"old":"catalog"}\n'
    (output / "nintendo_upcoming.json").write_text(old)
    path = tmp_path / "nintendo_languages.json"
    path.write_text('{"schema_version":1,"games":{"igdb:1":{"igdb_id":2}}}')
    assert main(["--output-dir", str(output), "--existing", str(tmp_path / "missing.json"),
                 "--nintendo-languages", str(path)]) == 1
    assert (output / "nintendo_upcoming.json").read_text() == old
    assert json.loads((output / "nintendo_refresh_status.json").read_text())["reason"] == "invalid_nintendo_language_registry"
