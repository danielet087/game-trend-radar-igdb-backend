"""Collect a full ledger; publishable data is written only after full validation."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from .catalog import build_documents
from .chinese_names import SteamNameClient, enrich_documents, load_registry
from .igdb import CollectionError, IGDBClient, paginated_releases
from .languages import load_combined_registry as load_language_registry
from .taiwan_releases import load_combined_registry as load_release_registry
from .playstation import PlayStationStoreClient
from .persistence import decode_master
from .descriptions import enrich_documents as enrich_descriptions
from .official_descriptions import OfficialDescriptionClient

GAME_FIELDS = (
    "id,name,hypes,url,category,game_type.type,status,game_status.status,summary,storyline,"
    "game_modes.id,game_modes.name,multiplayer_modes.platform.id,"
    "multiplayer_modes.campaigncoop,multiplayer_modes.dropin,multiplayer_modes.lancoop,"
    "multiplayer_modes.offlinecoop,multiplayer_modes.offlinecoopmax,multiplayer_modes.offlinemax,"
    "multiplayer_modes.onlinecoop,multiplayer_modes.onlinecoopmax,multiplayer_modes.onlinemax,"
    "multiplayer_modes.splitscreen,multiplayer_modes.splitscreenonline,"
    "platforms.id,platforms.name,release_dates.id,release_dates.platform.id,release_dates.platform.name,"
    "release_dates.date,release_dates.date_format.format,release_dates.category,release_dates.y,release_dates.m,"
    "release_dates.d,release_dates.human,release_dates.region,release_dates.release_region.region,release_dates.status.name,"
    "themes.name,age_ratings.synopsis,age_ratings.rating_category.rating,age_ratings.organization.name,"
    "age_ratings.rating_content_descriptions.description,age_ratings.rating_content_descriptions.description_type.name,"
    "age_ratings.content_descriptions.description,game_localizations.name,game_localizations.region.identifier,"
    "alternative_names.name,alternative_names.comment,external_games.uid,external_games.game,"
    "external_games.external_game_source.name,cover.url,websites.url"
)


class GameLookupError(CollectionError):
    """Safe ID-only diagnostics; never expose raw API responses or credentials."""

    def __init__(self, *, requested, missing, recovery_requests, unexpected=(), duplicates=(),
                 invalid_rows=0, reason="game_lookup_incomplete", http_status=None):
        super().__init__(reason, http_status)
        self.lookup_details = {
            "requested_ids": sorted(requested), "missing_ids": sorted(missing),
            "unexpected_ids": sorted(set(unexpected))[:100],
            "duplicate_ids": sorted(set(duplicates))[:100],
            "unexpected_count": len(set(unexpected)), "duplicate_count": len(set(duplicates)),
            "invalid_rows": invalid_rows, "recovery_requests": recovery_requests,
        }


def lookup_games(client, ids, *, max_recovery_requests=20):
    """Recover short HTTP-200 batches without treating missing games as deleted.

    Only absent IDs are retried: first in groups of ten, then individually.
    Recovery has one collection-wide request limit, in addition to the client's
    existing rate limit and deadline. Invalid/foreign/duplicate rows stay fatal.
    """
    raw_games, recovery_requests, initial_missing_count = [], 0, 0
    for offset in range(0, len(ids), 100):
        batch = ids[offset:offset + 100]
        found = {}

        def request(requested):
            joined = ",".join(str(id_) for id_ in requested)
            try:
                rows = client.query("games", f"fields {GAME_FIELDS}; where id = ({joined}); sort id asc; limit 100;")
            except CollectionError as error:
                raise GameLookupError(requested=requested, missing=set(batch) - found.keys(),
                    recovery_requests=recovery_requests, reason=error.reason, http_status=error.http_status) from None
            received, duplicates, invalid_rows = {}, set(), 0
            if not isinstance(rows, list):
                invalid_rows = 1
            else:
                for row in rows:
                    if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
                        invalid_rows += 1
                        continue
                    if row["id"] in received:
                        duplicates.add(row["id"])
                    received[row["id"]] = row
            unexpected = received.keys() - set(requested)
            if invalid_rows or unexpected or duplicates:
                raise GameLookupError(requested=requested,
                    missing=set(batch) - found.keys() - received.keys(),
                    unexpected=unexpected, duplicates=duplicates, invalid_rows=invalid_rows,
                    recovery_requests=recovery_requests)
            found.update(received)

        request(batch)
        initial_missing_count += len(set(batch) - found.keys())
        for size in (10, 1):
            pending = sorted(set(batch) - found.keys())
            for index in range(0, len(pending), size):
                requested = pending[index:index + size]
                if recovery_requests >= max_recovery_requests:
                    raise GameLookupError(requested=requested, missing=set(batch) - found.keys(),
                                          recovery_requests=recovery_requests)
                recovery_requests += 1
                request(requested)
        missing = set(batch) - found.keys()
        if missing:
            raise GameLookupError(requested=batch, missing=missing, recovery_requests=recovery_requests)
        raw_games.extend(found[id_] for id_ in batch)
    return raw_games, {"initial_missing_count": initial_missing_count,
                       "recovered_count": initial_missing_count,
                       "recovery_requests": recovery_requests,
                       "max_recovery_requests": max_recovery_requests}


def load_existing(path):
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "games": {}}
    try:
        existing = decode_master(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raise CollectionError("invalid_previous_master") from None
    if not isinstance(existing, dict) or existing.get("schema_version") != 1 or not isinstance(existing.get("games"), dict):
        raise CollectionError("invalid_previous_master")
    for key, row in existing["games"].items():
        if not isinstance(row, dict) or type(row.get("igdb_id")) is not int or row["igdb_id"] <= 0 or key != "igdb:" + str(row["igdb_id"]):
            raise CollectionError("invalid_previous_master")
    return existing


def collect(client, *, start: date, previous=None, checked_at=None, page_size=500, max_pages=100,
            name_registry=None, name_client=None, release_registry=None, language_registry=None,
            playstation_client=None, official_description_client=None):
    client.verify_platforms()
    end = start + timedelta(days=365)
    # The year branch also retains month/quarter/year dates overlapping the window.
    # A timestamp attached to a month is never mistaken for a precise day.
    # Query the same Taipei calendar window used by the date conversion. This
    # includes a previous UTC year's evening that becomes January 1 in Taipei.
    taipei = ZoneInfo("Asia/Taipei")
    lower = int(datetime.combine(start, datetime.min.time(), tzinfo=taipei).timestamp())
    upper = int(datetime.combine(end, datetime.min.time(), tzinfo=taipei).timestamp())
    where = f"platform = (130,167,508) & ((date >= {lower} & date < {upper}) | (y >= {start.year} & y <= {end.year}) | date = null)"
    releases = paginated_releases(client, where, page_size=page_size, max_pages=max_pages)
    game_ids = {row["game"] for row in releases}
    game_ids.update(row["igdb_id"] for row in (previous or {}).get("games", {}).values())
    if len(game_ids) > 50000:
        raise CollectionError("candidate_limit_exceeded")
    ids = sorted(game_ids)
    raw_games, lookup_report = lookup_games(client, ids)
    if checked_at is None:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    master, catalog, status = build_documents(raw_games, start=start, checked_at=checked_at, previous=previous,
                           name_registry=name_registry,
                           release_registry=release_registry,
                           language_registry=language_registry,
                           source={"discovery_release_count": len(releases), "request_count": client.request_count,
                                   "retry_count": client.retry_count, "platform_ids_verified": [130, 167, 508],
                                   "game_lookup": lookup_report,
                                   "candidate_scope": "NS/NS2/PS5 native platform release records: window/overlapping year/undated, plus previous ledger"})
    if name_client is not None:
        status["name_enrichment"] = enrich_documents(master, catalog, name_client)
    if playstation_client is not None:
        admitted = {row["igdb_id"]: row for row in catalog["games"] if any(p["code"] == "PS5" for p in row["platforms"])}
        last_report = (previous or {}).get("playstation_investigation") or {}
        prior_checks = last_report.get("games", {}) if isinstance(last_report, dict) else {}
        def previous_attempt(raw):
            record = prior_checks.get("igdb:" + str(raw["id"])) if isinstance(prior_checks, dict) else None
            if not isinstance(record, dict) or not record.get("attempted_urls"):
                return ""
            try:
                checked = datetime.fromisoformat(record["checked_at"].replace("Z", "+00:00"))
                if checked.utcoffset() is not None and checked <= datetime.fromisoformat(checked_at.replace("Z", "+00:00")):
                    return checked.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
            return ""
        investigation_games = sorted([row for row in raw_games if row["id"] in admitted],
            key=lambda row: (not any(r["platform"] == "PS5" for r in admitted[row["id"]]["releases"]),
                             previous_attempt(row),
                             -(row.get("hypes") or 0), row["id"]))
        # Investigation reports are review material. Only reviewed repository
        # registries affect languages/store identity; dates use IGDB only.
        status["playstation_investigation"] = {"review_only": True,
            **playstation_client.investigate(investigation_games)}
        master["playstation_investigation"] = deepcopy(status["playstation_investigation"])
    status["description_enrichment"] = enrich_descriptions(
        master, catalog, official_client=official_description_client,
        language_registry=language_registry, release_registry=release_registry, name_registry=name_registry)
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
    parser.add_argument("--nintendo-languages", default="data/nintendo_languages.json")
    parser.add_argument("--playstation-releases", default="data/playstation_release_dates.json")
    parser.add_argument("--playstation-languages", default="data/playstation_languages.json")
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    out = Path(args.output_dir)
    try:
        previous = load_existing(args.existing)
        name_registry = load_registry(args.chinese_names)
        release_registry = load_release_registry(args.taiwan_releases, args.playstation_releases)
        language_registry = load_language_registry(args.nintendo_languages, args.playstation_languages)
        client = IGDBClient(os.environ.get("TWITCH_CLIENT_ID", ""), os.environ.get("TWITCH_CLIENT_SECRET", ""))
        start = args.today or datetime.now(ZoneInfo("Asia/Taipei")).date()
        official_descriptions = OfficialDescriptionClient()
        try:
            master, catalog, status = collect(client, start=start, previous=previous,
                                             name_registry=name_registry, name_client=SteamNameClient(),
                                             release_registry=release_registry, language_registry=language_registry,
                                             playstation_client=PlayStationStoreClient(),
                                             official_description_client=official_descriptions)
        finally:
            official_descriptions.close()
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
        if isinstance(exc, GameLookupError):
            failure["game_lookup"] = exc.lookup_details
        try:
            write_json_atomic(out / "nintendo_refresh_status.json", failure)
        except OSError:
            pass
        print(json.dumps(failure, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
