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


def decide(*, now: datetime, requested: str = "", source: str = "manual",
           force: bool = False, receipt: dict | None = None) -> dict:
    slot = target_slot(now, requested, source)
    if force and source != "manual":
        raise ValueError("force_requires_manual")
    already_published = False
    if receipt is not None:
        if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
            raise ValueError("invalid_publication_receipt")
        if receipt.get("complete") is True and receipt.get("status") == "published":
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
            "reason": "manual_force" if force else "collection_due" if should_collect else "already_published_today"}


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
