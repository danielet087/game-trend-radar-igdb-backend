"""Taiwan store data, adopted ahead of IGDB and kept current.

PS5: the Taiwan PlayStation Store, checked per game on a cadence. NS: Nintendo
Taiwan's software list (nintendo_taiwan), read once a day for every game.

The store items IGDB itself links to a PS5 game are read on the Taiwan
storefront (playstation.PlayStationStoreClient); an item must name the same
English game, native PS5 version and standard edition
(playstation.parse_store_metadata). Its release instant then replaces IGDB's
worldwide PS5 day, its PS5 languages fill the version and its page becomes the
PS5 shop link.

The verified result is kept per game in the full master, so the publisher's
rebuild reaches the same rows. It is used only while it is current: confirmed
within MAX_AGE, or confirmed on or after its own release day. Games keep being
checked on a cadence (is_due) whether or not the store has them yet. Reviewed
repository registries stay link and language sources only: a snapshot of a
date can go stale, a store that is checked again cannot.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .languages import LANGUAGE_NAMES, support_entry
from . import nintendo_taiwan
from .playstation import (concept_id as sony_concept_id, official_url as playstation_url,
                          product_id as sony_product_id, taiwan_store_urls)

TAIPEI = ZoneInfo("Asia/Taipei")
FIELD = "official_store"
# IGDB's platform day in Taiwan time, after a Taiwan store's own release day.
RELEASE_DATE_POLICY = "taiwan_store_then_igdb_taipei_v1"
SOURCE_NAME = "PlayStation Store 台灣"
MAX_AGE = timedelta(days=14)
FAR_INTERVAL_DAYS, NEAR_WINDOW_DAYS = 3, 14
# A check that answered (the item is confirmed, or the store does not have it)
# versus one that did not (an error, the request budget or a rate limit).
ANSWERS = {"confirmed", "unconfirmed", "no_url"}
OUTCOMES = ANSWERS | {"unavailable", "rate_limited", "budget_exhausted"}
EVIDENCE_TYPES = {"official_product_languages", "official_chinese_unspecified", "official_concept_release_time"}
NINTENDO_OUTCOMES = {"confirmed", "unconfirmed", "unavailable", "rate_limited"}


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _day(value):
    stamp = _stamp(value)
    return stamp.astimezone(TAIPEI).date() if stamp else None


def _identity(state, game):
    return (isinstance(state, dict) and isinstance(game, dict) and type(game.get("id")) is int
            and state.get("igdb_id") == game["id"] and isinstance(game.get("name"), str)
            and state.get("name_en") == game["name"].strip())


def valid_evidence(value):
    """A Taiwan store item's verified data, normalized; None when anything is off."""
    if not isinstance(value, dict) or value.get("region") != "taiwan":
        return None
    url = playstation_url(value.get("source_url"), region="taiwan")
    if not url or url != value.get("source_url") or "/zh-hant-tw/" not in url or "store.playstation.com" not in url:
        return None
    concept = sony_concept_id(url)
    product = None if concept else sony_product_id(url)
    title = value.get("official_title")
    if ((concept and (value.get("concept_id") != concept or value.get("product_id") is not None))
            or (product and (value.get("product_id") != product or value.get("concept_id") is not None))
            or not isinstance(title, str) or not 0 < len(title.strip()) <= 500):
        return None
    result = {"region": "taiwan", "source_url": url, "product_id": product, "concept_id": concept,
              "official_title": title, "date": None, "release_time_utc": None,
              "supported_languages": [], "complete": False, "evidence_type": None}
    instant = value.get("release_time_utc")
    if instant is not None or value.get("date") is not None:
        stamp = _stamp(instant)
        if not stamp or value.get("date") != stamp.astimezone(TAIPEI).date().isoformat():
            return None
        result.update(date=value["date"], release_time_utc=instant)
    elif concept:
        return None  # A concept proves its identity only together with a release instant.
    codes, complete, kind = value.get("supported_languages"), value.get("complete"), value.get("evidence_type")
    if (not isinstance(codes, list) or len(codes) != len(set(codes)) or any(code not in LANGUAGE_NAMES for code in codes)
            or type(complete) is not bool or (kind is not None and kind not in EVIDENCE_TYPES)
            or (complete and (kind != "official_product_languages" or not codes))
            or (kind == "official_chinese_unspecified" and (complete or codes != ["zh"]))
            or (kind == "official_product_languages" and not codes)
            or (codes and kind not in {"official_product_languages", "official_chinese_unspecified"})):
        return None
    result.update(supported_languages=list(codes), complete=complete, evidence_type=kind)
    return result


def validated_state(game, state, checked_at, tracked=("PS5", "NS")):
    """The stored check state for this exact game and its tracked platforms, or None.

    PS5 evidence stays bound to the store item IGDB links today: when IGDB no
    longer links it, the evidence is dropped while the check history is kept.
    The Switch list is matched again every day, so its evidence needs no link.
    """
    now = _stamp(checked_at)
    if not now or not _identity(state, game):
        return None
    result = {"igdb_id": game["id"], "name_en": game["name"].strip()}
    if "PS5" in tracked and (entry := _validated_ps5(state.get("PS5"), game, now)):
        result["PS5"] = entry
    if "NS" in tracked and (entry := _validated_switch(state.get("NS"), now)):
        result["NS"] = entry
    return result if len(result) > 2 else None


def _times(entry, now):
    checked, attempted = entry.get("checked_at"), entry.get("attempted_at")
    return ((checked is None or (_stamp(checked) and _stamp(checked) <= now))
            and _stamp(attempted) is not None and _stamp(attempted) <= now
            and (checked is None or _stamp(checked) <= _stamp(attempted)))


def _validated_ps5(entry, game, now):
    if not isinstance(entry, dict):
        return None
    urls = entry.get("urls")
    if (entry.get("outcome") not in OUTCOMES or not isinstance(urls, list)
            or any(not isinstance(url, str) for url in urls) or len(urls) > 20 or not _times(entry, now)):
        return None
    result = {"outcome": entry["outcome"], "urls": list(urls), "checked_at": entry.get("checked_at"),
              "attempted_at": entry["attempted_at"], "request_url": None, "evidence": None, "confirmed_at": None}
    evidence, confirmed = valid_evidence(entry.get("evidence")), _stamp(entry.get("confirmed_at"))
    if (evidence and confirmed and confirmed <= now and entry.get("request_url") in taiwan_store_urls(game)):
        result.update(request_url=entry["request_url"], evidence=evidence, confirmed_at=entry["confirmed_at"])
    return result


def valid_switch_evidence(value):
    """A Nintendo Taiwan list entry's Taiwan day, normalized; None when anything is off."""
    if (not isinstance(value, dict) or value.get("region") != "taiwan"
            or value.get("source_url") != nintendo_taiwan.LIST_URL
            or not isinstance(value.get("title"), str) or not 0 < len(value["title"].strip()) <= 240
            or value.get("section") not in {"on_sale", "upcoming"} or type(value.get("chinese")) is not bool
            or (value.get("nsuid") is not None and not (isinstance(value["nsuid"], str)
                                                         and len(value["nsuid"]) == 14 and value["nsuid"].isdigit()))):
        return None
    try:
        if date.fromisoformat(value.get("date")).isoformat() != value["date"]:
            return None
    except (TypeError, ValueError):
        return None
    return {key: value[key] for key in ("region", "source_url", "title", "date", "nsuid", "chinese", "section")}


def _validated_switch(entry, now):
    if not isinstance(entry, dict) or entry.get("outcome") not in NINTENDO_OUTCOMES or not _times(entry, now):
        return None
    result = {"outcome": entry["outcome"], "checked_at": entry.get("checked_at"),
              "attempted_at": entry["attempted_at"], "evidence": None, "confirmed_at": None}
    evidence, confirmed = valid_switch_evidence(entry.get("evidence")), _stamp(entry.get("confirmed_at"))
    if evidence and confirmed and confirmed <= now:
        result.update(evidence=evidence, confirmed_at=entry["confirmed_at"])
    return result


def current_evidence(state, checked_at, platform="PS5"):
    """Evidence still current enough to come before IGDB, with its confirmation time."""
    entry = (state or {}).get(platform) or {}
    evidence, confirmed, now = entry.get("evidence"), _stamp(entry.get("confirmed_at")), _stamp(checked_at)
    if not evidence or not confirmed or not now:
        return None, None
    release = date.fromisoformat(evidence["date"]) if evidence["date"] else None
    final = release is not None and confirmed.astimezone(TAIPEI).date() >= release
    return (evidence, entry["confirmed_at"]) if final or now - confirmed <= MAX_AGE else (None, None)


def taiwan_release(evidence, confirmed_at, igdb_record=None, platform="PS5"):
    """The calendar row for a Taiwan store day (PS5: with its release instant).

    IGDB's own day and timestamp stay in the source fields, so the site can say
    which date the Taiwan one corrected.
    """
    if not evidence or not evidence.get("date"):
        return None
    base = igdb_record or {}
    timestamp = base.get("source_timestamp")
    sony = platform == "PS5"
    return {"date": evidence["date"], "platform": platform, "precision": "day", "region": "taiwan",
            "source": "official_registry", "date_basis": "taiwan_official_calendar_day",
            "source_date": base.get("source_date") if igdb_record else evidence["date"],
            "source_timestamp": timestamp if igdb_record else None,
            "source_region": base.get("source_region") if igdb_record else "taiwan",
            "time_zone": "Asia/Taipei",
            "timestamp_taipei_date": base.get("timestamp_taipei_date") if igdb_record else None,
            "timezone_status": "taiwan_official_date", "taiwan_release_confirmed": True,
            "official_source_url": evidence["source_url"],
            "official_source_name": SOURCE_NAME if sony else nintendo_taiwan.SOURCE_NAME,
            "official_verified_at": confirmed_at, "official_product_id": evidence["product_id"] if sony else None,
            "official_concept_id": evidence["concept_id"] if sony else None,
            "official_release_time_utc": evidence["release_time_utc"] if sony else None}


def apply_switch_languages(languages, evidence, confirmed_at):
    """The Taiwan list's 對應中文 mark proves Chinese, not which script; reviewed lists come first."""
    if ("NS" not in languages or not evidence or not evidence["chinese"]
            or languages["NS"].get("status") != "unknown"):
        return False
    languages["NS"] = support_entry({"supported_languages": ["zh"], "complete": False, "region": "taiwan",
        "source": nintendo_taiwan.SOURCE_NAME, "source_url": nintendo_taiwan.LIST_URL, "checked_at": confirmed_at,
        "evidence_type": "official_chinese_unspecified"}, product=False)
    return True


def apply_languages(languages, evidence, confirmed_at):
    """The Taiwan store's PS5 languages, unless a reviewed complete list says more."""
    if "PS5" not in languages or not evidence or not evidence["supported_languages"] or not evidence["product_id"]:
        return False
    existing = languages["PS5"]
    if existing.get("complete") and not evidence["complete"]:
        return False
    languages["PS5"] = support_entry({"supported_languages": evidence["supported_languages"],
        "complete": evidence["complete"], "region": "taiwan", "source": SOURCE_NAME,
        "source_url": evidence["source_url"], "checked_at": confirmed_at,
        "evidence_type": evidence["evidence_type"], "product_id": evidence["product_id"]})
    return True


def is_due(state, game, release_day, today):
    """Every 3 days until release, daily in the last 14 days, once after it.

    release_day is the PS5 day the calendar shows (the Taiwan one when current).
    A changed set of IGDB-linked items is checked at once; a check without an
    answer (an error, the budget or a rate limit) is repeated the next day.
    """
    entry = (state or {}).get("PS5") or {}
    last = _day(entry.get("checked_at"))
    if last is None or entry.get("urls") != taiwan_store_urls(game) or last > today:
        return True
    if release_day is None:
        return (today - last).days >= FAR_INTERVAL_DAYS
    if last >= release_day:
        return False
    if today >= release_day:
        return True
    interval = 1 if (release_day - today).days <= NEAR_WINDOW_DAYS else FAR_INTERVAL_DAYS
    return (today - last).days >= interval


def merge(state, game, report, checked_at):
    """The game's next state from this run's Taiwan result (or None: not checked)."""
    taiwan = report.get("taiwan") if isinstance(report, dict) else None
    previous = (state or {}).get("PS5") or {}
    if not isinstance(taiwan, dict) or taiwan.get("outcome") not in OUTCOMES:
        return deepcopy(state) if state else None
    base = deepcopy(state) if state else {}
    entry = {"outcome": taiwan["outcome"], "urls": taiwan_store_urls(game),
             "checked_at": checked_at if taiwan["outcome"] in ANSWERS else previous.get("checked_at"),
             "attempted_at": checked_at, "request_url": previous.get("request_url"),
             "evidence": deepcopy(previous.get("evidence")), "confirmed_at": previous.get("confirmed_at")}
    if taiwan["outcome"] == "confirmed":
        evidence = valid_evidence(taiwan.get("evidence"))
        entry.update(request_url=taiwan.get("url") if evidence else None, evidence=evidence,
                     confirmed_at=checked_at if evidence else None)
    elif taiwan["outcome"] in ANSWERS:
        # The store answered without this item: its earlier data no longer stands.
        entry.update(request_url=None, evidence=None, confirmed_at=None)
    return {**base, "igdb_id": game["id"], "name_en": game["name"].strip(), "PS5": entry}


def _final(evidence, confirmed_at):
    stamp = _stamp(confirmed_at)
    return bool(evidence and evidence.get("date") and stamp
                and stamp.astimezone(TAIPEI).date() >= date.fromisoformat(evidence["date"]))


def merge_switch(state, game, status, entry, checked_at):
    """The game's next state from today's Taiwan list (or None: nothing to keep).

    An entry confirms its day. A list without the game drops the earlier day,
    unless that was confirmed on or after the release day (the list later
    leaves games out); a list that could not be read keeps it.
    """
    previous = (state or {}).get("NS") or {}
    if status != "ok":
        if not previous:
            return deepcopy(state) if state else None
        result = {**deepcopy(previous), "outcome": status if status in NINTENDO_OUTCOMES else "unavailable",
                  "attempted_at": checked_at}
    elif entry is not None:
        evidence = valid_switch_evidence({"region": "taiwan", "source_url": nintendo_taiwan.LIST_URL, **entry})
        result = {"outcome": "confirmed" if evidence else "unconfirmed", "checked_at": checked_at,
                  "attempted_at": checked_at, "evidence": evidence, "confirmed_at": checked_at if evidence else None}
    elif previous:
        keep = _final(previous.get("evidence"), previous.get("confirmed_at"))
        result = {"outcome": "unconfirmed", "checked_at": checked_at, "attempted_at": checked_at,
                  "evidence": deepcopy(previous.get("evidence")) if keep else None,
                  "confirmed_at": previous.get("confirmed_at") if keep else None}
    else:
        return deepcopy(state) if state else None
    return {**(deepcopy(state) if state else {}), "igdb_id": game["id"], "name_en": game["name"].strip(), "NS": result}


def summary(states, report, catalog_rows, due_count, checked_at):
    """Counts for the refresh receipt; the per-game state lives in the master."""
    adopted = {"dates": 0, "languages": 0}
    for row in catalog_rows:
        if any(release.get("platform") == "PS5" and release.get("source") == "official_registry"
               for release in row.get("releases", [])):
            adopted["dates"] += 1
        if (row.get("platform_language_support") or {}).get("PS5", {}).get("source") == SOURCE_NAME:
            adopted["languages"] += 1
    outcomes = {}
    for row in (report.get("games") or {}).values():
        outcome = (row.get("taiwan") or {}).get("outcome")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {"schema_version": 1, "provider": "PlayStation Store", "region": "taiwan", "checked_at": checked_at,
            "due_count": due_count, "request_count": report.get("request_count", 0),
            "checked_count": len(report.get("games") or {}), "outcomes": dict(sorted(outcomes.items())),
            "tracked_count": len(states), "adopted_date_count": adopted["dates"],
            "adopted_language_count": adopted["languages"], "games": deepcopy(report.get("games") or {})}


def switch_summary(fetched, game_count, matched_count, catalog_rows, checked_at):
    """The Taiwan list's read for the refresh receipt."""
    entries = fetched.get("entries") or []
    adopted = {"dates": 0, "languages": 0}
    for row in catalog_rows:
        if any(release.get("platform") == "NS" and release.get("source") == "official_registry"
               and release.get("official_source_name") == nintendo_taiwan.SOURCE_NAME for release in row.get("releases", [])):
            adopted["dates"] += 1
        if (row.get("platform_language_support") or {}).get("NS", {}).get("source") == nintendo_taiwan.SOURCE_NAME:
            adopted["languages"] += 1
    return {"schema_version": 1, "provider": "Nintendo Taiwan", "url": nintendo_taiwan.LIST_URL,
            "checked_at": checked_at, "status": fetched.get("status"), "entry_count": len(entries),
            "switch_game_count": game_count, "matched_count": matched_count,
            "adopted_date_count": adopted["dates"], "adopted_language_count": adopted["languages"]}
