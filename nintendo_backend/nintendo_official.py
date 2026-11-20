"""Nintendo's own Taiwan and Hong Kong listings, matched to NS and NS2 games.

Three pages are read once a day: Nintendo Taiwan's Switch software list
(nintendo_taiwan), and the Taiwan and Hong Kong release schedules
(nintendo_schedule), which also list Switch 2 games. Entries for one eShop
title ID are one product. A product belongs to a game's platform only by an
exact title (the game's IGDB name or an alias, or its Chinese name; "中文
(English)" and "中文／English" match either part), on that hardware, claimed by
no other game and, when both have a day, within 60 days of IGDB's.

Only the Taiwan pages give Taiwan days. The product's title ID then names its
Hong Kong eShop page (the shop link, its Chinese description) and its price
(nintendo_eshop): Taiwan has no eShop of its own, and Nintendo Taiwan lists its
prices in Hong Kong dollars too.
"""
from __future__ import annotations

from datetime import date
import re

from .nintendo_eshop import title_url
from .nintendo_taiwan import LIST_URL as TW_LIST_URL, game_keys, normalize, title_keys as _pair_keys
from .nintendo_schedule import HK_URL, TW_URL, SOURCE_NAMES

MAX_DAYS_FROM_IGDB = 60
TAIWAN_SOURCES = (TW_LIST_URL, TW_URL)
_SLASH_PAIR = re.compile(r"(.+?)\s*[／/]\s*(.+)")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")
_PAIR = re.compile(r"(.+?)\s*[（(]([^()（）]+)[)）]")


def title_keys(title):
    """A title's keys: itself and both parts of "中文 (English)" or "中文／English"."""
    keys = set(_pair_keys(title))
    slash = _SLASH_PAIR.fullmatch(title.strip()) if isinstance(title, str) else None
    if slash and (_HAN.search(slash[1]) or _HAN.search(slash[2])):
        keys |= {normalize(slash[1]), normalize(slash[2])}
    return keys - {""}


def chinese_title(title):
    """The Chinese part of an official title, or None (English, Japanese or unclear)."""
    if not isinstance(title, str):
        return None
    value = " ".join(title.replace("™", "").replace("®", "").replace("©", "").split())
    for pattern in (_PAIR, _SLASH_PAIR):
        pair = pattern.fullmatch(value)
        if pair:
            parts = [part.strip() for part in (pair[1], pair[2])]
            chinese = [part for part in parts if _HAN.search(part)]
            if len(chinese) == 1:
                value = chinese[0]
            break
    if (not value or len(value) > 120 or not _HAN.search(value) or _KANA.search(value)
            or "<" in value or ">" in value or any(ord(char) < 32 for char in value)):
        return None
    return value


def official_name(game, products, naming):
    """A Chinese name from the game's matched eShop product (Switch 2 first,
    the Taiwan pages' title before Hong Kong's), when the game has no
    Traditional Chinese name of its own; a converted Simplified one is replaced.

    products: [(platform, product)] from official_store.nintendo_products.
    """
    from .descriptions import simplified  # descriptions reads this module's description candidates.
    evidence = naming.get("name_evidence")
    if naming.get("name_zh_tw") and isinstance(evidence, dict) and evidence.get("converted") is False:
        return None
    ordered = sorted(products, key=lambda item: item[0] != "NS2")
    for field, source in (("tw_title", SOURCE_NAMES[TW_URL]), ("hk_title", SOURCE_NAMES[HK_URL])):
        for platform, product in ordered:
            title = chinese_title(product.get(field))
            if title and not simplified(title):
                english = game["name"].strip()
                return {"display_name": title, "name_en": english, "name_zh_tw": title, "name_source": source,
                        "name_url": title_url(product["nsuid"]),
                        "name_evidence": {"provider": "nintendo_official", "locale": "zh-Hant", "converted": False,
                                          "igdb_id": game["id"], "name_en": english, "platform": platform,
                                          "nsuid": product["nsuid"], "official_title": product[field]}}
    return None


def description_candidates(game, products):
    """The matched products' Hong Kong eShop pages, for their Chinese descriptions."""
    rows = []
    for platform, product in products:
        url = title_url(product["nsuid"])
        if url and not any(row["url"] == url for row in rows):
            titles = [game["name"].strip(), *product["titles"]]
            titles += [title for title in map(chinese_title, product["titles"]) if title]
            rows.append({"url": url, "platform": platform, "titles": list(dict.fromkeys(titles))})
    return rows


def list_entries(entries):
    """Nintendo Taiwan's software list in the schedules' shape (it lists only Switch games)."""
    return [{**entry, "platform": "NS"} for entry in entries or []
            if isinstance(entry, dict) and entry.get("hardware") == "Nintendo Switch"]


def products(sources):
    """One row per (platform, eShop title ID) from the pages read today.

    sources: {page URL: entries}, where entries have platform (NS/NS2), title,
    date (or None) and nsuid (or None). A title-only entry joins the one product
    of that platform with the same title, if there is exactly one.
    """
    rows, loose = {}, []
    for url, entries in sources.items():
        for entry in entries or []:
            if entry.get("platform") not in {"NS", "NS2"} or not isinstance(entry.get("title"), str):
                continue
            if entry.get("nsuid"):
                key = (entry["platform"], entry["nsuid"])
                row = rows.setdefault(key, {"platform": entry["platform"], "nsuid": entry["nsuid"], "entries": []})
                row["entries"].append((url, entry))
            else:
                loose.append((url, entry))
    for url, entry in loose:
        keys = title_keys(entry["title"])
        same = [row for row in rows.values() if row["platform"] == entry["platform"]
                and any(title_keys(other["title"]) == keys for _, other in row["entries"])]
        if len(same) == 1:
            same[0]["entries"].append((url, entry))
        else:
            key = (entry["platform"], "title:" + "|".join(sorted(keys)))
            row = rows.setdefault(key, {"platform": entry["platform"], "nsuid": None, "entries": []})
            row["entries"].append((url, entry))
    result = []
    for row in rows.values():
        taiwan = [(url, entry) for url, entry in row["entries"] if url in TAIWAN_SOURCES and entry.get("date")]
        first = min(taiwan, key=lambda item: (item[1]["date"], TAIWAN_SOURCES.index(item[0]))) if taiwan else None
        days = sorted({entry["date"] for _, entry in row["entries"] if entry.get("date")})
        titles = list(dict.fromkeys(entry["title"] for _, entry in row["entries"]))
        tw_title = next((entry["title"] for url, entry in row["entries"] if url in TAIWAN_SOURCES), None)
        hk_title = next((entry["title"] for url, entry in row["entries"] if url == HK_URL), None)
        result.append({
            "platform": row["platform"], "nsuid": row["nsuid"], "titles": titles,
            "keys": set().union(*(title_keys(title) for title in titles)),
            "taiwan": {"source_url": first[0], "title": first[1]["title"], "date": first[1]["date"],
                       "chinese": any(entry.get("chinese") is True for url, entry in row["entries"] if url == TW_LIST_URL),
                       "section": first[1].get("section", "schedule")} if first else None,
            "days": days, "tw_title": tw_title, "hk_title": hk_title,
            "sources": sorted({url for url, _ in row["entries"]})})
    return result


def match(rows, games):
    """Each game's single product per platform: {key: {platform: product}}.

    games: {key: (raw, row, {platform: IGDB day or None})}. Ambiguity on either
    side matches nothing.
    """
    claims = {}
    for key, (raw, row, days) in games.items():
        names = game_keys(raw, row)
        if not names:
            continue
        for platform, igdb_day in days.items():
            found = [product for product in rows if product["platform"] == platform and product["keys"] & names]
            if len(found) != 1:
                continue
            product = found[0]
            if igdb_day is not None and product["days"] and min(
                    abs((date.fromisoformat(day) - igdb_day).days) for day in product["days"]) > MAX_DAYS_FROM_IGDB:
                continue
            claims.setdefault(id(product), []).append((key, platform, product))
    result = {}
    for claimed in claims.values():
        if len(claimed) == 1:
            key, platform, product = claimed[0]
            result.setdefault(key, {})[platform] = product
    return result
