"""Read-only trial: can IGDB PopScore measure attention for NS/NS2 games?

The public calendar admits console games by IGDB hypes >= 30. Hypes come from
IGDB's mostly Western PC audience, so most Switch games have little or none.
This trial reads IGDB's popularity primitives (page visits, "Want to Play",
"Playing", "Played" and any newer types) for every NS/NS2 game with an exact
release day from 90 days ago to a year ahead, with PS5 for scale, and reports
how the values spread and what different thresholds would admit. It writes
output/popularity_trial.{json,csv,md} and changes no public data.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from nintendo_backend.igdb import API_ROOT, CollectionError, IGDBClient, paginated_releases

TAIPEI = ZoneInfo("Asia/Taipei")
PLATFORMS = {130: "NS", 508: "NS2", 167: "PS5"}
ALLOWED_TYPES = {"main_game", "standalone_expansion", "remake", "remaster", "expanded_game", "port"}
CATEGORIES = {0: "main_game", 4: "standalone_expansion", 8: "remake", 9: "remaster", 10: "expanded_game", 11: "port"}
GAME_FIELDS = ("id,name,hypes,category,game_type.type,status,game_status.status,"
               "platforms.id,release_dates.platform.id,release_dates.date,release_dates.date_format.format,"
               "release_dates.category,release_dates.status.name,"
               "involved_companies.publisher,involved_companies.company.name")
HYPES_THRESHOLD = 30
PERCENTILES = (50, 75, 90, 95, 99)


class TrialClient(IGDBClient):
    """The collector's bounded client, allowed the two PopScore endpoints too."""
    ENDPOINTS = {"release_dates", "release_dates/count", "games", "popularity_types", "popularity_primitives"}

    def query(self, endpoint: str, query: str):
        if endpoint not in self.ENDPOINTS:
            raise ValueError("unsupported endpoint")
        if self._token is None:
            self.authenticate()
        return self._post(API_ROOT + endpoint, data=query, headers={
            "Client-ID": self.client_id, "Authorization": "Bearer " + self._token, "Accept": "application/json"})


def taipei_day(timestamp: int) -> date:
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(TAIPEI).date()


def game_type(game: dict) -> str | None:
    ref = game.get("game_type")
    if isinstance(ref, dict) and isinstance(ref.get("type"), str):
        return ref["type"].lower().replace(" ", "_")
    return CATEGORIES.get(game.get("category")) if type(game.get("category")) is int else None


def exact_releases(game: dict, start: date, end: date) -> dict[str, str]:
    """Earliest exact Taipei release day per tracked platform inside [start, end)."""
    days: dict[str, str] = {}
    for row in game.get("release_dates") or []:
        if not isinstance(row, dict):
            continue
        platform = (row.get("platform") or {}).get("id") if isinstance(row.get("platform"), dict) else row.get("platform")
        fmt = (row.get("date_format") or {}).get("format") if isinstance(row.get("date_format"), dict) else None
        # Same reading as the collector: IGDB spells a day either way; old rows use category 0.
        exact = (str(fmt).upper().replace(" ", "") in {"YYYYMMMMDD", "YYYYMMDD"} if fmt
                 else row.get("category") == 0)
        status = ((row.get("status") or {}).get("name") or "") if isinstance(row.get("status"), dict) else ""
        if (platform not in PLATFORMS or not exact or type(row.get("date")) is not int
                or status.lower().startswith(("cancel", "rumor"))):
            continue
        day = taipei_day(row["date"])
        if start <= day < end:
            code = PLATFORMS[platform]
            days[code] = min(days.get(code, day.isoformat()), day.isoformat())
    return days


def publishers(game: dict) -> list[str]:
    names = []
    for row in game.get("involved_companies") or []:
        if isinstance(row, dict) and row.get("publisher") is True:
            name = (row.get("company") or {}).get("name") if isinstance(row.get("company"), dict) else None
            if isinstance(name, str) and name not in names:
                names.append(name)
    return names


def chunks(values: list, size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def fetch_primitives(client, ids: list[int]) -> list[dict]:
    """Every primitive for these games; a full page is split until it is not full."""
    rows = client.query("popularity_primitives", (
        f"fields game_id,popularity_type,value,calculated_at; where game_id = ({','.join(map(str, ids))}); limit 500;"))
    if not isinstance(rows, list):
        raise CollectionError("invalid_popularity_page")
    if len(rows) >= 500:
        if len(ids) == 1:
            raise CollectionError("popularity_page_overflow")
        middle = len(ids) // 2
        return fetch_primitives(client, ids[:middle]) + fetch_primitives(client, ids[middle:])
    return rows


def collect(client, today: date, *, back_days: int = 90, ahead_days: int = 365) -> dict:
    start, end = today - timedelta(days=back_days), today + timedelta(days=ahead_days)
    lower = int(datetime.combine(start, datetime.min.time(), tzinfo=TAIPEI).timestamp())
    upper = int(datetime.combine(end, datetime.min.time(), tzinfo=TAIPEI).timestamp())
    releases = paginated_releases(client, f"platform = (130,167,508) & date >= {lower} & date < {upper}")
    ids = sorted({row["game"] for row in releases})
    games = []
    for part in chunks(ids, 100):
        rows = client.query("games", f"fields {GAME_FIELDS}; where id = ({','.join(map(str, part))}); limit 100;")
        if not isinstance(rows, list):
            raise CollectionError("invalid_game_page")
        games.extend(row for row in rows if isinstance(row, dict) and type(row.get("id")) is int)
    types = client.query("popularity_types", "fields id,name,popularity_source,external_popularity_source,updated_at; limit 500;")
    primitives = []
    for part in chunks(ids, 40):
        primitives.extend(fetch_primitives(client, part))
    return {"today": today.isoformat(), "start": start.isoformat(), "end": end.isoformat(),
            "games": games, "types": types if isinstance(types, list) else [], "primitives": primitives,
            "release_count": len(releases), "request_count": client.request_count}


def percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    def ranks(values):
        order = sorted(range(len(values)), key=values.__getitem__)
        result = [0.0] * len(values)
        index = 0
        while index < len(order):
            tie = index
            while tie + 1 < len(order) and values[order[tie + 1]] == values[order[index]]:
                tie += 1
            for position in range(index, tie + 1):
                result[order[position]] = (index + tie) / 2 + 1
            index = tie + 1
        return result
    xs, ys = ranks([pair[0] for pair in pairs]), ranks([pair[1] for pair in pairs])
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def build_rows(data: dict) -> tuple[list[dict], dict[int, str]]:
    today, start, end = (date.fromisoformat(data[key]) for key in ("today", "start", "end"))
    names = {row["id"]: str(row.get("name") or f"type {row['id']}") for row in data["types"]
             if isinstance(row, dict) and type(row.get("id")) is int}
    values: dict[int, dict[int, float]] = {}
    for row in data["primitives"]:
        if (isinstance(row, dict) and type(row.get("game_id")) is int and type(row.get("popularity_type")) is int
                and isinstance(row.get("value"), (int, float)) and not isinstance(row.get("value"), bool)):
            values.setdefault(row["game_id"], {})[row["popularity_type"]] = float(row["value"])
            names.setdefault(row["popularity_type"], f"type {row['popularity_type']}")
    rows = []
    for game in data["games"]:
        days = exact_releases(game, start, end)
        if not days:
            continue
        kind = game_type(game)
        status = str(((game.get("game_status") or {}).get("status") if isinstance(game.get("game_status"), dict) else "") or "")
        hypes = game.get("hypes") if type(game.get("hypes")) is int else None
        rows.append({
            "igdb_id": game["id"], "name": str(game.get("name") or ""), "game_type": kind,
            "standalone": kind in ALLOWED_TYPES and not status.lower().startswith(("cancel", "rumor")),
            "platforms": "/".join(code for code in ("NS2", "NS", "PS5") if code in days),
            "switch": "NS" in days or "NS2" in days, "first_day": min(days.values()),
            "upcoming": max(days.values()) >= today.isoformat(), "releases": days,
            "hypes": hypes, "hypes_qualified": hypes is not None and hypes >= HYPES_THRESHOLD,
            "publishers": publishers(game), "popularity": {str(type_id): value for type_id, value in sorted(values.get(game["id"], {}).items())},
        })
    rows.sort(key=lambda row: (row["first_day"], row["igdb_id"]))
    return rows, names


def report(data: dict) -> tuple[str, list[dict]]:
    rows, names = build_rows(data)
    focus = [row for row in rows if row["switch"] and row["standalone"]]
    ps5 = [row for row in rows if not row["switch"] and row["standalone"]]
    type_ids = sorted(names)
    value = lambda row, type_id: row["popularity"].get(str(type_id), 0.0)
    lines = [f"# IGDB PopScore trial — {data['today']} (Taipei)", "",
             f"Window: exact release days {data['start']} to {data['end']} (90 days back, one year ahead); "
             f"{data.get('release_count', '?')} NS/NS2/PS5 release records for {len(data['games'])} games, "
             f"{len(data['primitives'])} popularity values, {data['request_count']} IGDB requests.",
             f"NS/NS2 standalone games: {len(focus)} ({sum(row['upcoming'] for row in focus)} still to come); "
             f"PS5-only for scale: {len(ps5)}. Hypes >= {HYPES_THRESHOLD} today: "
             f"{sum(row['hypes_qualified'] for row in focus)} NS/NS2, {sum(row['hypes_qualified'] for row in ps5)} PS5-only.", "",
             "## Popularity types", ""]
    for type_id in type_ids:
        source = next((row for row in data["types"] if isinstance(row, dict) and row.get("id") == type_id), {})
        covered = sum(str(type_id) in row["popularity"] for row in focus)
        lines.append(f"- {type_id} {names[type_id]} (source {source.get('popularity_source', '?')}, "
                     f"external {source.get('external_popularity_source', '-')}): values for {covered}/{len(focus)} NS/NS2 games")
    lines += ["", "## Spread among NS/NS2 games (missing = 0)", "",
              "| type | " + " | ".join(f"p{p}" for p in PERCENTILES) + " | hypes>=30 min | hypes>=30 median | Spearman vs hypes |",
              "|---|" + "---|" * (len(PERCENTILES) + 3)]
    for type_id in type_ids:
        all_values = [value(row, type_id) for row in focus]
        qualified = [value(row, type_id) for row in focus if row["hypes_qualified"]]
        rho = spearman([(row["hypes"], value(row, type_id)) for row in focus if row["hypes"] is not None])
        lines.append(f"| {names[type_id]} | " + " | ".join(f"{percentile(all_values, p):.6g}" for p in PERCENTILES)
                     + f" | {min(qualified) if qualified else 0:.6g} | {statistics.median(qualified) if qualified else 0:.6g}"
                     + f" | {'' if rho is None else f'{rho:.2f}'} |")
    # The rank lists and threshold options use the type with the most NS/NS2 coverage.
    lead = max(type_ids, key=lambda type_id: (sum(str(type_id) in row["popularity"] for row in focus), -type_id), default=None)
    if lead is not None:
        for type_id in dict.fromkeys([lead, 2, 1]):
            if type_id not in names or not any(str(type_id) in row["popularity"] for row in focus):
                continue
            top = sorted(focus, key=lambda row: -value(row, type_id))[:60]
            lines += ["", f"## Top 60 NS/NS2 games by {names[type_id]}", "",
                      "| # | game | platforms | first day | hypes | value | publishers |", "|---|---|---|---|---|---|---|"]
            for rank, row in enumerate(top, 1):
                mark = " ✓" if row["hypes_qualified"] else ""
                lines.append(f"| {rank} | {row['name']}{mark} | {row['platforms']} | {row['first_day']} | "
                             f"{'' if row['hypes'] is None else row['hypes']} | {value(row, type_id):.6g} | "
                             f"{', '.join(row['publishers'][:2])} |")
        upcoming = [row for row in focus if row["upcoming"]]
        soon = [row for row in upcoming if row["first_day"] <= (date.fromisoformat(data["today"]) + timedelta(days=90)).isoformat()]
        lines += ["", f"## What thresholds on {names[lead]} would admit (NS/NS2, still to come)", "",
                  "| threshold | value | admitted by PopScore | new beyond hypes>=30 | next 90 days new |", "|---|---|---|---|---|"]
        all_values = [value(row, lead) for row in focus]
        for p in (80, 85, 90, 95):
            cut = percentile(all_values, p)
            admitted = [row for row in upcoming if value(row, lead) >= cut and value(row, lead) > 0]
            new = [row for row in admitted if not row["hypes_qualified"]]
            lines.append(f"| top {100 - p}% | {cut:.6g} | {len(admitted)} | {len(new)} | "
                         f"{sum(row in soon for row in new)} |")
            if p == 90:
                lines += [""] + [f"  - new at top 10%: {row['name']} ({row['platforms']} {row['first_day']}, hypes "
                                 f"{row['hypes']}, {value(row, lead):.6g})" for row in sorted(new, key=lambda row: row['first_day'])[:40]] + [""]
    return "\n".join(lines) + "\n", rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    today = args.today or datetime.now(TAIPEI).date()
    client = TrialClient(os.environ.get("TWITCH_CLIENT_ID", ""), os.environ.get("TWITCH_CLIENT_SECRET", ""),
                         deadline_seconds=600)
    data = collect(client, today)
    text, rows = report(data)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "popularity_trial.json").write_text(json.dumps(
        {**{key: data[key] for key in ("today", "start", "end", "types", "request_count")}, "games": rows},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (args.output_dir / "popularity_trial.md").write_text(text, encoding="utf-8")
    type_ids = sorted({int(key) for row in rows for key in row["popularity"]})
    with (args.output_dir / "popularity_trial.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["igdb_id", "name", "platforms", "first_day", "game_type", "hypes", "publishers",
                         *[f"pop_{type_id}" for type_id in type_ids]])
        for row in rows:
            writer.writerow([row["igdb_id"], row["name"], row["platforms"], row["first_day"], row["game_type"],
                             row["hypes"], "; ".join(row["publishers"]),
                             *[row["popularity"].get(str(type_id), "") for type_id in type_ids]])
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)


if __name__ == "__main__":
    main()
