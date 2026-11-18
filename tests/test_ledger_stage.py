"""The ledger follows the frontend copy while it exists, then the database-only source."""
from copy import deepcopy
import json

import pytest

from nintendo_backend.persistence import decode_master
from scripts import state_store
from scripts.load_state import load
from scripts.primary_publication import publish_primary
from scripts.publish import PublishError, _serialize
from scripts.state_store import STATE_PATH, STATE_SOURCE
from test_partial_publication import OLDER, partial_bundle, seed_prior
from test_primary_state import SOURCE, primary_git  # noqa: F401  (fixture)
from test_publish import bundle, NOW, CHECKED, RecordingClient


def files(db, store, source):
    return store.read_files(db, source)[1]


def retire(db, store):
    """What the backend retire step leaves behind: no frontend ledger, in the database and in Git."""
    head, _ = store.read_files(db, SOURCE)
    store.publish_patch(db, SOURCE, base_generation=head["generation"], changed={}, deleted=[STATE_PATH],
                        operation_id="retire-ledger")


def seed_state(db, store, master):
    """Put a ledger with the given master into the database-only source, encoded as the publisher does."""
    value = bundle()
    value["nintendo_master.json"] = master
    raw = _serialize(value)[STATE_PATH].encode()
    head, _ = store.read_files(db, STATE_SOURCE)
    store.publish_patch(db, STATE_SOURCE, base_generation=head["generation"], changed={STATE_PATH: raw},
                        operation_id="seed-" + str(head["generation"]), projection_required=False)
    return raw


def older_master():
    master = deepcopy(bundle()["nintendo_master.json"])
    master["generated_at"] = OLDER
    return master


@pytest.fixture
def primary_mode(primary_git, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RADAR_DATABASE_PRIMARY", "1")
    return primary_git


def test_frontend_stage_publishes_as_before_and_mirrors_the_same_bytes(primary_mode):
    db, store, repository, git, remote = primary_mode
    seed_state(db, store, older_master())  # the mirror lags until this run copies it
    result = publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                             trigger_source="manual", run_id="1234", repository=repository())
    frontend = files(db, store, SOURCE)
    assert result["ledger_stage"] == "frontend" and result["ledger"]["status"] == "written"
    assert files(db, store, STATE_SOURCE) == {STATE_PATH: frontend[STATE_PATH]}
    assert git(remote, "show", "main:" + STATE_PATH) == frontend[STATE_PATH].decode().strip()
    assert store.projection_pending(db, STATE_SOURCE) == []


def test_a_failed_mirror_only_warns(primary_mode, monkeypatch, capsys):
    db, store, repository, _, _ = primary_mode
    def broken(*args, **kwargs):
        from radar_core.storage.sql import StorageError
        raise StorageError("LEASE_BUSY", "busy")
    monkeypatch.setattr(state_store, "write_ledger", broken)
    result = publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                             trigger_source="manual", run_id="1234", repository=repository())
    assert result["status"] == "published" and result["ledger"] == {"status": "mirror_failed", "code": "LEASE_BUSY"}
    assert "::warning::IGDB ledger mirror" in capsys.readouterr().out


def test_database_stage_writes_the_ledger_first_and_keeps_it_out_of_the_frontend(primary_mode, tmp_path):
    db, store, repository, git, remote = primary_mode
    retire(db, store)
    seed_state(db, store, older_master())
    result = publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                             trigger_source="manual", run_id="1234", repository=repository())
    frontend = files(db, store, SOURCE)
    state = files(db, store, STATE_SOURCE)
    expected = _serialize(bundle())[STATE_PATH].encode()
    assert result["ledger_stage"] == "database" and result["ledger"]["status"] == "written"
    assert STATE_PATH not in frontend and state == {STATE_PATH: expected}
    assert json.loads(frontend["data/nintendo_refresh_status.json"])["status"] == "persisted"
    assert STATE_PATH not in git(remote, "ls-tree", "-r", "--name-only", "main").split()
    # The next run resumes from the database-only copy.
    output = tmp_path / "input/master.json"
    load(output)
    assert output.read_bytes() == expected


def test_database_stage_never_replaces_a_newer_ledger(primary_mode):
    db, store, repository, _, _ = primary_mode
    retire(db, store)
    newer = deepcopy(bundle()["nintendo_master.json"])
    newer["generated_at"] = "2026-10-04T16:05:00Z"
    raw = seed_state(db, store, newer)
    before = files(db, store, SOURCE)
    with pytest.raises(PublishError, match="newer_database_ledger_exists"):
        publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                        trigger_source="manual", run_id="1234", repository=repository())
    assert files(db, store, STATE_SOURCE) == {STATE_PATH: raw}
    assert files(db, store, SOURCE) == before


def test_database_stage_checks_retained_rows_against_the_database_ledger(primary_mode):
    db, store, repository, _, _ = primary_mode
    prior, current = partial_bundle()
    seed_prior(db, store, prior)
    retire(db, store)
    seed_state(db, store, prior["nintendo_master.json"])
    result = publish_primary(current, RecordingClient(), now=NOW, slot=CHECKED,
                             trigger_source="manual", run_id="1234", repository=repository())
    assert result["status"] == "published_partial" and result["ledger_stage"] == "database"
    master = decode_master(json.loads(files(db, store, STATE_SOURCE)[STATE_PATH]))
    assert master["games"]["igdb:415392"] == prior["nintendo_master.json"]["games"]["igdb:415392"]


@pytest.mark.parametrize("change", ["forged_retained", "previously_public"])
def test_database_stage_rejects_changed_retained_rows_before_publishing(primary_mode, change):
    db, store, repository, _, _ = primary_mode
    prior, current = partial_bundle()
    if change == "forged_retained":
        current["nintendo_master.json"]["games"]["igdb:415392"]["cover_image"] = "https://images.igdb.com/new.jpg"
    else:
        prior["nintendo_upcoming.json"]["games"].append({"id": "igdb:415392"})
    seed_prior(db, store, prior)
    retire(db, store)
    state_raw = seed_state(db, store, prior["nintendo_master.json"])
    before = files(db, store, SOURCE)
    with pytest.raises(PublishError, match="retained_candidate_changed_in_database"):
        publish_primary(current, RecordingClient(), now=NOW, slot=CHECKED,
                        trigger_source="manual", run_id="1234", repository=repository())
    # Rejected before the ledger was written.
    assert files(db, store, SOURCE) == before
    assert files(db, store, STATE_SOURCE) == {STATE_PATH: state_raw}


@pytest.mark.parametrize("read_stage", ["frontend", "database"])
def test_a_stage_change_during_the_run_stops_before_publishing(primary_mode, monkeypatch, read_stage):
    db, store, repository, _, _ = primary_mode
    if read_stage == "frontend":
        retire(db, store)  # retired after this run read its stage
    monkeypatch.setattr(state_store, "stage", lambda storage=None: read_stage)
    before = files(db, store, SOURCE)
    with pytest.raises(PublishError, match="ledger_stage_changed"):
        publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                        trigger_source="manual", run_id="1234", repository=repository())
    assert files(db, store, SOURCE) == before


def test_load_reads_the_frontend_copy_then_the_database_copy_and_never_nothing(primary_mode, tmp_path, monkeypatch):
    db, store, _, _, _ = primary_mode
    output = tmp_path / "master.json"
    frontend = files(db, store, SOURCE)[STATE_PATH]
    load(output)
    assert output.read_bytes() == frontend
    retire(db, store)
    newer = deepcopy(bundle()["nintendo_master.json"])
    newer["generated_at"] = "2026-10-04T16:05:00Z"
    raw = seed_state(db, store, newer)
    load(output)
    assert output.read_bytes() == raw
    head, _ = store.read_files(db, STATE_SOURCE)
    store.publish_patch(db, STATE_SOURCE, base_generation=head["generation"], changed={}, deleted=[STATE_PATH],
                        operation_id="lost-ledger", projection_required=False)
    with pytest.raises(KeyError):
        load(output)
    assert output.read_bytes() == raw


def test_write_ledger_retries_a_concurrent_write_and_rechecks(primary_mode, monkeypatch):
    db, store, _, _, _ = primary_mode
    from radar_core.storage import primary
    seen, raced, original = [], [], primary.write_files
    def race(*args, **kwargs):
        if not raced:
            raced.append(True)
            head, _ = store.read_files(db, STATE_SOURCE)
            store.publish_patch(db, STATE_SOURCE, base_generation=head["generation"],
                                changed={STATE_PATH: b'{"concurrent": true}\n'}, operation_id="concurrent",
                                projection_required=False)
        return original(*args, **kwargs)
    monkeypatch.setattr(primary, "write_files", race)
    result = state_store.write_ledger(b'{"mine": true}\n', check=lambda prior: seen.append(prior))
    assert result["status"] == "written"
    assert seen[-1] == b'{"concurrent": true}\n' and len(seen) == 2
    assert files(db, store, STATE_SOURCE) == {STATE_PATH: b'{"mine": true}\n'}


def test_git_mode_never_treats_a_missing_ledger_as_a_first_run(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_DATABASE_PRIMARY", "0")
    class Client:
        def head(self, repository):
            return "a" * 40, None
        def request(self, method, path):
            raise PublishError("github_request_failed", status=404)
    output = tmp_path / "master.json"
    with pytest.raises(ValueError, match="not in Git"):
        load(output, client=Client(), get=lambda *args, **kwargs: pytest.fail("no download"))
    assert not output.exists()
