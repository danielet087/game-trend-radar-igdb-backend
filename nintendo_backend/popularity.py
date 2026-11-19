"""IGDB PopScore admission for NS/NS2 games.

Console games enter the calendar at IGDB hypes >= 30. Hypes come from IGDB's
mostly Western PC audience, so most Switch games have little or none. NS/NS2
games are also admitted when their IGDB page visits or "Want to Play" list
additions are in the top 20% of NS/NS2 games with an exact release day from
90 days ago to a year ahead (the 2026-11-19 trial: 15 upcoming NS/NS2 games
by hypes, 53 with this rule). PopScore values are shares of IGDB activity
recalculated daily, so the bar is a rank within that pool, not a number, and
an admitted game stays admitted. PS5-only games keep the hypes rule.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from .igdb import CollectionError

TYPES = {1: "visits", 2: "want_to_play"}
TOP_PERCENT = 20
POOL_BACK_DAYS = 90
SWITCH_PLATFORMS = {130: "NS", 508: "NS2"}
PAGE_IDS = 100  # two types per game stay well under IGDB's 500-row page
POOL_EXCLUSIONS = {"not_standalone_game", "cancelled_or_rumored", "no_tracked_native_platform"}


def _platform_id(value):
    return value.get("id") if isinstance(value, dict) else value


def switch_game_ids(raw_games) -> list[int]:
    """IGDB games listing NS or NS2 at all; the pool narrows them by date later."""
    ids = set()
    for game in raw_games:
        if not isinstance(game, dict) or type(game.get("id")) is not int:
            continue
        platforms = [_platform_id(row) for row in game.get("platforms") or []]
        releases = [_platform_id(row.get("platform")) for row in game.get("release_dates") or [] if isinstance(row, dict)]
        if any(value in SWITCH_PLATFORMS for value in platforms + releases):
            ids.add(game["id"])
    return sorted(ids)


def fetch(client, ids) -> dict[int, dict]:
    """Visits and Want to Play per game; a missing type is 0.

    Should IGDB ever return one type twice, the latest calculation wins (then
    the larger value), so the result never depends on row order.
    """
    latest: dict[tuple[int, int], tuple[int, float]] = {}
    ids = sorted(set(ids))
    for index in range(0, len(ids), PAGE_IDS):
        part = ids[index:index + PAGE_IDS]
        rows = client.query("popularity_primitives", (
            f"fields game_id,popularity_type,value,calculated_at; "
            f"where game_id = ({','.join(map(str, part))}) & popularity_type = ({','.join(map(str, TYPES))}); limit 500;"))
        if not isinstance(rows, list) or len(rows) >= 500:
            raise CollectionError("invalid_popscore_page")
        requested = set(part)
        for row in rows:
            value = row.get("value") if isinstance(row, dict) else None
            if (not isinstance(row, dict) or row.get("game_id") not in requested
                    or row.get("popularity_type") not in TYPES or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise CollectionError("invalid_popscore_row")
            stamp = row.get("calculated_at") if type(row.get("calculated_at")) is int else 0
            key, candidate = (row["game_id"], row["popularity_type"]), (stamp, float(value))
            if key not in latest or candidate > latest[key]:
                latest[key] = candidate
    values: dict[int, dict] = {}
    for (game_id, type_), (_, value) in sorted(latest.items()):
        values.setdefault(game_id, {name: 0.0 for name in TYPES.values()})[TYPES[type_]] = value
    return values


def _day(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def pool_member(row: dict, start: date, end: date) -> bool:
    """A standalone, not-cancelled game with an exact NS/NS2 day in [start, end)."""
    if set(row.get("eligibility_reasons") or []) & POOL_EXCLUSIONS:
        return False
    return any(isinstance(record, dict) and record.get("platform") in SWITCH_PLATFORMS.values()
               and record.get("precision") == "day"
               and not str(record.get("status") or "").lower().startswith(("cancel", "rumor"))
               and (day := _day(record.get("date"))) is not None and start <= day < end
               for record in row.get("release_records") or [])


def top_percent(rank: int, size: int) -> int:
    """A rank as "top X%", rounded: the last game over the bar shows 20, not 21."""
    return max(1, math.floor(100 * rank / size + 0.5))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def admit(rows: dict[str, dict], values: dict[int, dict] | None, *, start: date, end: date,
          checked_at: str, previous_rows: dict | None = None, skip=()) -> dict:
    """Mark NS/NS2 rows admitted by PopScore and return a summary.

    Without fresh values (a failed request, or the publisher rebuilding the
    collector's own output) each row's stored values are used, so the same
    ledger always gives the same admissions.
    """
    previous_rows = previous_rows or {}
    if values is None:
        values = {}
        for row in previous_rows.values():
            stored = row.get("popscore") if isinstance(row, dict) else None
            if isinstance(stored, dict) and type(row.get("igdb_id")) is int:
                values[row["igdb_id"]] = {name: float(stored.get(name) or 0.0) for name in TYPES.values()}
    pool_start = start - timedelta(days=POOL_BACK_DAYS)
    pool = [row for key, row in rows.items() if key not in skip and pool_member(row, pool_start, end)]
    pool_ids = {row["igdb_id"] for row in pool}
    ranked = {name: sorted((values.get(row["igdb_id"], {}).get(name, 0.0) for row in pool), reverse=True)
              for name in TYPES.values()}
    cuts = {name: percentile(ranked[name], 100 - TOP_PERCENT) for name in TYPES.values()}
    admitted = 0
    for key, row in rows.items():
        if key in skip:
            continue
        own = values.get(row["igdb_id"])
        switch = any(platform.get("code") in SWITCH_PLATFORMS.values() for platform in row.get("platforms") or [])
        top = None
        if own and row["igdb_id"] in pool_ids:
            # "Top X%" for display: the better of the two ranks within the pool.
            tops = [top_percent(sum(other > own[name] for other in ranked[name]) + 1, len(pool))
                    for name in TYPES.values() if own.get(name, 0.0) > 0]
            top = min(tops) if tops else None
        row["popscore"] = ({**{name: own.get(name, 0.0) for name in TYPES.values()}, "top_percent": top}
                           if switch and own else None)
        prior = previous_rows.get(key) if isinstance(previous_rows.get(key), dict) else {}
        since = prior.get("popscore_admitted_at") if isinstance(prior.get("popscore_admitted_at"), str) else None
        qualifies = (switch and row["igdb_id"] in pool_ids and own is not None
                     and any(own.get(name, 0.0) > 0 and own[name] >= cuts[name] for name in TYPES.values()))
        if row.get("popularity_status") == "qualified":
            row["popularity_basis"] = "hypes"
            row["popscore_admitted_at"] = since
        elif switch and (qualifies or since):
            # Only the hypes reasons give way; dates, content and game type still decide.
            row["popularity_basis"] = "igdb_popscore"
            row["popscore_admitted_at"] = since or checked_at
            row["eligibility_reasons"] = [reason for reason in row["eligibility_reasons"] if not reason.startswith("hypes_")]
            row["calendar_eligible"] = not row["eligibility_reasons"]
        else:
            row["popularity_basis"] = None
            row["popscore_admitted_at"] = since
        admitted += row["popularity_basis"] == "igdb_popscore" and row["calendar_eligible"]
    return {"types": list(TYPES.values()), "top_percent": TOP_PERCENT, "pool_size": len(pool),
            "pool_window": {"start": pool_start.isoformat(), "end": end.isoformat()},
            "cuts": cuts, "admitted_count": admitted}
