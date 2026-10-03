from datetime import datetime, timezone
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
