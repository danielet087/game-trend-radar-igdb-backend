"""Reject stale daily dispatches and avoid repeating a successful collection."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
from zoneinfo import ZoneInfo

from scripts.publish import FRONTEND, GitHub, PublishError

TAIPEI = ZoneInfo("Asia/Taipei")
UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
SOURCES = {"manual", "cloudflare", "schedule"}


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        raise ValueError("invalid_timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_timestamp") from None


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def target_slot(now: datetime, requested: str = "", source: str = "manual") -> str:
    if source not in SOURCES or now.tzinfo is None:
        raise ValueError("invalid_trigger_source")
    if source != "manual" and not requested:
        raise ValueError("missing_daily_slot")
    target = parse_utc(requested) if requested else now.astimezone(timezone.utc).replace(microsecond=0)
    if target > now or target.astimezone(TAIPEI).date() != now.astimezone(TAIPEI).date():
        raise ValueError("stale_or_future_slot")
    if source != "manual" and (target.hour, target.minute, target.second) != (0, 30, 0):
        raise ValueError("invalid_daily_slot")
    return utc_text(target)


def _validate_partial_receipt(receipt: dict, *, now: datetime) -> None:
    """Accept a durable partial ledger without claiming a successful daily lookup.

    Missing historical IDs remain in the authoritative ledger and must be
    queried on a later collection. The delivery receipt alone cannot certify
    the contents of that ledger; publication separately verifies the bundle.
    """
    reason = "invalid_partial_collection_receipt"
    provenance = receipt.get("source")
    if (type(receipt.get("schema_version")) is not int
            or receipt.get("complete") is not False or receipt.get("collection_status") != "partial"
            or not isinstance(provenance, dict) or provenance.get("provider") != "IGDB"
            or type(provenance.get("hypes_threshold")) is not int
            or provenance["hypes_threshold"] != 30 or provenance.get("complete") is not False):
        raise ValueError(reason)
    for payload in (receipt, provenance):
        if (payload.get("ledger_complete") is not True or payload.get("lookup_complete") is not False
                or payload.get("public_complete") is not True):
            raise ValueError(reason)
    lookup = provenance.get("game_lookup")
    if not isinstance(lookup, dict):
        raise ValueError(reason)
    ids, unavailable = lookup.get("unavailable_ids"), lookup.get("unavailable")
    if (not isinstance(ids, list) or not ids
            or any(type(id_) is not int or id_ <= 0 for id_ in ids)
            or ids != sorted(set(ids)) or not isinstance(unavailable, list)
            or len(unavailable) != len(ids)):
        raise ValueError(reason)
    count = len(ids)
    for key in ("pending_count", "unavailable_count"):
        if type(receipt.get(key)) is not int or receipt[key] != count:
            raise ValueError(reason)
    for key in ("initial_missing_count", "recovered_count", "recovery_requests", "max_recovery_requests"):
        if type(lookup.get(key)) is not int or lookup[key] < 0:
            raise ValueError(reason)
    if (lookup["recovered_count"] + count != lookup["initial_missing_count"]
            or not 2 <= lookup["recovery_requests"] <= lookup["max_recovery_requests"] <= 20):
        raise ValueError(reason)
    generated = parse_utc(receipt.get("generated_at"))
    for id_, detail in zip(ids, unavailable):
        if (not isinstance(detail, dict)
                or set(detail) != {"igdb_id", "reason", "last_checked_at", "request_attempts"}
                or type(detail.get("igdb_id")) is not int or detail["igdb_id"] != id_
                or detail.get("reason") != "missing_after_bounded_recovery"
                or type(detail.get("request_attempts")) is not int or detail["request_attempts"] != 3
                or detail["request_attempts"] > 1 + lookup["recovery_requests"]):
            raise ValueError(reason)
        if parse_utc(detail.get("last_checked_at")) > generated:
            raise ValueError(reason)
    trigger = receipt.get("trigger_source")
    recorded_target = parse_utc(receipt.get("target_slot"))
    if (not isinstance(trigger, str) or trigger not in SOURCES or recorded_target > generated
            or recorded_target.astimezone(TAIPEI).date() != generated.astimezone(TAIPEI).date()
            or (trigger != "manual" and
                (recorded_target.hour, recorded_target.minute, recorded_target.second) != (0, 30, 0))):
        raise ValueError(reason)
    if receipt["status"] == "persisted_partial":
        run_id, delivered_at = receipt.get("run_id"), receipt.get("committed_at")
        if (receipt.get("storage_backend") != "turso" or receipt.get("state_persisted") is not True
                or receipt.get("authoritative_source") != FRONTEND + "@main"
                or receipt.get("backend_mirror") != "not_required"):
            raise ValueError(reason)
    else:
        run_id, delivered_at = receipt.get("published_run_id"), receipt.get("published_at")
        if "run_id" in receipt and receipt["run_id"] != run_id:
            raise ValueError(reason)
    if (not isinstance(run_id, str) or not re.fullmatch(r"[0-9]+", run_id)
            or not generated <= parse_utc(delivered_at) <= now):
        raise ValueError(reason)


def decide(*, now: datetime, requested: str = "", source: str = "manual",
           force: bool = False, receipt: dict | None = None) -> dict:
    slot = target_slot(now, requested, source)
    if force and source != "manual":
        raise ValueError("force_requires_manual")
    already_published = False
    partial_history_lookup = False
    if receipt is not None:
        if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
            raise ValueError("invalid_publication_receipt")
        if receipt.get("status") in ("persisted_partial", "published_partial"):
            _validate_partial_receipt(receipt, now=now)
            partial_history_lookup = True
        elif receipt.get("status") == "persisted":
            # The primary collection is complete even while its separate static
            # projection remains pending; do not recollect the same daily slot.
            run_id = receipt.get("run_id")
            if (receipt.get("complete") is not True or receipt.get("storage_backend") != "turso"
                    or receipt.get("state_persisted") is not True
                    or not isinstance(run_id, str) or not re.fullmatch(r"[0-9]+", run_id)):
                raise ValueError("invalid_primary_collection_receipt")
            generated = parse_utc(receipt.get("generated_at"))
            committed = parse_utc(receipt.get("committed_at"))
            recorded_target = parse_utc(receipt.get("target_slot"))
            if recorded_target > generated or generated > committed or committed > now:
                raise ValueError("invalid_primary_collection_receipt")
            already_published = generated.astimezone(TAIPEI).date() == now.astimezone(TAIPEI).date()
        elif receipt.get("complete") is True and receipt.get("status") == "published":
            run_id = receipt.get("published_run_id")
            if not isinstance(run_id, str) or not re.fullmatch(r"[0-9]+", run_id):
                raise ValueError("invalid_publication_receipt")
            generated = parse_utc(receipt.get("generated_at"))
            published = parse_utc(receipt.get("published_at"))
            recorded_target = parse_utc(receipt.get("target_slot"))
            if recorded_target > generated or generated > published or published > now:
                raise ValueError("invalid_publication_receipt")
            already_published = generated.astimezone(TAIPEI).date() == now.astimezone(TAIPEI).date()
    should_collect = force or not already_published
    return {"should_collect": should_collect, "target_slot": slot, "trigger_source": source,
            "reason": "manual_force" if force else "partial_history_lookup_due" if partial_history_lookup
            else "collection_due" if should_collect else "already_published_today"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-slot", default="")
    parser.add_argument("--trigger-source", choices=sorted(SOURCES), default="manual")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        now = datetime.now(timezone.utc)
        # Validate before making a request, including forced runs.
        target_slot(now, args.target_slot, args.trigger_source)
        if args.force and args.trigger_source != "manual":
            raise ValueError("force_requires_manual")
        if os.environ.get("RADAR_DATABASE_PRIMARY") == "1":
            from radar_core.storage.primary import read_json
            receipt = read_json(FRONTEND, "data/nintendo_refresh_status.json")
        else:
            client = GitHub(os.environ.get("FRONTEND_REPO_TOKEN", "").strip())
            receipt = client.read_json(FRONTEND, "data/nintendo_refresh_status.json")
        result = decide(now=now, requested=args.target_slot, source=args.trigger_source,
                        force=args.force, receipt=receipt)
        print(json.dumps(result, ensure_ascii=False))
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a", encoding="utf-8") as handle:
                for key, value in result.items():
                    handle.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")
        return 0
    except Exception:
        print(json.dumps({"status": "error", "reason": "daily_guard_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
