import json
import subprocess

import pytest

from scripts.load_state import load
from scripts.primary_publication import publish_primary
from scripts.publish import FRONTEND, PublishError, _serialize
from scripts.guard import decide
from test_publish import bundle, NOW, CHECKED, RecordingClient

SOURCE = FRONTEND + "@main"


def test_database_failure_never_fetches_git_or_overwrites_previous_input(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_DATABASE_PRIMARY", "1")
    output = tmp_path / "master.json"
    output.write_text("old input")
    class Broken:
        def read_files(self, *args):
            raise RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        load(output, storage=Broken(), get=lambda *args, **kwargs: pytest.fail("Git fallback"))
    assert output.read_text() == "old input"


def test_load_preserves_complete_encoded_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_DATABASE_PRIMARY", "1")
    from nintendo_backend.persistence import encode_master
    raw = (json.dumps(encode_master(bundle()["nintendo_master.json"]), ensure_ascii=False) + "\n").encode()
    class Store:
        def read_files(self, repository, paths):
            return {}, {"data/nintendo_master.json": raw}
    output = tmp_path / "input/master.json"
    load(output, storage=Store())
    assert output.read_bytes() == raw


@pytest.fixture
def primary_git(tmp_path, monkeypatch):
    from radar_core.storage import documents, primary, primary_store
    from radar_core.storage.sql import SqliteClient
    from radar_core.publication import SubprocessGitRepository
    database = SqliteClient()
    documents.initialize(database)
    initial = {path: raw.encode() for path, raw in _serialize(bundle()).items()}
    initial["data/another-collector.json"] = b'{"authoritative":"database"}\n'
    primary_store.bootstrap_primary(database, SOURCE, initial, "a" * 40)
    def git(root, *args):
        return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
                              cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    remote, work = tmp_path / "remote.git", tmp_path / "frontend"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    git(tmp_path, "init", "--initial-branch=main", str(work))
    (work / "data").mkdir()
    (work / "data/another-collector.json").write_text('{"stale":"git"}')
    git(work, "add", ".")
    git(work, "commit", "-m", "initial")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "origin", "HEAD:main")
    monkeypatch.setenv("RADAR_DATABASE_PRIMARY", "0")
    def repository():
        return SubprocessGitRepository(work, disposable_checkout=True,
                                       primary=primary.GitPrimary(SOURCE, db=database))
    yield database, primary_store, repository, git, remote
    database.close()


def test_atomic_three_file_commit_and_whole_database_projection_have_real_ack(primary_git):
    db, store, repository, git, remote = primary_git
    result = publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                             trigger_source="manual", run_id="1234", repository=repository())
    head, files = store.read_files(db, SOURCE)
    status = json.loads(files["data/nintendo_refresh_status.json"])
    assert status["status"] == "persisted" and status["complete"] is True
    assert status["storage_backend"] == "turso" and "frontend_commit" not in status
    assert status["backend_mirror"] == result["backend_mirror"] == "not_required"
    assert status["authoritative_source"] == result["authoritative_source"] == SOURCE
    assert result["frontend_commit"] == git(remote, "rev-parse", "main")
    for path, raw in files.items():
        assert git(remote, "show", "main:" + path) == raw.decode().strip()
    assert store.projection_pending(db, SOURCE) == []
    operations = db.execute("SELECT * FROM radar_operations WHERE source_id=?", (SOURCE,))
    assert len(operations) == 1
    assert set(json.loads(operations[0]["change_manifest_json"])) == {
        "data/nintendo_refresh_status.json"}
    assert store.resolve_git_revision(db, SOURCE, result["frontend_commit"])["manifest_digest"] == head["manifest_digest"]


def test_git_failure_retains_durable_collection_and_pending_projection(primary_git):
    from radar_core.publication import PublicationError
    db, store, repository, git, remote = primary_git
    broken = repository()
    broken.push = lambda revision=None: False
    with pytest.raises(PublicationError):
        publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                        trigger_source="manual", run_id="1234", repository=broken)
    _, files = store.read_files(db, SOURCE)
    status = json.loads(files["data/nintendo_refresh_status.json"])
    assert status["status"] == "persisted" and status["state_persisted"] is True
    assert len(store.projection_pending(db, SOURCE)) == 1
    assert decide(now=NOW, receipt=status)["should_collect"] is False
    publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                    trigger_source="manual", run_id="1234", repository=repository())
    assert store.projection_pending(db, SOURCE) == []


def test_cas_retry_preserves_another_collectors_newer_data(primary_git, monkeypatch):
    db, store, repository, git, remote = primary_git
    original = store.publish_patch
    raced = []
    def race(client, source, **options):
        if not raced:
            raced.append(True)
            original(client, source, base_generation=options["base_generation"],
                     changed={"data/another-collector.json": b'{"concurrent":true}\n'}, operation_id="other")
        return original(client, source, **options)
    monkeypatch.setattr(store, "publish_patch", race)
    publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                    trigger_source="manual", run_id="1234", repository=repository())
    assert json.loads(git(remote, "show", "main:data/another-collector.json")) == {"concurrent": True}


def test_stale_collection_cannot_replace_newer_primary_state(primary_git):
    db, store, repository, git, remote = primary_git
    head, files = store.read_files(db, SOURCE)
    status = json.loads(files["data/nintendo_refresh_status.json"])
    status["generated_at"] = "2026-10-04T16:05:00Z"
    store.publish_patch(db, SOURCE, base_generation=head["generation"],
                        changed={"data/nintendo_refresh_status.json": json.dumps(status).encode()}, operation_id="newer")
    with pytest.raises(PublishError, match="newer_database_collection"):
        publish_primary(bundle(), RecordingClient(), now=NOW, slot=CHECKED,
                        trigger_source="manual", run_id="1234", repository=repository())


@pytest.mark.parametrize("changes", [{"storage_backend": "git"}, {"state_persisted": False},
    {"run_id": ""}, {"committed_at": "2026-10-05T00:00:00Z"}, {"complete": False}])
def test_unverified_persisted_receipt_cannot_skip_collection(changes):
    state = {"schema_version": 1, "status": "persisted", "complete": True,
             "storage_backend": "turso", "state_persisted": True, "run_id": "1234",
             "generated_at": CHECKED, "committed_at": CHECKED, "target_slot": CHECKED, **changes}
    with pytest.raises(ValueError):
        decide(now=NOW, receipt=state)
