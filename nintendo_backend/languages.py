"""Reviewed language evidence for a precise Nintendo game and native version.

The Steam version and a game's general IGDB metadata cannot establish which
languages the NS or NS2 product supports. Missing evidence stays unknown.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlparse

from .igdb import CollectionError

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "nintendo_languages.json"
OFFICIAL_DOMAINS = {"www.nintendo.com", "www.nintendo.co.jp", "www.nintendo.com.hk",
                    "ec.nintendo.com", "asia.sega.com", "www.konami.com",
                    "www.playtombraider.com", "www.layton.jp"}
REGIONS = {"taiwan", "north_america", "japan", "hong_kong", "asia", "worldwide",
           "united_kingdom", "europe"}
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


def _source_url(value, region):
    if not isinstance(value, str) or len(value) > 2000:
        return False
    try:
        url = urlparse(value)
        if (url.scheme != "https" or url.hostname not in OFFICIAL_DOMAINS
                or url.username or url.password or url.port is not None or url.fragment):
            return False
        if url.hostname == "www.nintendo.com":
            return url.path.startswith({"taiwan": "/tw/", "north_america": "/us/",
                                        "united_kingdom": "/en-gb/", "europe": "/en-gb/"}.get(region, "\0"))
        if url.hostname == "www.nintendo.co.jp":
            return region == "japan"
        if url.hostname == "www.nintendo.com.hk":
            return region == "hong_kong"
        if url.hostname == "ec.nintendo.com":
            prefix = {"taiwan": "/TW/", "hong_kong": "/HK/", "japan": "/JP/"}.get(region, "\0")
            return url.path.startswith(prefix)
        return True
    except ValueError:
        return False


def validate_registry(document):
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or not isinstance(document.get("games"), dict)):
        raise CollectionError("invalid_nintendo_language_registry")
    for key, game in document["games"].items():
        if (not isinstance(game, dict) or type(game.get("igdb_id")) is not int or game["igdb_id"] <= 0
                or key != "igdb:" + str(game["igdb_id"])
                or not isinstance(game.get("name_en"), str) or not 0 < len(game["name_en"].strip()) <= 240
                or not isinstance(game.get("platforms"), dict) or not game["platforms"]
                or set(game["platforms"]) - {"NS", "NS2"}):
            raise CollectionError("invalid_nintendo_language_registry")
        for row in game["platforms"].values():
            try:
                if not isinstance(row, dict):
                    raise ValueError
                checked = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
                codes = row["supported_languages"]
                if (row.get("region") not in REGIONS or type(row.get("complete")) is not bool
                        or not isinstance(codes, list) or not codes or len(codes) != len(set(codes))
                        or any(code not in LANGUAGE_NAMES for code in codes)
                        or not isinstance(row.get("source"), str) or not 0 < len(row["source"].strip()) <= 240
                        or checked.utcoffset() is None or not _source_url(row.get("source_url"), row["region"])
                        or row.get("evidence_type") not in {"official_product_languages", "official_chinese_unspecified"}
                        or (row["evidence_type"] == "official_chinese_unspecified"
                            and (row["complete"] or codes != ["zh"]))):
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
    return result
