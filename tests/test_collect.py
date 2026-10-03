from collections import deque
from datetime import date
import json

import pytest

from nintendo_backend.collect import collect, load_existing, main
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


def test_missing_batch_game_prevents_complete_snapshot():
    api = Client([{"count": 1}, [{"id": 4, "game": 1, "platform": 508}], {"count": 1}, []])
    with pytest.raises(CollectionError, match="game_lookup_incomplete"):
        collect(api, start=date(2026, 10, 4))


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
