"""Reviewed language evidence for a precise game and native platform version.

The Steam version and a game's general IGDB metadata cannot establish which
languages the NS or NS2 product supports. Missing evidence stays unknown.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from .igdb import CollectionError
from .playstation import official_url as playstation_url, product_id as playstation_product_id, PRODUCT_ID

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "nintendo_languages.json"
PLAYSTATION_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "playstation_languages.json"
OFFICIAL_DOMAINS = {"www.nintendo.com", "www.nintendo.co.jp", "www.nintendo.com.hk",
                    "ec.nintendo.com", "asia.sega.com", "www.konami.com",
                    "www.playtombraider.com", "www.layton.jp"}
REGIONS = {"taiwan", "north_america", "japan", "hong_kong", "asia", "worldwide",
           "united_kingdom", "europe", "australia"}
EDITION_TYPES = {"base_plus_expansion", "deluxe", "base_plus_dlc"}
LANGUAGE_NAMES = {
    "zh-Hant": "繁體中文", "zh-Hans": "簡體中文", "zh": "中文",
    "en": "英文", "ja": "日文", "ko": "韓文", "fr": "法文", "de": "德文",
    "it": "義大利文", "es": "西班牙文", "es-419": "拉丁美洲西班牙文",
    "pt": "葡萄牙文", "pt-BR": "巴西葡萄牙文", "ru": "俄文", "nl": "荷蘭文",
    "pl": "波蘭文", "cs": "捷克文", "uk": "烏克蘭文", "ar": "阿拉伯文",
    "th": "泰文", "tr": "土耳其文", "hu": "匈牙利文", "sv": "瑞典文",
    "da": "丹麥文", "fi": "芬蘭文", "no": "挪威文", "ro": "羅馬尼亞文",
    "bg": "保加利亞文", "el": "希臘文", "vi": "越南文", "id": "印尼文",
}


def _source_url(value, region, platform=None):
    if platform == "PS5":
        url = playstation_url(value, region=region)
        return bool(url and urlparse(url).hostname == "store.playstation.com")
    if not isinstance(value, str) or len(value) > 2000:
        return False
    try:
        url = urlparse(value)
        if (url.scheme != "https" or url.hostname not in OFFICIAL_DOMAINS
                or url.username or url.password or url.port is not None or url.fragment):
            return False
        if url.hostname == "www.nintendo.com":
            return url.path.startswith({"taiwan": "/tw/", "north_america": "/us/",
                                        "united_kingdom": "/en-gb/", "europe": "/en-gb/",
                                        "australia": "/au/"}.get(region, "\0"))
        if url.hostname == "www.nintendo.co.jp":
            return region == "japan"
        if url.hostname == "www.nintendo.com.hk":
            return region == "hong_kong"
        if url.hostname == "ec.nintendo.com":
            prefix = {"taiwan": "/TW/", "hong_kong": "/HK/", "japan": "/JP/",
                      "australia": "/AU/"}.get(region, "\0")
            return url.path.startswith(prefix)
        return region != "australia"
    except ValueError:
        return False


def _edition_identity_url(value, region, platform=None):
    """Publisher base-inclusion proof may differ from the regional product URL."""
    if _source_url(value, region, platform):
        return True
    if not isinstance(value, str) or len(value) > 2000:
        return False
    try:
        url = urlparse(value)
        return (url.scheme == "https" and url.hostname == "captown.capcom.com"
                and url.path.startswith("/en/theaters/") and not url.username and not url.password
                and url.port is None and not url.fragment)
    except ValueError:
        return False


def _edition_valid(row, platform=None):
    if "edition_type" not in row and "edition_label" not in row:
        return True
    return (row.get("edition_type") in EDITION_TYPES
            and isinstance(row.get("edition_label"), str) and 0 < len(row["edition_label"].strip()) <= 120
            and isinstance(row.get("official_title"), str) and 0 < len(row["official_title"].strip()) <= 240
            and isinstance(row.get("product_id"), str)
            and re.fullmatch(PRODUCT_ID if platform == "PS5" else r"[0-9]{14}", row["product_id"]) is not None
            and row.get("identity_relation") == "base_game_included"
            and isinstance(row.get("identity_evidence"), str) and 0 < len(row["identity_evidence"].strip()) <= 1000
            and _edition_identity_url(row.get("identity_source_url"), row.get("region"), platform))


def validate_registry(document):
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or not isinstance(document.get("games"), dict)):
        raise CollectionError("invalid_nintendo_language_registry")
    for key, game in document["games"].items():
        if (not isinstance(game, dict) or type(game.get("igdb_id")) is not int or game["igdb_id"] <= 0
                or key != "igdb:" + str(game["igdb_id"])
                or not isinstance(game.get("name_en"), str) or not 0 < len(game["name_en"].strip()) <= 240
                or not isinstance(game.get("platforms"), dict) or not game["platforms"]
                or set(game["platforms"]) - {"NS", "NS2", "PS5"}):
            raise CollectionError("invalid_nintendo_language_registry")
        for platform, row in game["platforms"].items():
            try:
                if not isinstance(row, dict):
                    raise ValueError
                checked = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
                codes = row["supported_languages"]
                if (row.get("region") not in REGIONS or type(row.get("complete")) is not bool
                        or not isinstance(codes, list) or not codes or len(codes) != len(set(codes))
                        or any(code not in LANGUAGE_NAMES for code in codes)
                        or not isinstance(row.get("source"), str) or not 0 < len(row["source"].strip()) <= 240
                        or checked.utcoffset() is None or not _source_url(row.get("source_url"), row["region"], platform)
                        or (platform == "PS5" and playstation_product_id(row.get("source_url"))
                            and row.get("product_id") != playstation_product_id(row["source_url"]))
                        or row.get("evidence_type") not in {"official_product_languages", "official_chinese_unspecified"}
                        or (row["evidence_type"] == "official_chinese_unspecified"
                            and (row["complete"] or codes != ["zh"]))
                        or not _edition_valid(row, platform)):
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError):
                raise CollectionError("invalid_nintendo_language_registry") from None
    return document["games"]


def load_registry(path=REGISTRY_PATH):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise CollectionError("invalid_nintendo_language_registry") from None
    return validate_registry(document)


def load_combined_registry(nintendo_path=REGISTRY_PATH, playstation_path=PLAYSTATION_REGISTRY_PATH):
    """Keep evidence for Nintendo and PlayStation versions independently bound."""
    result = deepcopy(load_registry(nintendo_path))
    for key, game in load_registry(playstation_path).items():
        if key not in result:
            result[key] = deepcopy(game)
            continue
        existing = result[key]
        if existing["name_en"] != game["name_en"] or existing["igdb_id"] != game["igdb_id"]:
            raise CollectionError("conflicting_native_language_registry_identity")
        if set(existing["platforms"]) & set(game["platforms"]):
            raise CollectionError("conflicting_native_language_registry_platform")
        existing["platforms"].update(deepcopy(game["platforms"]))
    return result


def unknown_support():
    return {"status": "unknown", "region": None,
            "languages": {key: None for key in ("tchinese", "schinese", "english", "chinese")},
            "supported_languages": [], "complete": False, "source": None, "source_url": None,
            "checked_at": None, "evidence_type": None}


def platform_language_support(game, tracked_platforms, registry=None):
    result = {row["code"]: unknown_support() for row in tracked_platforms}
    if type(game.get("id")) is not int or not isinstance(game.get("name"), str):
        return result
    official = (registry or {}).get("igdb:" + str(game["id"]))
    if (not official or official.get("igdb_id") != game["id"]
            or official.get("name_en") != game["name"].strip()):
        return result
    for platform, row in official["platforms"].items():
        if platform not in result:
            continue  # Evidence for another native version never carries over.
        codes = set(row["supported_languages"])
        complete = row["complete"]
        # A generic Chinese label proves Chinese, but not either written variant.
        def supported(code):
            return True if code in codes else None if not complete or (code.startswith("zh-") and "zh" in codes) else False
        result[platform] = {"status": "confirmed" if complete else "partial", "region": row["region"],
                            "languages": {"tchinese": supported("zh-Hant"), "schinese": supported("zh-Hans"),
                                          "english": supported("en"),
                                          "chinese": True if codes & {"zh", "zh-Hant", "zh-Hans"} else False if complete else None},
                            "supported_languages": [{"code": code, "name": LANGUAGE_NAMES[code]} for code in row["supported_languages"]],
                            "complete": complete, **{key: deepcopy(row[key]) for key in
                                                      ("source", "source_url", "checked_at", "evidence_type")}}
        if platform == "PS5" and row.get("product_id"):
            result[platform]["product_id"] = row["product_id"]
    return result


def platform_editions(game, tracked_platforms, registry=None):
    """Expose only reviewed bundled/deluxe versions; absence makes no base claim."""
    if type(game.get("id")) is not int or not isinstance(game.get("name"), str):
        return {}
    official = (registry or {}).get("igdb:" + str(game["id"]))
    if (not official or official.get("igdb_id") != game["id"]
            or official.get("name_en") != game["name"].strip()):
        return {}
    tracked = {row["code"] for row in tracked_platforms}
    return {platform: {"type": row["edition_type"], "label": row["edition_label"],
                       "title": row["official_title"], "product_id": row["product_id"],
                       "region": row["region"], "source_url": row["source_url"], "checked_at": row["checked_at"]}
            for platform, row in official["platforms"].items()
            if platform in tracked and "edition_type" in row}
