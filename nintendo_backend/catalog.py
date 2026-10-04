"""Evidence-based calendar eligibility and per-platform release dates."""
from __future__ import annotations

import calendar
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .chinese_names import names, previous_lookup
from .igdb import CollectionError
from .languages import platform_editions, platform_language_support
from .taiwan_releases import official_releases

PLATFORMS = {130: {"id": 130, "name": "Nintendo Switch", "code": "NS"},
             167: {"id": 167, "name": "PlayStation 5", "code": "PS5"},
             508: {"id": 508, "name": "Nintendo Switch 2", "code": "NS2"}}
FORMATS = {0: "YYYYMMMMDD", 1: "YYYYMMMM", 2: "YYYY", 3: "YYYYQ1",
           4: "YYYYQ2", 5: "YYYYQ3", 6: "YYYYQ4", 7: "TBD"}
REGIONS = {1: "europe", 2: "north_america", 3: "australia", 4: "new_zealand",
           5: "japan", 6: "china", 7: "asia", 8: "worldwide", 9: "korea", 10: "brazil"}
ALLOWED_TYPES = {"main_game", "standalone_expansion", "remake", "remaster", "expanded_game", "port"}
CATEGORIES = {0: "main_game", 4: "standalone_expansion", 8: "remake", 9: "remaster", 10: "expanded_game", 11: "port"}
EXPLICIT = re.compile(r"\b(?:hentai|pornograph(?:y|ic)|sex game|adult game|sexually explicit|"
                      r"explicit sexual|uncensored sexual|sex scenes|sexual acts|lots of sex)\b", re.I)
STRONG_CONTENT = {"strong sexual content", "explicit sexual content", "explicit sex", "sexo explicito"}
TAIPEI = ZoneInfo("Asia/Taipei")
RELEASE_FIELDS = ("date", "platform", "precision", "region", "source", "date_basis",
                  "source_date", "source_timestamp", "source_region", "time_zone",
                  "timestamp_taipei_date", "timezone_status", "taiwan_release_confirmed",
                  "official_source_url", "official_source_name", "official_verified_at",
                  "official_product_id", "official_release_time_utc")


def reference(value, field="name"):
    if type(value) is int and value > 0:
        return {"id": value}
    if not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] <= 0:
        raise CollectionError("invalid_reference")
    result = {"id": value["id"]}
    if isinstance(value.get(field), str):
        result[field] = value[field]
    return result


def safe_url(value, allowed_domains=None):
    if not isinstance(value, str) or len(value) > 2000:
        return None
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    if allowed_domains and not any(parsed.hostname == host or parsed.hostname.endswith("." + host) for host in allowed_domains):
        return None
    return value


def release_record(row):
    if not isinstance(row, dict):
        raise CollectionError("invalid_release_metadata")
    platform_ref = reference(row.get("platform"))
    platform_id = platform_ref["id"]
    if platform_id not in PLATFORMS:
        return None
    format_ref = row.get("date_format")
    fmt = format_ref.get("format") if isinstance(format_ref, dict) else None
    if not fmt:
        fmt = FORMATS.get(row.get("category")) if type(row.get("category")) is int else None
    fmt = str(fmt).upper().replace(" ", "") if fmt else "UNKNOWN"
    exact = fmt in {"YYYYMMMMDD", "YYYYMMDD"}
    timestamp = row.get("date")
    utc_day = taipei_day = None
    if timestamp is not None:
        if type(timestamp) is not int or timestamp < 0:
            raise CollectionError("invalid_release_timestamp")
        try:
            utc_value = datetime.fromtimestamp(timestamp, timezone.utc)
            utc_day = utc_value.date()
            taipei_day = utc_value.astimezone(TAIPEI).date()
        except (ValueError, OverflowError, OSError):
            raise CollectionError("invalid_release_timestamp") from None
    year, month, day = row.get("y"), row.get("m"), row.get("d")
    if any(value is not None and type(value) is not int for value in (year, month, day)):
        raise CollectionError("invalid_release_parts")
    start = end = None
    precision = "unknown"
    if exact:
        if all(value is not None for value in (year, month, day)):
            try:
                nominal = date(year, month, day)
            except ValueError:
                raise CollectionError("invalid_release_parts") from None
            if utc_day is not None and nominal != utc_day:
                raise CollectionError("release_date_conflict")
            start = end = nominal
        else:
            start = end = utc_day
        precision = "day" if start else "unknown"
    elif year is not None:
        try:
            if fmt in {"YYYYMMMM", "YYYYMM"} and month is not None:
                start, end = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
                precision = "month"
            elif fmt in {"YYYYQ1", "YYYYQ2", "YYYYQ3", "YYYYQ4"}:
                quarter_month = (int(fmt[-1]) - 1) * 3 + 1
                end_month = quarter_month + 2
                start, end = date(year, quarter_month, 1), date(year, end_month, calendar.monthrange(year, end_month)[1])
                precision = "quarter"
            elif fmt == "YYYY":
                start, end, precision = date(year, 1, 1), date(year, 12, 31), "year"
        except ValueError:
            raise CollectionError("invalid_release_parts") from None
    region_ref = row.get("release_region")
    region = region_ref.get("region") if isinstance(region_ref, dict) else None
    if not isinstance(region, str) or not region:
        region = REGIONS.get(row.get("region"), "unknown") if type(row.get("region")) is int else "unknown"
    region = region.lower().replace(" ", "_")
    status = row.get("status") or {}
    status_name = status.get("name") if isinstance(status, dict) else None
    # IGDB's YYYYMMDD field has day precision, not an actual unlock time.
    # Preserve the regional calendar day and audit its timestamp in Taipei.
    # A cross-day conversion needs evidence of an instant before it may alter
    # a release date; silently adding a day would invent a regional date.
    source_day = start.isoformat() if exact and start else None
    converted_day = taipei_day.isoformat() if taipei_day is not None else None
    timezone_status = ("imprecise_date" if precision != "day" else "date_only" if timestamp is None
                       else "same_calendar_day" if source_day == converted_day else "requires_time_evidence")
    return {"id": row.get("id"), "platform": PLATFORMS[platform_id]["code"],
            "platform_id": platform_id, "date": start.isoformat() if exact and start else None,
            "range_start": start.isoformat() if start else None, "range_end": end.isoformat() if end else None,
            "precision": precision, "region": region, "status": status_name,
            "human": row.get("human") if isinstance(row.get("human"), str) else None,
            "source": "IGDB", "date_basis": "regional_calendar_day",
            "source_date": source_day, "source_timestamp": timestamp, "source_region": region,
            "time_zone": "Asia/Taipei", "timestamp_taipei_date": converted_day,
            "timezone_status": timezone_status, "taiwan_release_confirmed": False,
            "official_source_url": None, "official_source_name": None, "official_verified_at": None,
            "official_product_id": None, "official_release_time_utc": None}


def content_policy(game):
    """A mature rating or a lone sexuality tag is not an adult-game verdict."""
    themes = [ref.get("name", "") for ref in game.get("themes", []) if isinstance(ref, dict)]
    ratings = deepcopy(game.get("age_ratings") or [])
    if not isinstance(ratings, list) or any(not isinstance(row, dict) for row in ratings):
        raise CollectionError("invalid_age_rating_metadata")
    descriptions = []
    for rating in ratings:
        for field in ("rating_content_descriptions", "content_descriptions"):
            for item in rating.get(field) or []:
                if isinstance(item, dict):
                    descriptions.extend(text for text in (item.get("description"), (item.get("description_type") or {}).get("name")) if isinstance(text, str))
        if isinstance(rating.get("synopsis"), str):
            descriptions.append(rating["synopsis"])
    summary = game.get("summary") if isinstance(game.get("summary"), str) else ""
    normalized = {re.sub(r"[_-]", " ", text).lower().strip() for text in descriptions}
    strong = any(text in STRONG_CONTENT or EXPLICIT.search(text) for text in normalized)
    erotic = any(name.lower().strip() in {"erotic", "erotica"} for name in themes)
    explicit_primary = bool(EXPLICIT.search(summary))
    if strong or (erotic and explicit_primary):
        status, reason = "excluded", "explicit_sexual_evidence"
    elif erotic or explicit_primary:
        status, reason = "review", "ambiguous_sexual_evidence"
    elif summary.strip() or descriptions:
        status, reason = "screened", "no_explicit_sexual_evidence"
    else:
        status, reason = "review", "insufficient_content_evidence"
    return {"policy_version": "nintendo-2026-10-04-v1", "status": status, "reason": reason,
            "source": "IGDB", "themes": themes, "age_ratings": ratings, "summary_present": bool(summary.strip())}


def normalize_game(game, start: date, end: date, checked_at: str, *, previous=None, name_registry=None,
                   release_registry=None, language_registry=None):
    if not isinstance(game, dict) or type(game.get("id")) is not int or game["id"] <= 0:
        raise CollectionError("invalid_game_identity")
    hypes = game.get("hypes")
    if hypes is not None and (type(hypes) is not int or hypes < 0):
        raise CollectionError("invalid_hypes")
    raw_platforms = game.get("platforms")
    platform_complete = isinstance(raw_platforms, list) and bool(raw_platforms)
    if raw_platforms is not None and not isinstance(raw_platforms, list):
        raise CollectionError("invalid_platform_metadata")
    known = [reference(row) for row in raw_platforms or []]
    known_ids = {row["id"] for row in known}
    release_rows = game.get("release_dates") or []
    if not isinstance(release_rows, list):
        raise CollectionError("invalid_release_metadata")
    releases = [record for row in release_rows if (record := release_record(row)) is not None]
    for row in releases:
        if row["platform_id"] not in known_ids:
            known.append(deepcopy(PLATFORMS[row["platform_id"]]))
            known_ids.add(row["platform_id"])
            platform_complete = False
    known = sorted({row["id"]: row for row in known}.values(), key=lambda row: row["id"])
    for row in known:
        if row["id"] in PLATFORMS:
            row.update(PLATFORMS[row["id"]])
    tracked = [deepcopy(PLATFORMS[id_]) for id_ in sorted(known_ids & PLATFORMS.keys())]
    # Keep each IGDB record intact, including region and timestamp. A reviewed
    # Taiwan product/announcement date is a separate record and takes priority.
    for official in official_releases(game, release_registry):
        platform_id = next(id_ for id_, platform in PLATFORMS.items() if platform["code"] == official["platform"])
        if platform_id not in known_ids:
            continue
        originals = [row for row in releases if row["platform_id"] == platform_id and row["precision"] == "day"]
        ranks = {"asia": 0, "worldwide": 1, "japan": 2, "china": 3, "korea": 4}
        if originals:
            best_rank = min(ranks.get(row["region"], 5) for row in originals)
            originals = [row for row in originals if ranks.get(row["region"], 5) == best_rank]
        original = originals[0] if originals and len({row["date"] for row in originals}) == 1 else None
        # A later changed/ambiguous IGDB date invalidates this reviewed snapshot.
        # The same Taiwan date appearing in IGDB does not invalidate it.
        if not original or original["date"] not in {official["verified_source_date"], official["date"]}:
            continue
        row = deepcopy(original) if original else {
            "source_date": None, "source_timestamp": None, "source_region": "unknown",
            "time_zone": "Asia/Taipei", "timestamp_taipei_date": None}
        row.update({"id": None, "platform": official["platform"], "platform_id": platform_id,
                    "date": official["date"], "range_start": official["date"], "range_end": official["date"],
                    "precision": "day", "region": "taiwan", "status": None, "human": official["date"],
                    "source": "official_registry", "date_basis": "taiwan_official_calendar_day",
                    "timezone_status": "taiwan_official_date", "taiwan_release_confirmed": True,
                    "official_source_url": official["url"], "official_source_name": official["source"],
                    "official_verified_at": official["verified_at"],
                    "official_product_id": official.get("product_id"),
                    "official_release_time_utc": official.get("release_time_utc")})
        releases.append(row)
    outside = known_ids - PLATFORMS.keys()
    exclusive_status = "multi_platform" if outside or (platform_complete and len(known_ids) > 1) else "listed_only" if platform_complete else "unknown"
    exclusive_platform = tracked[0]["code"] if len(tracked) == 1 and not outside and platform_complete else None
    game_type_ref = game.get("game_type") or {}
    game_type = game_type_ref.get("type") if isinstance(game_type_ref, dict) else None
    if isinstance(game_type, str):
        game_type = game_type.lower().replace(" ", "_")
    else:
        game_type = CATEGORIES.get(game.get("category")) if type(game.get("category")) is int else None
    game_status_ref = game.get("game_status") or {}
    game_status = game_status_ref.get("status") if isinstance(game_status_ref, dict) else None
    cancelled = str(game_status or "").lower() in {"cancelled", "canceled", "rumored", "rumoured"} or game.get("status") in {6, 7}
    screening = content_policy(game)
    popularity = "qualified" if hypes is not None and hypes >= 30 else "observe" if hypes is not None and hypes >= 20 else "unknown" if hypes is None else "below_threshold"
    possible = [r for r in releases if (not r["range_start"] or not r["range_end"] or
                (date.fromisoformat(r["range_start"]) < end and date.fromisoformat(r["range_end"]) >= start))]
    calendar_releases = []
    # Region priority, including a more relevant known date outside the window,
    # prevents incorrectly replacing an earlier Asia date with a later US date.
    for code in ("NS", "NS2", "PS5"):
        exact = [r for r in releases if r["platform"] == code and r["precision"] == "day"
                 and not str(r["status"] or "").lower().startswith(("cancel", "rumor"))]
        if not exact:
            continue
        ranks = {"taiwan": -1, "asia": 0, "worldwide": 1, "japan": 2, "china": 3, "korea": 4}
        best_rank = min(ranks.get(r["region"], 5) for r in exact)
        best = [r for r in exact if ranks.get(r["region"], 5) == best_rank]
        dates = {r["date"] for r in best}
        if len(dates) != 1:
            continue  # Conflicting equally relevant records require review.
        selected = sorted(best, key=lambda row: (row["region"], row.get("id") or 0))[0]
        if selected["timezone_status"] == "requires_time_evidence":
            continue
        if start <= date.fromisoformat(selected["date"]) < end:
            calendar_releases.append({key: selected[key] for key in RELEASE_FIELDS})
    reasons = []
    if not tracked:
        reasons.append("no_tracked_native_platform")
    if game_type not in ALLOWED_TYPES:
        reasons.append("not_standalone_game")
    if cancelled:
        reasons.append("cancelled_or_rumored")
    if screening["status"] != "screened":
        reasons.append("content_" + screening["status"])
    if popularity != "qualified":
        reasons.append("hypes_" + popularity)
    if not calendar_releases:
        reasons.append("no_exact_release_in_window")
    url = safe_url(game.get("url"), ["igdb.com"])
    cover = game.get("cover") or {}
    cover_url = safe_url(cover.get("url") if isinstance(cover, dict) else None, ["images.igdb.com"])
    if cover_url:
        cover_url = cover_url.replace("/t_thumb/", "/t_cover_big/")
    websites = []
    for website in game.get("websites") or []:
        if isinstance(website, dict) and (website_url := safe_url(website.get("url"))):
            websites.append({"url": website_url})
    nintendo_urls = [row["url"] for row in websites if safe_url(row["url"], ["nintendo.com", "nintendo.co.jp", "nintendo.com.hk"])]
    from .playstation import official_url as playstation_official_url
    playstation_urls = [row["url"] for row in websites if playstation_official_url(row["url"])]
    # Reviewed platform evidence can identify a regional store product even
    # when IGDB has not linked it yet. Never copy a Nintendo URL to PS5.
    for evidence in list(platform_language_support(game, tracked, language_registry).values()) + list(platform_editions(game, tracked, language_registry).values()):
        if (link := playstation_official_url(evidence.get("source_url"))) and link not in playstation_urls:
            playstation_urls.insert(0, link)
    for evidence in official_releases(game, release_registry):
        if evidence["platform"] == "PS5" and (link := playstation_official_url(evidence["url"])) and link not in playstation_urls:
            playstation_urls.insert(0, link)
    result = {"id": "igdb:" + str(game["id"]), "igdb_id": game["id"],
              **names(game, registry=name_registry, previous=previous),
              "hypes": hypes, "hypes_status": "missing" if hypes is None else "available",
              "popularity_status": popularity, "platforms": tracked, "known_platforms": known,
              "platform_language_support": platform_language_support(game, tracked, language_registry),
              "platform_editions": platform_editions(game, tracked, language_registry),
              "platform_data_complete": platform_complete,
              "exclusivity": {"status": exclusive_status, "platform": exclusive_platform, "source": "IGDB", "url": url},
              "releases": calendar_releases, "release_records": releases, "cover_image": cover_url, "url": url,
              "websites": websites, "nintendo_url": nintendo_urls[0] if nintendo_urls else None,
              "playstation_url": playstation_urls[0] if playstation_urls else None,
              "platform_urls": {"PS5": playstation_urls[0]} if playstation_urls else {},
              "game_type": game_type, "content_screening": screening,
              "sexual_content_screened": screening["status"] == "screened", "calendar_eligible": not reasons,
              "eligibility_reasons": reasons, "possible_in_window": bool(possible), "checked_at": checked_at,
              "first_observed_at": (previous or {}).get("first_observed_at", checked_at), "raw": deepcopy(game)}
    history = deepcopy((previous or {}).get("platform_history") or [])
    if not isinstance(history, list):
        raise CollectionError("invalid_previous_history")
    snapshot = {"known_platforms": deepcopy(known), "release_records": deepcopy(releases)}
    latest = history[-1] if history else None
    if latest is None or any(latest.get(key) != value for key, value in snapshot.items()):
        history.append({"checked_at": checked_at, **snapshot})
    result["platform_history"] = history[-100:]
    if lookup := previous_lookup(game, previous):
        result["steam_name_lookup"] = lookup
    return result


def build_documents(raw_games, *, start: date, checked_at: str, previous=None, source=None, name_registry=None,
                    release_registry=None, language_registry=None):
    end = start + timedelta(days=365)
    prior_games = (previous or {}).get("games") or {}
    games = {}
    for raw in raw_games:
        key = "igdb:" + str(raw.get("id")) if isinstance(raw, dict) else "invalid"
        if key in games:
            raise CollectionError("duplicate_game_identity")
        games[key] = normalize_game(raw, start, end, checked_at, previous=prior_games.get(key),
                                    name_registry=name_registry, release_registry=release_registry,
                                    language_registry=language_registry)
    missing_prior = set(prior_games) - set(games)
    if missing_prior:
        raise CollectionError("previous_game_lookup_incomplete")
    window = {"start": start.isoformat(), "end": end.isoformat(), "end_inclusive": False, "time_zone": "Asia/Taipei"}
    provenance = {"provider": "IGDB", "complete": True, "hypes_threshold": 30, **(source or {})}
    master = {"schema_version": 1, "generated_at": checked_at, "window": window, "source": provenance, "games": games}
    public = []
    for row in games.values():
        if row["calendar_eligible"]:
            public.append({key: deepcopy(value) for key, value in row.items()
                           if key not in {"raw", "content_screening", "eligibility_reasons", "release_records", "possible_in_window", "platform_history", "steam_name_lookup"}})
    public.sort(key=lambda row: (min(r["date"] for r in row["releases"]), row["id"]))
    counts = {status: sum(row["popularity_status"] == status for row in games.values())
              for status in ("qualified", "observe", "below_threshold", "unknown")}
    catalog = {"schema_version": 1, "generated_at": checked_at, "window": window, "source": provenance, "games": public}
    refresh = {"schema_version": 1, "generated_at": checked_at, "complete": True, "status": "complete",
               "candidate_count": len(games), "public_count": len(public), "popularity_counts": counts,
               "content_review_count": sum(row["content_screening"]["status"] == "review" for row in games.values()),
               "content_excluded_count": sum(row["content_screening"]["status"] == "excluded" for row in games.values()),
               "pending_count": 0, "source": provenance}
    return master, catalog, refresh
