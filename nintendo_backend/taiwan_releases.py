"""Repository-reviewed native platform release dates for Taiwan.

An IGDB worldwide/Asia date and a Unix timestamp do not independently prove
Taiwan availability. Official calendar dates are bound to game and platform.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .igdb import CollectionError
from .playstation import official_url as playstation_url, product_id as playstation_product_id

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "nintendo_release_dates.json"
PLAYSTATION_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "playstation_release_dates.json"
OFFICIAL_DOMAINS = {"www.nintendo.com", "asia.sega.com", "www.playtombraider.com", "www.konami.com"}


def validate_registry(document):
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or not isinstance(document.get("games"), dict)):
        raise CollectionError("invalid_taiwan_release_registry")
    for key, game in document["games"].items():
        if (not isinstance(game, dict) or type(game.get("igdb_id")) is not int or game["igdb_id"] <= 0
                or key != "igdb:" + str(game["igdb_id"])
                or not isinstance(game.get("name_en"), str) or not 0 < len(game["name_en"].strip()) <= 240
                or not isinstance(game.get("releases"), list) or not game["releases"]):
            raise CollectionError("invalid_taiwan_release_registry")
        platforms = set()
        for row in game["releases"]:
            try:
                if not isinstance(row, dict):
                    raise ValueError
                day = date.fromisoformat(row["date"])
                original_day = date.fromisoformat(row["verified_source_date"])
                checked = datetime.fromisoformat(row["verified_at"].replace("Z", "+00:00"))
                url = urlparse(row["url"])
                sony = row.get("platform") == "PS5"
                source_valid = (playstation_url(row.get("url"), region="taiwan") is not None if sony else
                                url.hostname in OFFICIAL_DOMAINS and
                                (url.hostname != "www.nintendo.com" or url.path.startswith("/tw/")))
                store_id = playstation_product_id(row.get("url")) if sony else None
                time_matches = True
                if sony and "release_time_utc" in row:
                    stamp = datetime.fromisoformat(row["release_time_utc"].replace("Z", "+00:00"))
                    time_matches = stamp.utcoffset() is not None and stamp.astimezone(ZoneInfo("Asia/Taipei")).date() == day
                if (row.get("platform") not in {"NS", "NS2", "PS5"} or row["platform"] in platforms
                        or day.isoformat() != row["date"]
                        or original_day.isoformat() != row["verified_source_date"] or checked.utcoffset() is None
                        or not isinstance(row.get("source"), str) or not 0 < len(row["source"].strip()) <= 240
                        or url.scheme != "https" or not source_valid
                        or (sony and store_id and row.get("product_id") != store_id)
                        or not time_matches
                        or url.username or url.password or url.port is not None
                        or len(row["url"]) > 2000):
                    raise ValueError
                platforms.add(row["platform"])
            except (ValueError, TypeError, KeyError, AttributeError):
                raise CollectionError("invalid_taiwan_release_registry") from None
    return document["games"]


def load_registry(path=REGISTRY_PATH):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise CollectionError("invalid_taiwan_release_registry") from None
    return validate_registry(document)


def load_combined_registry(nintendo_path=REGISTRY_PATH, playstation_path=PLAYSTATION_REGISTRY_PATH):
    """Merge independently reviewed registries without replacing another version."""
    result = deepcopy(load_registry(nintendo_path))
    for key, game in load_registry(playstation_path).items():
        if key not in result:
            result[key] = deepcopy(game)
            continue
        existing = result[key]
        if existing["name_en"] != game["name_en"] or existing["igdb_id"] != game["igdb_id"]:
            raise CollectionError("conflicting_taiwan_release_registry_identity")
        platforms = {row["platform"] for row in existing["releases"]}
        if platforms & {row["platform"] for row in game["releases"]}:
            raise CollectionError("conflicting_taiwan_release_registry_platform")
        existing["releases"].extend(deepcopy(game["releases"]))
    return result


def official_releases(game, registry):
    if type(game.get("id")) is not int or not isinstance(game.get("name"), str):
        return []
    official = (registry or {}).get("igdb:" + str(game["id"]))
    if (not official or official.get("igdb_id") != game["id"]
            or official.get("name_en") != game["name"].strip()):
        return []
    return deepcopy(official["releases"])
