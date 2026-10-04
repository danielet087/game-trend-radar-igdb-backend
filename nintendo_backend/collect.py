"""Collect a full ledger; publishable data is written only after full validation."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from .catalog import build_documents
from .chinese_names import SteamNameClient, enrich_documents, load_registry
from .igdb import CollectionError, IGDBClient, paginated_releases
from .taiwan_releases import load_registry as load_release_registry

GAME_FIELDS = (
    "id,name,hypes,url,category,game_type.type,status,game_status.status,summary,storyline,"
    "platforms.id,platforms.name,release_dates.id,release_dates.platform.id,release_dates.platform.name,"
    "release_dates.date,release_dates.date_format.format,release_dates.category,release_dates.y,release_dates.m,"
    "release_dates.d,release_dates.human,release_dates.region,release_dates.release_region.region,release_dates.status.name,"
    "themes.name,age_ratings.synopsis,age_ratings.rating_category.rating,age_ratings.organization.name,"
    "age_ratings.rating_content_descriptions.description,age_ratings.rating_content_descriptions.description_type.name,"
    "age_ratings.content_descriptions.description,game_localizations.name,game_localizations.region.identifier,"
    "alternative_names.name,alternative_names.comment,external_games.uid,external_games.game,"
    "external_games.external_game_source.name,cover.url,websites.url"
)


def load_existing(path):
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "games": {}}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CollectionError("invalid_previous_master") from None
    if not isinstance(existing, dict) or existing.get("schema_version") != 1 or not isinstance(existing.get("games"), dict):
        raise CollectionError("invalid_previous_master")
    for key, row in existing["games"].items():
        if not isinstance(row, dict) or type(row.get("igdb_id")) is not int or row["igdb_id"] <= 0 or key != "igdb:" + str(row["igdb_id"]):
            raise CollectionError("invalid_previous_master")
    return existing


def collect(client, *, start: date, previous=None, checked_at=None, page_size=500, max_pages=100,
            name_registry=None, name_client=None, release_registry=None):
    client.verify_platforms()
    end = start + timedelta(days=365)
    # The year branch also retains month/quarter/year dates overlapping the window.
    # A timestamp attached to a month is never mistaken for a precise day.
    lower = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    upper = int(datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    where = f"platform = (130,508) & ((date >= {lower} & date < {upper}) | (y >= {start.year} & y <= {end.year}) | date = null)"
    releases = paginated_releases(client, where, page_size=page_size, max_pages=max_pages)
    game_ids = {row["game"] for row in releases}
    game_ids.update(row["igdb_id"] for row in (previous or {}).get("games", {}).values())
    if len(game_ids) > 50000:
        raise CollectionError("candidate_limit_exceeded")
    raw_games = []
    ids = sorted(game_ids)
    for offset in range(0, len(ids), 100):
        batch = ids[offset:offset + 100]
        joined = ",".join(str(id_) for id_ in batch)
        rows = client.query("games", f"fields {GAME_FIELDS}; where id = ({joined}); sort id asc; limit 100;")
        if (not isinstance(rows, list) or len(rows) != len(batch) or
                any(not isinstance(row, dict) or type(row.get("id")) is not int for row in rows) or
                sorted(row["id"] for row in rows) != batch):
            raise CollectionError("game_lookup_incomplete")
        raw_games.extend(rows)
    if checked_at is None:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    master, catalog, status = build_documents(raw_games, start=start, checked_at=checked_at, previous=previous,
                           name_registry=name_registry,
                           release_registry=release_registry,
                           source={"discovery_release_count": len(releases), "request_count": client.request_count,
                                   "retry_count": client.retry_count, "platform_ids_verified": [130, 508],
                                   "candidate_scope": "Nintendo platform release records: window/overlapping year/undated, plus previous ledger"})
    if name_client is not None:
        status["name_enrichment"] = enrich_documents(master, catalog, name_client)
    return master, catalog, status


def write_json_atomic(path: Path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--existing", default="data/nintendo_master.json")
    parser.add_argument("--chinese-names", default="data/chinese_names.json")
    parser.add_argument("--taiwan-releases", default="data/nintendo_release_dates.json")
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    out = Path(args.output_dir)
    try:
        previous = load_existing(args.existing)
        name_registry = load_registry(args.chinese_names)
        release_registry = load_release_registry(args.taiwan_releases)
        client = IGDBClient(os.environ.get("TWITCH_CLIENT_ID", ""), os.environ.get("TWITCH_CLIENT_SECRET", ""))
        start = args.today or datetime.now(ZoneInfo("Asia/Taipei")).date()
        master, catalog, status = collect(client, start=start, previous=previous,
                                         name_registry=name_registry, name_client=SteamNameClient(),
                                         release_registry=release_registry)
        # All API, completeness and qualification gates have passed before any data write.
        write_json_atomic(out / "nintendo_master.json", master)
        write_json_atomic(out / "nintendo_upcoming.json", catalog)
        write_json_atomic(out / "nintendo_refresh_status.json", status)
        print(json.dumps({key: status[key] for key in ("status", "generated_at", "candidate_count", "public_count", "popularity_counts")}, ensure_ascii=False))
        return 0
    except Exception as exc:
        failure = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                   "complete": False, "status": "failed", "reason": exc.reason if isinstance(exc, CollectionError) else "collection_error"}
        if isinstance(exc, CollectionError) and exc.http_status is not None:
            failure["http_status"] = exc.http_status
        try:
            write_json_atomic(out / "nintendo_refresh_status.json", failure)
        except OSError:
            pass
        print(json.dumps(failure, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
