from datetime import datetime, timezone
from copy import deepcopy
import json

import pytest

from scripts.guard import decide, main, target_slot

NOW = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)
SLOT = "2026-10-04T00:30:00Z"


def receipt():
    return {"schema_version": 1, "complete": True, "status": "published", "published_run_id": "1234",
            "generated_at": "2026-10-04T00:35:00Z", "published_at": "2026-10-04T00:36:00Z", "target_slot": SLOT}


def test_daily_dispatch_skips_published_day_and_manual_force_can_refresh():
    state = receipt()
    skipped = decide(now=NOW, requested=SLOT, source="cloudflare", receipt=state)
    assert skipped["should_collect"] is False and skipped["reason"] == "already_published_today"
    assert decide(now=NOW, force=True, receipt=state)["should_collect"] is True


def test_today_is_taipei_day_including_previous_utc_evening():
    state = receipt()
    state.update(generated_at="2026-10-03T16:15:00Z", published_at="2026-10-03T16:16:00Z", target_slot="2026-10-03T16:10:00Z")
    assert decide(now=NOW, requested=SLOT, source="cloudflare", receipt=state)["should_collect"] is False


def test_prior_day_or_missing_receipt_collects():
    assert decide(now=NOW, requested=SLOT, source="cloudflare")["should_collect"] is True
    state = receipt()
    state.update(generated_at="2026-10-03T00:35:00Z", published_at="2026-10-03T00:36:00Z", target_slot="2026-10-03T00:30:00Z")
    assert decide(now=NOW, requested=SLOT, source="cloudflare", receipt=state)["should_collect"] is True


@pytest.mark.parametrize("requested", ["bad\nshould_collect=true", "2026-10-04T00:30:00", "2026-10-04T00:30:00+00:00", "2026-10-03T00:30:00Z", "2026-10-04T02:00:00Z", "2026-10-04T00:31:00Z"])
def test_malformed_stale_future_or_wrong_daily_slot_rejected(requested):
    with pytest.raises(ValueError):
        target_slot(NOW, requested, "cloudflare")


def test_external_dispatch_must_supply_slot_and_cannot_force():
    with pytest.raises(ValueError, match="missing_daily_slot"):
        decide(now=NOW, source="cloudflare")
    with pytest.raises(ValueError, match="force_requires_manual"):
        decide(now=NOW, source="cloudflare", requested=SLOT, force=True)


def test_manual_slot_is_actual_utc_time():
    assert decide(now=NOW)["target_slot"] == "2026-10-04T01:00:00Z"


@pytest.mark.parametrize("changed", [
    {"published_run_id": ""}, {"generated_at": "2026-10-04T02:00:00Z"},
    {"published_at": "2026-10-04T02:00:00Z"}, {"target_slot": "2026-10-04T00:40:00Z"},
    {"schema_version": 99},
])
def test_invalid_receipts_fail_closed(changed):
    state = receipt()
    state.update(changed)
    with pytest.raises(ValueError):
        decide(now=NOW, requested=SLOT, source="cloudflare", receipt=state)


def test_incomplete_receipt_cannot_prevent_retry():
    state = receipt()
    state.update(complete=False, status="prepared")
    assert decide(now=NOW, requested=SLOT, source="cloudflare", receipt=state)["should_collect"] is True


def test_guard_outputs_only_safe_decision_and_never_exception_text(monkeypatch, tmp_path, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("SECRET-FROM-HEADERS")
    monkeypatch.setattr("scripts.guard.GitHub.read_json", fail)
    monkeypatch.setenv("FRONTEND_REPO_TOKEN", "SECRET-FROM-HEADERS")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    assert main([]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "daily_guard_failed" and "SECRET" not in output
    assert not (tmp_path / "output").exists()


def partial_receipt(status="persisted_partial"):
    flags = {"ledger_complete": True, "lookup_complete": False, "public_complete": True}
    state = {**receipt(), **flags, "status": status, "complete": False,
             "collection_status": "partial", "trigger_source": "cloudflare",
             "pending_count": 2, "unavailable_count": 2,
             "source": {"provider": "IGDB", "complete": False, "hypes_threshold": 30, **flags,
                        "game_lookup": {"unavailable_ids": [23, 415392],
                            "unavailable": [{"igdb_id": id_, "reason": "missing_after_bounded_recovery",
                                "last_checked_at": "2026-10-03T00:35:00Z", "request_attempts": 3}
                                for id_ in [23, 415392]],
                            "initial_missing_count": 3, "recovered_count": 1,
                            "recovery_requests": 3, "max_recovery_requests": 20}}}
    if status == "persisted_partial":
        state.update(storage_backend="turso", state_persisted=True, run_id="1234",
                     committed_at="2026-10-04T00:36:00Z", backend_mirror="not_required",
                     authoritative_source="danielet087/game-trend-radar@main")
        state.pop("published_at")
        state.pop("published_run_id")
    return state


@pytest.mark.parametrize("status", ["persisted_partial", "published_partial"])
def test_durable_partial_ledger_never_counts_as_complete_daily_lookup(status):
    state = partial_receipt(status)
    before = deepcopy(state)
    result = decide(now=NOW, requested=SLOT, source="cloudflare", receipt=state)
    assert result["should_collect"] is True
    assert result["reason"] == "partial_history_lookup_due"
    assert state == before
    assert decide(now=NOW, receipt=state, force=True)["reason"] == "manual_force"
    tomorrow = datetime(2026, 10, 5, 1, tzinfo=timezone.utc)
    following = decide(now=tomorrow, requested="2026-10-05T00:30:00Z", source="cloudflare", receipt=state)
    assert following["should_collect"] is True
    assert following["reason"] == "partial_history_lookup_due"


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("complete",), True), (("collection_status",), "complete"),
    (("ledger_complete",), False), (("lookup_complete",), True), (("public_complete",), False),
    (("source", "provider"), "unknown"), (("source", "complete"), True),
    (("source", "hypes_threshold"), True), (("source", "ledger_complete"), False),
    (("source", "lookup_complete"), True), (("source", "public_complete"), False),
    (("pending_count",), 0), (("unavailable_count",), True),
    (("source", "game_lookup", "unavailable_ids"), []),
    (("source", "game_lookup", "unavailable_ids"), [415392, 23]),
    (("source", "game_lookup", "unavailable_ids"), [23, 23]),
    (("source", "game_lookup", "unavailable_ids"), [23, "415392"]),
    (("source", "game_lookup", "unavailable_ids"), [23, True]),
    (("source", "game_lookup", "unavailable"), []),
    (("source", "game_lookup", "unavailable", 0, "igdb_id"), 415392),
    (("source", "game_lookup", "unavailable", 0, "reason"), "http_error"),
    (("source", "game_lookup", "unavailable", 0, "last_checked_at"), "2026-10-04T00:36:00Z"),
    (("source", "game_lookup", "unavailable", 0, "request_attempts"), True),
    (("source", "game_lookup", "unavailable", 0, "request_attempts"), 0),
    (("source", "game_lookup", "unavailable", 0, "request_attempts"), 1),
    (("source", "game_lookup", "unavailable", 0, "request_attempts"), 2),
    (("source", "game_lookup", "unavailable", 0, "request_attempts"), 5),
    (("source", "game_lookup", "initial_missing_count"), 2),
    (("source", "game_lookup", "recovered_count"), -1),
    (("source", "game_lookup", "max_recovery_requests"), 2),
    (("source", "game_lookup", "max_recovery_requests"), 21),
    (("source", "game_lookup", "recovery_requests"), 1),
    (("source", "game_lookup", "recovery_requests"), True),
    (("storage_backend",), "git"), (("state_persisted",), False), (("run_id",), ""),
    (("authoritative_source",), "other@main"), (("backend_mirror",), "published"),
    (("trigger_source",), "unknown"), (("target_slot",), "2026-10-04T00:31:00Z"),
    (("target_slot",), "2026-10-03T00:30:00Z"),
    (("generated_at",), "2026-10-04T02:00:00Z"),
    (("committed_at",), "2026-10-04T00:34:00Z"),
    (("committed_at",), "2026-10-04T02:00:00Z"),
])
def test_partial_receipt_cannot_bypass_validation_with_malformed_metadata(path, value):
    state = partial_receipt()
    target = state
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        decide(now=NOW, requested=SLOT, source="cloudflare", receipt=state)


@pytest.mark.parametrize("key", ["collection_status", "source", "pending_count", "unavailable_count",
                                "ledger_complete", "lookup_complete", "public_complete", "committed_at"])
def test_partial_receipt_requires_all_metadata(key):
    state = partial_receipt()
    del state[key]
    with pytest.raises(ValueError):
        decide(now=NOW, requested=SLOT, source="cloudflare", receipt=state)


def test_partial_receipt_rejects_extra_unsanitized_failure_details():
    state = partial_receipt()
    state["source"]["game_lookup"]["unavailable"][0]["response_body"] = "unsafe"
    with pytest.raises(ValueError):
        decide(now=NOW, receipt=state)


@pytest.mark.parametrize("changes", [{"published_run_id": ""}, {"run_id": "999"},
                                   {"published_at": "2026-10-04T02:00:00Z"}])
def test_published_partial_requires_verified_delivery_metadata(changes):
    state = partial_receipt("published_partial")
    state.update(changes)
    with pytest.raises(ValueError):
        decide(now=NOW, receipt=state)
