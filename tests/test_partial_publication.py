"""A missing nonpublic lookup may persist only unchanged primary ledger state."""
from copy import deepcopy
from datetime import date
import json

import pytest

from nintendo_backend.catalog import build_documents
from nintendo_backend.persistence import decode_master
from scripts.primary_publication import publish_primary
from scripts.publish import FILES, PublishError, _serialize, publish, validate_bundle
from test_primary_state import primary_git, SOURCE
from test_publish import bundle, NOW, CHECKED, RecordingClient

OLDER = "2026-10-02T16:05:00Z"


def partial_bundle():
    fresh = deepcopy(bundle()["nintendo_master.json"]["games"]["igdb:12345"]["raw"])
    retained = {**deepcopy(fresh), "id": 415392, "name": "Unavailable ledger candidate", "hypes": 10}
    prior = dict(zip(FILES, build_documents([fresh, retained], start=date(2026, 10, 3), checked_at=OLDER)))
    details = [{"igdb_id": 415392, "reason": "missing_after_bounded_recovery",
                "request_attempts": 3, "last_checked_at": OLDER}]
    current = dict(zip(FILES, build_documents([fresh], start=date(2026, 10, 4), checked_at=CHECKED,
        previous=prior["nintendo_master.json"], retained_unavailable_ids=[415392],
        source={"game_lookup": {"unavailable_ids": [415392], "unavailable": details,
            "initial_missing_count": 1, "recovered_count": 0, "recovery_requests": 2, "max_recovery_requests": 20}})))
    return prior, current


def seed_prior(db, store, prior):
    head, _ = store.read_files(db, SOURCE)
    store.publish_patch(db, SOURCE, base_generation=head["generation"],
        changed={path: raw.encode() for path, raw in _serialize(prior).items()}, operation_id="prior-ledger")


@pytest.fixture
def partial_primary_git(primary_git, monkeypatch):
    monkeypatch.setenv("RADAR_DATABASE_PRIMARY", "1")
    return primary_git


def test_partial_gate_rebuilds_fresh_public_without_refreshing_retained_observation():
    prior, current = partial_bundle()
    validate_bundle(current, now=NOW)
    assert current["nintendo_master.json"]["games"]["igdb:415392"] == prior["nintendo_master.json"]["games"]["igdb:415392"]
    assert [row["id"] for row in current["nintendo_upcoming.json"]["games"]] == ["igdb:12345"]


@pytest.mark.parametrize("payload", ["source", "status"])
@pytest.mark.parametrize("field", ["ledger_complete", "lookup_complete", "public_complete"])
def test_complete_bundle_cannot_claim_contradictory_explicit_completeness_flags(payload, field):
    current = bundle()
    target = current["nintendo_master.json"]["source"] if payload == "source" else current["nintendo_refresh_status.json"]
    target[field] = False
    with pytest.raises(PublishError, match="inconsistent_lookup_completeness"):
        validate_bundle(current, now=NOW)


def test_legacy_complete_bundle_without_new_flags_remains_valid():
    current = bundle()
    for payload in current.values():
        for target in (payload, payload["source"]):
            for key in ("ledger_complete", "lookup_complete", "public_complete", "unavailable_count"):
                target.pop(key, None)
    validate_bundle(current, now=NOW)


@pytest.mark.parametrize("mutate", [
    lambda b: b["nintendo_refresh_status.json"].update(pending_count=0),
    lambda b: b["nintendo_refresh_status.json"].update(public_complete=False),
    lambda b: b["nintendo_refresh_status.json"].update(lookup_complete=True),
    lambda b: b["nintendo_master.json"]["source"]["game_lookup"].update(unavailable_ids=[415392, 415392]),
    lambda b: b["nintendo_master.json"]["source"]["game_lookup"]["unavailable"][0].update(request_attempts=2),
    lambda b: b["nintendo_master.json"]["source"]["game_lookup"]["unavailable"][0].update(last_checked_at=CHECKED),
    lambda b: b["nintendo_master.json"]["source"]["game_lookup"].update(initial_missing_count=2),
    lambda b: b["nintendo_master.json"]["source"]["game_lookup"].update(max_recovery_requests=21),
    lambda b: b["nintendo_master.json"]["source"]["game_lookup"].update(recovery_requests=1),
    lambda b: b["nintendo_master.json"]["games"]["igdb:415392"].update(calendar_eligible=True),
    lambda b: b["nintendo_upcoming.json"]["games"][0].update(hypes=999),
])
def test_partial_does_not_relax_metadata_or_fresh_public_qualification(mutate):
    _, current = partial_bundle()
    mutate(current)
    with pytest.raises(PublishError):
        validate_bundle(current, now=NOW)


def test_partial_is_rejected_before_legacy_remote_writes(monkeypatch):
    monkeypatch.setenv("RADAR_DATABASE_PRIMARY", "0")
    _, current = partial_bundle()
    client = RecordingClient()
    with pytest.raises(PublishError, match="partial_collection_requires_database_primary"):
        publish(current, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


def test_direct_primary_entry_also_requires_database_flag_for_partial(monkeypatch):
    monkeypatch.setenv("RADAR_DATABASE_PRIMARY", "0")
    _, current = partial_bundle()
    with pytest.raises(PublishError, match="partial_collection_requires_database_primary"):
        publish_primary(current, RecordingClient(), now=NOW, slot=CHECKED,
            trigger_source="manual", run_id="1234", repository=object())


def test_primary_persists_partial_truthfully_and_projects_unchanged_retained_row(partial_primary_git, monkeypatch):
    db, store, repository, git, remote = partial_primary_git
    prior, current = partial_bundle()
    # The CAS check must also decode a compressed previous ledger.
    monkeypatch.setattr("nintendo_backend.persistence.ENCODE_THRESHOLD_BYTES", 1)
    seed_prior(db, store, prior)
    result = publish_primary(current, RecordingClient(), now=NOW, slot=CHECKED,
        trigger_source="manual", run_id="1234", repository=repository())
    _, files = store.read_files(db, SOURCE)
    master = decode_master(json.loads(files["data/nintendo_master.json"]))
    status = json.loads(files["data/nintendo_refresh_status.json"])
    assert master["games"]["igdb:415392"] == prior["nintendo_master.json"]["games"]["igdb:415392"]
    assert status["status"] == "persisted_partial" and status["complete"] is False
    assert result["status"] == "published_partial" and result["complete"] is False
    for receipt in (status, result):
        assert receipt["collection_status"] == "partial"
        assert receipt["ledger_complete"] is True and receipt["lookup_complete"] is False and receipt["public_complete"] is True
        assert receipt["pending_count"] == receipt["unavailable_count"] == 1
        from scripts.guard import decide
        decision = decide(now=NOW, receipt=receipt)
        assert decision["should_collect"] is True and decision["reason"] == "partial_history_lookup_due"
    assert store.projection_pending(db, SOURCE) == []
    assert git(remote, "show", "main:data/nintendo_master.json") == files["data/nintendo_master.json"].decode().strip()


@pytest.mark.parametrize("change", ["forged_retained", "previously_public"])
def test_primary_validates_retention_against_actual_previous_database(partial_primary_git, change):
    db, store, repository, _, _ = partial_primary_git
    prior, current = partial_bundle()
    if change == "forged_retained":
        current["nintendo_master.json"]["games"]["igdb:415392"]["cover_image"] = "https://images.igdb.com/new.jpg"
    else:
        prior["nintendo_upcoming.json"]["games"].append({"id": "igdb:415392"})
    validate_bundle(current, now=NOW)  # A bundle cannot establish its own baseline.
    seed_prior(db, store, prior)
    head, before = store.read_files(db, SOURCE)
    with pytest.raises(PublishError, match="retained_candidate_changed_in_database"):
        publish_primary(current, RecordingClient(), now=NOW, slot=CHECKED,
            trigger_source="manual", run_id="1234", repository=repository())
    after_head, after = store.read_files(db, SOURCE)
    assert after_head["generation"] == head["generation"] and after == before


def test_cas_retry_cannot_overwrite_a_concurrently_refreshed_retained_candidate(partial_primary_git, monkeypatch):
    db, store, repository, _, _ = partial_primary_git
    prior, current = partial_bundle()
    seed_prior(db, store, prior)
    original = store.publish_patch
    raced = []
    newer = deepcopy(prior["nintendo_master.json"])
    newer["games"]["igdb:415392"].update(checked_at=CHECKED, hypes=11)
    newer["games"]["igdb:415392"]["raw"]["hypes"] = 11
    def race(client, source, **options):
        if not raced:
            raced.append(True)
            original(client, source, base_generation=options["base_generation"],
                changed={"data/nintendo_master.json": json.dumps(newer).encode()}, operation_id="newer-retained-row")
        return original(client, source, **options)
    monkeypatch.setattr(store, "publish_patch", race)
    with pytest.raises(PublishError, match="retained_candidate_changed_in_database"):
        publish_primary(current, RecordingClient(), now=NOW, slot=CHECKED,
            trigger_source="manual", run_id="1234", repository=repository())
    _, files = store.read_files(db, SOURCE)
    actual = decode_master(json.loads(files["data/nintendo_master.json"]))
    assert actual["games"]["igdb:415392"]["hypes"] == 11
    assert json.loads(files["data/nintendo_refresh_status.json"])["generated_at"] == OLDER
