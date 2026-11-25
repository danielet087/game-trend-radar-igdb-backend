"""Taiwan store data, adopted ahead of IGDB and kept current.

PS5: the Taiwan PlayStation Store, checked per game on a cadence. NS and NS2:
Nintendo's Taiwan and Hong Kong listings (nintendo_official), read once a day
for every game; the Taiwan pages give the Taiwan day, the matched eShop title
ID the Hong Kong eShop link and price (nintendo_eshop), and that eShop page,
read on a cadence (languages_due), the product's languages.

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
from . import nintendo_eshop, nintendo_schedule, nintendo_taiwan
from .playstation import (concept_id as sony_concept_id, official_url as playstation_url,
                          product_id as sony_product_id, taiwan_store_urls)

TAIPEI = ZoneInfo("Asia/Taipei")
FIELD = "official_store"
# IGDB's platform day in Taiwan time, after a Taiwan store's own release day.
RELEASE_DATE_POLICY = "taiwan_store_then_igdb_taipei_v1"
SOURCE_NAME = "PlayStation Store 台灣"
MAX_AGE = timedelta(days=14)
FAR_INTERVAL_DAYS, NEAR_WINDOW_DAYS = 3, 14
# A released PS5 game's Taiwan product page is read again every three days for
# this long, for its price; its Taiwan day stays final.
PRICE_DAYS_AFTER_RELEASE = 30
# A price is shown only while its last check is this recent.
PRICE_MAX_AGE = timedelta(days=7)
# A check that answered (the item is confirmed, or the store does not have it)
# versus one that did not (an error, the request budget or a rate limit).
ANSWERS = {"confirmed", "unconfirmed", "no_url"}
OUTCOMES = ANSWERS | {"unavailable", "rate_limited", "budget_exhausted"}
EVIDENCE_TYPES = {"official_product_languages", "official_chinese_unspecified", "official_concept_release_time"}
NINTENDO_PLATFORMS = ("NS", "NS2")
NINTENDO_OUTCOMES = {"confirmed", "unconfirmed", "unavailable", "rate_limited"}
# A matched product's Hong Kong eShop page is read for its languages every 3
# days until the platform's release day, weekly after it; the list is shown
# while its last read is this recent.
LANGUAGE_UPCOMING_DAYS, LANGUAGE_RELEASED_DAYS = 3, 7
LANGUAGE_MAX_AGE = timedelta(days=30)
LANGUAGE_FIELDS = ("nsuid", "checked_at", "supported_languages", "complete", "evidence_type", "official_title")
_CHINESE = {"zh", "zh-Hant", "zh-Hans"}
# The Taiwan pages that give a Switch or Switch 2 game's Taiwan day.
SWITCH_SOURCES = {nintendo_taiwan.LIST_URL: {"on_sale", "upcoming"}, nintendo_schedule.TW_URL: {"schedule"}}


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


def valid_price(value, currency):
    """A stored store price, normalized; None when anything is off.

    Amounts are in cents; only a sale price below the regular one has an end time.
    """
    if not isinstance(value, dict) or value.get("currency") != currency or value.get("status") not in nintendo_eshop.SELLING:
        return None
    regular, final, ends = value.get("regular_cents"), value.get("final_cents"), value.get("discount_ends_at")
    if (type(regular) is not int or type(final) is not int or not 0 <= final <= regular <= 100_000_000
            or (final == regular and ends is not None) or (ends is not None and _stamp(ends) is None)):
        return None
    return {"currency": currency, "status": value["status"], "regular_cents": regular, "final_cents": final,
            "discount_ends_at": ends}


def public_price(price, checked_at, now, source, source_url):
    """The site's price row: only while checked within PRICE_MAX_AGE; a sale past its end shows the regular price."""
    checked, current = _stamp(checked_at), _stamp(now)
    if not price or not checked or not current or not source_url or not timedelta(0) <= current - checked <= PRICE_MAX_AGE:
        return None
    final, ends = price["final_cents"], price["discount_ends_at"]
    if ends is not None and _stamp(ends) <= current:
        final, ends = price["regular_cents"], None
    result = {"currency": price["currency"], "free": final == 0, "final_cents": final, "status": price["status"],
              "source": source, "source_url": source_url, "checked_at": checked_at}
    if final < price["regular_cents"]:
        result.update(original_cents=price["regular_cents"],
                      discount_pct=max(1, min(99, round(100 - final * 100 / price["regular_cents"]))),
                      discount_ends_at=ends)
    return result


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
    # Only a product page carries a price; a concept has none yet.
    price = value.get("price")
    if price is not None and (concept or not (price := valid_price(price, "TWD"))):
        return None
    result["price"] = price
    return result


def validated_state(game, state, checked_at, tracked=("PS5", "NS", "NS2")):
    """The stored check state for this exact game and its tracked platforms, or None.

    PS5 evidence stays bound to the store item IGDB links today: when IGDB no
    longer links it, the evidence is dropped while the check history is kept.
    Nintendo's pages are matched again every day, so their evidence needs no link.
    """
    now = _stamp(checked_at)
    if not now or not _identity(state, game):
        return None
    result = {"igdb_id": game["id"], "name_en": game["name"].strip()}
    if "PS5" in tracked and (entry := _validated_ps5(state.get("PS5"), game, now)):
        result["PS5"] = entry
    for platform in NINTENDO_PLATFORMS:
        if platform in tracked and (entry := _validated_switch(state.get(platform), now, platform)):
            result[platform] = entry
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


def _nsuid(value):
    return isinstance(value, str) and len(value) == 14 and value.isdigit() and value.startswith("7001")


def valid_switch_evidence(value):
    """A Taiwan page entry's Taiwan day (the software list, or the schedule), normalized; None when anything is off."""
    if (not isinstance(value, dict) or value.get("region") != "taiwan"
            or value.get("source_url") not in SWITCH_SOURCES
            or not isinstance(value.get("title"), str) or not 0 < len(value["title"].strip()) <= 240
            or value.get("section") not in SWITCH_SOURCES[value["source_url"]] or type(value.get("chinese")) is not bool
            or (value["chinese"] and value["source_url"] != nintendo_taiwan.LIST_URL)
            or (value.get("nsuid") is not None and not _nsuid(value["nsuid"]))):
        return None
    try:
        if date.fromisoformat(value.get("date")).isoformat() != value["date"]:
            return None
    except (TypeError, ValueError):
        return None
    return {key: value[key] for key in ("region", "source_url", "title", "date", "nsuid", "chinese", "section")}


def valid_product(value):
    """The eShop product matched to a Nintendo platform, normalized; None when anything is off."""
    if not isinstance(value, dict) or not _nsuid(value.get("nsuid")) or _stamp(value.get("matched_at")) is None:
        return None
    titles = value.get("titles")
    if (not isinstance(titles, list) or not 0 < len(titles) <= 10 or len(set(titles)) != len(titles)
            or any(not isinstance(title, str) or not 0 < len(title.strip()) <= 240 for title in titles)):
        return None
    for field in ("tw_title", "hk_title"):
        if value.get(field) is not None and value[field] not in titles:
            return None
    return {"nsuid": value["nsuid"], "titles": list(titles), "tw_title": value.get("tw_title"),
            "hk_title": value.get("hk_title"), "matched_at": value["matched_at"]}


def valid_switch_languages(value):
    """A product page's language read (official_descriptions.parse_languages),
    normalized; None when anything is off. Without an evidence type the page
    was read and gave no usable list."""
    if not isinstance(value, dict) or not _nsuid(value.get("nsuid")) or _stamp(value.get("checked_at")) is None:
        return None
    codes, complete, kind, title = (value.get(key) for key in ("supported_languages", "complete", "evidence_type", "official_title"))
    if (not isinstance(codes, list) or len(codes) > 40 or any(not isinstance(code, str) or code not in LANGUAGE_NAMES for code in codes)
            or len(codes) != len(set(codes)) or type(complete) is not bool):
        return None
    if kind is None:
        valid = not codes and not complete and title is None
    else:
        chinese = set(codes) & _CHINESE
        valid = (isinstance(title, str) and 0 < len(title.strip()) <= 240 and bool(codes)
                 and (kind == "official_chinese_unspecified" and not complete and codes == ["zh"]
                      or kind == "official_product_languages" and "zh" not in codes and (complete or bool(chinese))))
    return {key: deepcopy(value[key]) for key in LANGUAGE_FIELDS} if valid else None


def _validated_switch(entry, now, platform="NS"):
    if not isinstance(entry, dict) or entry.get("outcome") not in NINTENDO_OUTCOMES or not _times(entry, now):
        return None
    result = {"outcome": entry["outcome"], "checked_at": entry.get("checked_at"),
              "attempted_at": entry["attempted_at"], "evidence": None, "confirmed_at": None,
              "product": None, "price": None, "price_checked_at": None, "languages": None}
    evidence, confirmed = valid_switch_evidence(entry.get("evidence")), _stamp(entry.get("confirmed_at"))
    # The Taiwan software list covers only Switch games.
    if (evidence and confirmed and confirmed <= now
            and (platform == "NS" or evidence["source_url"] != nintendo_taiwan.LIST_URL)):
        result.update(evidence=evidence, confirmed_at=entry["confirmed_at"])
    product = valid_product(entry.get("product"))
    if product and _stamp(product["matched_at"]) <= now:
        result["product"] = product
        price, priced = valid_price(entry.get("price"), nintendo_eshop.CURRENCY), _stamp(entry.get("price_checked_at"))
        if price and priced and priced <= now:
            result.update(price=price, price_checked_at=entry["price_checked_at"])
        # A language read belongs to the product it read.
        languages = valid_switch_languages(entry.get("languages"))
        if languages and languages["nsuid"] == product["nsuid"] and _stamp(languages["checked_at"]) <= now:
            result["languages"] = languages
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


def current_languages(state, platform, checked_at):
    """A platform's product page language list while its read is at most
    LANGUAGE_MAX_AGE old; None otherwise (also for a read without a list)."""
    entry = (state or {}).get(platform) or {}
    read, product, now = entry.get("languages"), entry.get("product"), _stamp(checked_at)
    if not read or not read.get("evidence_type") or not product or read.get("nsuid") != product["nsuid"] or not now:
        return None
    checked = _stamp(read["checked_at"])
    return read if checked and timedelta(0) <= now - checked <= LANGUAGE_MAX_AGE else None


def _page_entry(read):
    return support_entry({"supported_languages": read["supported_languages"], "complete": read["complete"],
        "region": "hong_kong", "source": nintendo_eshop.SOURCE_NAME, "source_url": nintendo_eshop.title_url(read["nsuid"]),
        "checked_at": read["checked_at"], "evidence_type": read["evidence_type"]}, product=False)


def apply_switch_languages(languages, state, checked_at):
    """NS/NS2 languages from Nintendo's own pages, after the reviewed lists.

    The matched product's Hong Kong eShop page lists its languages (current_languages):
    complete, with each Chinese script, from its 「對應語言」 row; only "Chinese"
    from its JSON-LD alone. Nintendo Taiwan's 對應中文 mark proves Chinese for a
    Switch game, not which script. A reviewed complete list stays; a reviewed
    partial one gives way only to a complete page list. Where the mark says
    Chinese and the page lists none, only the mark's Chinese stands; where both
    prove only Chinese, Taiwan's comes first. Returns the platforms changed.
    """
    taiwan, confirmed_at = current_evidence(state, checked_at, "NS")
    changed = []
    for code in NINTENDO_PLATFORMS:
        existing = languages.get(code)
        if existing is None or existing.get("complete"):
            continue
        page = current_languages(state, code, checked_at)
        codes = set(page["supported_languages"]) if page else set()
        marked = code == "NS" and bool(taiwan) and taiwan["chinese"]
        if page and page["complete"] and (codes & _CHINESE or not marked):
            entry = _page_entry(page)
        elif existing.get("status") != "unknown":
            continue
        elif page and (codes & {"zh-Hant", "zh-Hans"} or not marked):
            entry = _page_entry(page)
        elif marked:
            entry = support_entry({"supported_languages": ["zh"], "complete": False, "region": "taiwan",
                "source": nintendo_taiwan.SOURCE_NAME, "source_url": nintendo_taiwan.LIST_URL, "checked_at": confirmed_at,
                "evidence_type": "official_chinese_unspecified"}, product=False)
        else:
            continue
        languages[code] = entry
        changed.append(code)
    return changed


def languages_due(entry, release_day, today):
    """Whether a platform's matched product page is due for its languages:
    at once for a product not read yet, every LANGUAGE_UPCOMING_DAYS until the
    platform's release day (or while it has none), every LANGUAGE_RELEASED_DAYS
    from it. entry: the platform's state; release_day: the day the calendar shows."""
    product = (entry or {}).get("product")
    if not product:
        return False
    read = entry.get("languages") or {}
    last = _day(read.get("checked_at"))
    if read.get("nsuid") != product["nsuid"] or last is None or last > today:
        return True
    released = release_day is not None and release_day <= today
    return (today - last).days >= (LANGUAGE_RELEASED_DAYS if released else LANGUAGE_UPCOMING_DAYS)


def merge_languages(state, platform, page, checked_at):
    """A platform's languages from today's read of its product page
    (OfficialDescriptionClient.read_languages): replaced when the page answered,
    also by no list; kept when it did not."""
    entry = (state or {}).get(platform)
    product = (entry or {}).get("product")
    if not product or not isinstance(page, dict) or page.get("status") != "ok":
        return state
    read = {"nsuid": product["nsuid"], "checked_at": checked_at, "supported_languages": [], "complete": False,
            "evidence_type": None, "official_title": None}
    found = page.get("languages")
    if isinstance(found, dict):
        read = valid_switch_languages({**read, **{key: found.get(key) for key in LANGUAGE_FIELDS[2:]}}) or read
    return {**state, platform: {**entry, "languages": read}}


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
    After release, a confirmed Taiwan product is read every 3 days for
    PRICE_DAYS_AFTER_RELEASE more days, for its price.
    """
    entry = (state or {}).get("PS5") or {}
    last = _day(entry.get("checked_at"))
    if last is None or entry.get("urls") != taiwan_store_urls(game) or last > today:
        return True
    if release_day is None:
        return (today - last).days >= FAR_INTERVAL_DAYS
    if last >= release_day:
        product = isinstance(entry.get("evidence"), dict) and entry["evidence"].get("product_id")
        return (bool(product) and (today - release_day).days <= PRICE_DAYS_AFTER_RELEASE
                and (today - last).days >= FAR_INTERVAL_DAYS)
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
        if _final(previous.get("evidence"), previous.get("confirmed_at")):
            # A day confirmed on or after release stays; only its price goes.
            entry["evidence"] = {**deepcopy(previous["evidence"]), "price": None}
        else:
            # The store answered without this item: its earlier data no longer stands.
            entry.update(request_url=None, evidence=None, confirmed_at=None)
    return {**base, "igdb_id": game["id"], "name_en": game["name"].strip(), "PS5": entry}


def _final(evidence, confirmed_at):
    stamp = _stamp(confirmed_at)
    return bool(evidence and evidence.get("date") and stamp
                and stamp.astimezone(TAIPEI).date() >= date.fromisoformat(evidence["date"]))


def merge_nintendo(state, game, platform, read, product, checked_at):
    """The game's next state for one Nintendo platform from today's pages (or None: nothing to keep).

    read: {"taiwan": the Taiwan pages read today, "status": "ok" when any page
    was read, else why not}. product: today's matched product
    (nintendo_official.products row) or None.

    A Taiwan day on a matched product confirms it. A Taiwan page that was read
    and no longer lists the game drops the earlier day, unless that was
    confirmed on or after the release day (the pages later leave games out); a
    page that could not be read keeps it. A matched eShop product replaces the
    stored one; without a match the stored product stays, as the schedules
    drop past months.
    """
    previous = (state or {}).get(platform) or {}
    if read.get("status") != "ok":
        if not previous:
            return deepcopy(state) if state else None
        status = read.get("status")
        result = {**deepcopy(previous), "outcome": status if status in NINTENDO_OUTCOMES else "unavailable",
                  "attempted_at": checked_at}
    else:
        taiwan = (product or {}).get("taiwan")
        evidence = valid_switch_evidence({"region": "taiwan", **taiwan, "nsuid": product.get("nsuid")}) if taiwan else None
        adopted = bool(evidence) and (platform == "NS" or evidence["source_url"] != nintendo_taiwan.LIST_URL)
        if adopted:
            kept, confirmed = evidence, checked_at
        else:
            old = previous.get("evidence")
            keep = bool(old) and (_final(old, previous.get("confirmed_at")) or old.get("source_url") not in read["taiwan"])
            kept, confirmed = (deepcopy(old), previous.get("confirmed_at")) if keep else (None, None)
        bound = deepcopy(previous.get("product"))
        if product and product.get("nsuid"):
            same_product = bool(bound) and bound.get("nsuid") == product["nsuid"]
            # Titles from a page not read today stay with the same product.
            titles = list(dict.fromkeys(product["titles"] + (bound["titles"] if same_product else [])))[:10]
            tw_title = product.get("tw_title") or (bound.get("tw_title") if same_product else None)
            hk_title = product.get("hk_title") or (bound.get("hk_title") if same_product else None)
            bound = {"nsuid": product["nsuid"], "titles": titles,
                     "tw_title": tw_title if tw_title in titles else None,
                     "hk_title": hk_title if hk_title in titles else None,
                     "matched_at": bound["matched_at"] if same_product else checked_at}
        if not previous and not kept and not bound:
            return deepcopy(state) if state else None
        same = bound and previous.get("product") and previous["product"].get("nsuid") == bound["nsuid"]
        result = {"outcome": "confirmed" if adopted else "unconfirmed",
                  "checked_at": checked_at if read["taiwan"] else previous.get("checked_at"),
                  "attempted_at": checked_at, "evidence": kept, "confirmed_at": confirmed, "product": bound,
                  "price": deepcopy(previous.get("price")) if same else None,
                  "price_checked_at": previous.get("price_checked_at") if same else None,
                  "languages": deepcopy(previous.get("languages")) if same else None}
    return {**(deepcopy(state) if state else {}), "igdb_id": game["id"], "name_en": game["name"].strip(), platform: result}


def merge_price(state, platform, fetched, checked_at):
    """A platform's Hong Kong eShop price from today's price read: replaced when
    the reply covers the product (None: not for sale), kept when it did not."""
    entry = (state or {}).get(platform)
    product = (entry or {}).get("product")
    prices = fetched.get("prices") if isinstance(fetched, dict) else None
    if not product or not isinstance(prices, dict) or product["nsuid"] not in prices:
        return state
    found = prices[product["nsuid"]]
    price = valid_price({"currency": nintendo_eshop.CURRENCY, **found}, nintendo_eshop.CURRENCY) if found else None
    return {**state, platform: {**entry, "price": price, "price_checked_at": checked_at}}


def nintendo_products(state):
    """[(platform, product)] for the eShop products matched to a game's Nintendo platforms."""
    return [(platform, state[platform]["product"]) for platform in NINTENDO_PLATFORMS
            if isinstance((state or {}).get(platform), dict) and state[platform].get("product")]


def platform_prices(state, checked_at):
    """The site's current prices by platform: PS5 from its Taiwan product page, NS/NS2 from the Hong Kong eShop."""
    result = {}
    ps5 = (state or {}).get("PS5") or {}
    evidence = ps5.get("evidence")
    if evidence and evidence.get("price") and evidence.get("product_id"):
        row = public_price(evidence["price"], ps5.get("confirmed_at"), checked_at, SOURCE_NAME, evidence["source_url"])
        if row:
            result["PS5"] = row
    for platform, product in nintendo_products(state):
        entry = state[platform]
        row = public_price(entry.get("price"), entry.get("price_checked_at"), checked_at, nintendo_eshop.SOURCE_NAME,
                           nintendo_eshop.title_url(product["nsuid"]))
        if row:
            result[platform] = row
    return result


def summary(states, report, catalog_rows, due_count, checked_at):
    """Counts for the refresh receipt; the per-game state lives in the master."""
    adopted = {"dates": 0, "languages": 0, "prices": 0}
    for row in catalog_rows:
        if any(release.get("platform") == "PS5" and release.get("source") == "official_registry"
               for release in row.get("releases", [])):
            adopted["dates"] += 1
        if (row.get("platform_language_support") or {}).get("PS5", {}).get("source") == SOURCE_NAME:
            adopted["languages"] += 1
        if (row.get("platform_prices") or {}).get("PS5"):
            adopted["prices"] += 1
    outcomes = {}
    for row in (report.get("games") or {}).values():
        outcome = (row.get("taiwan") or {}).get("outcome")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {"schema_version": 1, "provider": "PlayStation Store", "region": "taiwan", "checked_at": checked_at,
            "due_count": due_count, "request_count": report.get("request_count", 0),
            "checked_count": len(report.get("games") or {}), "outcomes": dict(sorted(outcomes.items())),
            "tracked_count": len(states), "adopted_date_count": adopted["dates"],
            "adopted_language_count": adopted["languages"], "adopted_price_count": adopted["prices"],
            "games": deepcopy(report.get("games") or {})}


def nintendo_summary(reads, games, matched, price_read, catalog_rows, checked_at, language_read=None):
    """Nintendo's pages, the Hong Kong eShop prices and product pages, for the refresh receipt.

    reads: {page URL: read}; games: {key: {platform: IGDB day}} of the
    published NS/NS2 games; matched: {key: {platform: product}};
    language_read: this run's product page reads for languages.
    """
    platforms = {code: {"game_count": 0, "matched_count": 0, "taiwan_date_count": 0, "eshop_link_count": 0,
                        "price_count": 0, "eshop_language_count": 0} for code in NINTENDO_PLATFORMS}
    for days in games.values():
        for code in days:
            platforms[code]["game_count"] += 1
    for found in matched.values():
        for code in found:
            platforms[code]["matched_count"] += 1
    names = 0
    for row in catalog_rows:
        names += (row.get("name_evidence") or {}).get("provider") == "nintendo_official"
        for code in NINTENDO_PLATFORMS:
            if not any(platform.get("code") == code for platform in row.get("platforms", [])):
                continue
            if any(release.get("platform") == code and release.get("official_source_name") == nintendo_taiwan.SOURCE_NAME
                   for release in row.get("releases", [])):
                platforms[code]["taiwan_date_count"] += 1
            if str((row.get("platform_urls") or {}).get(code) or "").startswith("https://ec.nintendo.com/HK/"):
                platforms[code]["eshop_link_count"] += 1
            if (row.get("platform_prices") or {}).get(code):
                platforms[code]["price_count"] += 1
            if ((row.get("platform_language_support") or {}).get(code) or {}).get("source") == nintendo_eshop.SOURCE_NAME:
                platforms[code]["eshop_language_count"] += 1
    return {"schema_version": 1, "provider": "Nintendo", "checked_at": checked_at,
            "pages": {url: {"status": read.get("status"), "entry_count": len(read.get("entries") or [])}
                      for url, read in reads.items()},
            "price": {"url": nintendo_eshop.PRICE_URL, "status": (price_read or {}).get("status"),
                      "request_count": (price_read or {}).get("request_count", 0),
                      "product_count": len((price_read or {}).get("prices") or {})},
            "languages": {"due_count": (language_read or {}).get("due_count", 0),
                          "request_count": (language_read or {}).get("request_count", 0),
                          "outcomes": dict(sorted(((language_read or {}).get("outcomes") or {}).items()))},
            "platforms": platforms, "official_name_count": names}


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
