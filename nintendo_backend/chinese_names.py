"""Chinese display names with explicit identity and language evidence.

This module never supplies platform, release date, popularity or eligibility data.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from opencc import OpenCC
import requests

from .igdb import CollectionError

_S2T = OpenCC("s2t")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3040-\u30ff]")
_APP_ID = re.compile(r"[1-9][0-9]{0,9}\Z")
_STEAM_URL = re.compile(r"https://store\.steampowered\.com/app/([1-9][0-9]{0,9})(?:/[^/?#]+)?/?(?:[?#].*)?\Z")
NAME_FIELDS = ("display_name", "name_en", "name_zh_tw", "name_source", "name_url", "name_evidence", "steam_appid")
_PROVIDERS = {"official_registry", "igdb_localization", "igdb_alternative_name", "steam_appdetails"}


def _https_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def _chinese(value):
    return (isinstance(value, str) and 0 < len(value.strip()) <= 240 and bool(_HAN.search(value)) and not _KANA.search(value)
            and not any(ord(char) < 32 for char in value) and "<" not in value and ">" not in value)


def validate_registry(document):
    """Reject malformed curated evidence before any catalog files are replaced."""
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int or document["schema_version"] != 1
            or not isinstance(document.get("games"), dict)):
        raise CollectionError("invalid_chinese_name_registry")
    for key, row in document["games"].items():
        if (not isinstance(row, dict) or type(row.get("igdb_id")) is not int or row["igdb_id"] <= 0
                or key != "igdb:" + str(row["igdb_id"])
                or not isinstance(row.get("name_en"), str) or not row["name_en"].strip()
                or not _chinese(row.get("name_zh_tw"))
                or not isinstance(row.get("source"), str) or not row["source"].strip()
                or not _https_url(row.get("url"))):
            raise CollectionError("invalid_chinese_name_registry")
    return document["games"]


def load_registry(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CollectionError("invalid_chinese_name_registry") from None
    return validate_registry(document)


def _name(game, title, source, provider, locale, *, url=None, converted=False, **evidence):
    english = game["name"].strip()
    return {"display_name": title.strip(), "name_en": english, "name_zh_tw": title.strip(),
            "name_source": source, "name_url": url,
            "name_evidence": {"provider": provider, "locale": locale, "converted": converted,
                              "igdb_id": game["id"], "name_en": english, **evidence}}


def _alternative_locale(comment):
    if not isinstance(comment, str):
        return None
    label = comment.lower().replace("_", "-")
    if re.search(r"\b(?:unofficial|fan translation|working title)\b", label):
        return None
    traditional = bool(re.search(r"\bzh-(?:tw|hk|hant)\b|繁體|繁中|traditional chinese|chinese traditional|chinese.*(?:taiwan|hong kong)", label))
    simplified = bool(re.search(r"\bzh-(?:cn|hans)\b|簡體|简体|簡中|简中|simplified chinese|chinese simplified", label))
    if traditional and simplified:
        return None
    if traditional:
        return "zh-Hant"
    if simplified:
        return "zh-Hans"
    if re.search(r"\bchinese\b|中文", label) and not re.search(r"\bjapanese\b|日文|日語|日语", label):
        return "zh"  # Language is known; script is unspecified and is converted conservatively.
    return None


def _cached_name(game, previous, registry):
    if not isinstance(previous, dict):
        return None
    evidence = previous.get("name_evidence")
    if (not isinstance(evidence, dict) or evidence.get("provider") not in _PROVIDERS
            or type(evidence.get("igdb_id")) is not int or evidence["igdb_id"] != game["id"]
            or evidence.get("name_en") != game["name"].strip()
            or previous.get("name_en") != game["name"].strip()
            or not _chinese(previous.get("name_zh_tw"))
            or evidence.get("locale") not in {"zh-Hant", "zh-Hans", "zh"}
            or type(evidence.get("converted")) is not bool
            or evidence["converted"] != (evidence["locale"] != "zh-Hant")
            or not isinstance(previous.get("name_source"), str)):
        return None
    if evidence["provider"] in {"official_registry", "steam_appdetails"} and not _https_url(previous.get("name_url")):
        return None
    if evidence["provider"] == "official_registry":
        official = registry.get("igdb:" + str(game["id"]))
        if (not official or official.get("name_en", "").strip() != game["name"].strip()
                or official.get("name_zh_tw") != previous["name_zh_tw"]
                or official.get("source") != previous["name_source"] or official.get("url") != previous.get("name_url")):
            return None
    if evidence["provider"] == "steam_appdetails":
        appid = previous.get("steam_appid")
        url_match = _STEAM_URL.fullmatch(previous["name_url"])
        if (type(appid) is not int or appid <= 0 or type(evidence.get("steam_appid")) is not int
                or evidence["steam_appid"] != appid or not url_match or int(url_match[1]) != appid):
            return None
        current = _steam_identities(game)
        if current and set(current) != {appid}:
            return None
    result = {key: deepcopy(previous[key]) for key in NAME_FIELDS if key in previous}
    result["display_name"] = previous["name_zh_tw"]
    return result


def names(game, *, registry=None, previous=None):
    english = game.get("name")
    if not isinstance(english, str) or not english.strip():
        raise CollectionError("invalid_game_name")
    if type(game.get("id")) is not int or game["id"] <= 0:
        raise CollectionError("invalid_game_identity")
    english = english.strip()
    official = (registry or {}).get("igdb:" + str(game["id"]))
    if official and official["igdb_id"] == game["id"] and official["name_en"].strip() == english:
        return _name(game, official["name_zh_tw"], official["source"], "official_registry", "zh-Hant", url=official["url"])
    traditional, simplified = [], []
    localizations = game.get("game_localizations") or []
    if not isinstance(localizations, list):
        raise CollectionError("invalid_localization_metadata")
    for row in localizations:
        if not isinstance(row, dict) or not _chinese(row.get("name")) or not isinstance(row.get("region"), dict):
            continue
        locale = str(row["region"].get("identifier") or "").lower().replace("_", "-")
        if locale in {"zh-tw", "zh-hant", "zh-hk"}:
            traditional.append(_name(game, row["name"], "IGDB game_localizations", "igdb_localization", "zh-Hant"))
        elif locale in {"zh-cn", "zh-hans"}:
            simplified.append(_name(game, _S2T.convert(row["name"]), "IGDB game_localizations (simplified converted)", "igdb_localization", "zh-Hans", converted=True))
    alternatives = game.get("alternative_names") or []
    if not isinstance(alternatives, list):
        raise CollectionError("invalid_alternative_name_metadata")
    for row in alternatives:
        if not isinstance(row, dict) or not _chinese(row.get("name")):
            continue
        locale = _alternative_locale(row.get("comment"))
        if not locale:
            continue
        converted = locale != "zh-Hant"
        result = _name(game, _S2T.convert(row["name"]) if converted else row["name"],
                       "IGDB alternative_names" + (" (Chinese converted)" if converted else ""),
                       "igdb_alternative_name", locale, converted=converted, comment=row["comment"])
        (simplified if converted else traditional).append(result)
    if traditional:
        return traditional[0]
    cached = _cached_name(game, previous, registry or {})
    if cached and not cached["name_evidence"]["converted"]:
        return cached
    if cached:
        return cached
    if simplified:
        return simplified[0]
    return {"display_name": english, "name_en": english, "name_zh_tw": None,
            "name_source": "IGDB name", "name_url": None, "name_evidence": None}


def _steam_identities(game):
    ids = {}
    for row in game.get("external_games") or []:
        if not isinstance(row, dict) or not isinstance(row.get("external_game_source"), dict):
            continue
        source = row["external_game_source"].get("name")
        uid = row.get("uid")
        linked_game = row.get("game")
        if isinstance(linked_game, dict):
            linked_game = linked_game.get("id")
        if linked_game is not None and (type(linked_game) is not int or linked_game != game.get("id")):
            continue
        if isinstance(source, str) and source.strip().lower() == "steam" and isinstance(uid, str) and _APP_ID.fullmatch(uid):
            ids[int(uid)] = "IGDB external_games Steam"
    for row in game.get("websites") or []:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            continue
        match = _STEAM_URL.fullmatch(row["url"])
        if match:
            ids.setdefault(int(match[1]), "IGDB Steam product URL")
    return ids


def steam_identity(game):
    """Use an IGDB service reference or exact official product URL; reject ambiguity."""
    ids = _steam_identities(game)
    return next(iter(ids.items())) if len(ids) == 1 else None


def previous_lookup(game, previous):
    state = (previous or {}).get("steam_name_lookup")
    identity = steam_identity(game)
    if (not isinstance(state, dict) or not identity or type(state.get("steam_appid")) is not int
            or state["steam_appid"] != identity[0]
            or state.get("name_en") != game["name"].strip() or not isinstance(state.get("checked_at"), str)):
        return None
    try:
        checked = datetime.fromisoformat(state["checked_at"].replace("Z", "+00:00"))
        if checked.tzinfo is None:
            return None
    except ValueError:
        return None
    return deepcopy(state)


class SteamNameClient:
    """Optional serial enrichment: at most 30 calls/90 seconds, stop on throttling."""
    def __init__(self, *, session=None, max_requests=30, timeout=8, request_interval=0.5,
                 deadline_seconds=90, clock=time.monotonic, sleep=time.sleep):
        if (not 1 <= max_requests <= 30 or not 0 < timeout <= 8
                or request_interval < 0.5 or not 0 < deadline_seconds <= 90):
            raise ValueError("invalid Steam name request bounds")
        self.session = session or requests.Session()
        self.max_requests, self.timeout, self.interval = max_requests, timeout, request_interval
        self.clock, self.sleep = clock, sleep
        self.deadline_seconds, self.deadline, self.next_request = deadline_seconds, None, clock()
        self.request_count, self.failure_count, self.stopped, self.rate_limited = 0, 0, False, False

    def _fetch(self, appid, language):
        if self.deadline is None:
            # The preceding full IGDB collection must not consume this optional stage's budget.
            self.deadline = self.clock() + self.deadline_seconds
            self.next_request = self.clock()
        wait = max(0, self.next_request - self.clock())
        if self.stopped or self.request_count >= self.max_requests or self.clock() + wait >= self.deadline:
            return None
        if wait:
            self.sleep(wait)
        timeout = min(self.timeout, self.deadline - self.clock())
        if timeout <= 0:
            return None
        self.request_count += 1
        try:
            response = self.session.get("https://store.steampowered.com/api/appdetails", timeout=timeout,
                                        allow_redirects=False, params={"appids": appid, "l": language, "cc": "tw", "filters": "basic"})
            if response.status_code != 200:
                self.failure_count += 1
                if response.status_code == 429:
                    self.stopped, self.rate_limited = True, True
                return None
            body = response.json()
        except (requests.RequestException, ValueError):
            self.failure_count += 1
            self.stopped = True
            return None
        finally:
            self.next_request = self.clock() + self.interval
        row = body.get(str(appid)) if isinstance(body, dict) else None
        data = row.get("data") if isinstance(row, dict) and row.get("success") is True else None
        if (not isinstance(data, dict) or type(data.get("steam_appid")) is not int
                or data["steam_appid"] != appid or data.get("type") != "game" or not _chinese(data.get("name"))):
            return None
        return data["name"]

    def lookup(self, game):
        identity = steam_identity(game)
        if not identity:
            return None
        appid, identity_source = identity
        for language, locale, converted in (("tchinese", "zh-Hant", False), ("schinese", "zh-Hans", True)):
            title = self._fetch(appid, language)
            if title:
                translated = _S2T.convert(title)
                converted = converted or translated != title
                locale = "zh-Hans" if converted else "zh-Hant"
                result = _name(game, translated,
                               "Steam appdetails (" + language + ")", "steam_appdetails", locale,
                               url=f"https://store.steampowered.com/app/{appid}/?l={language}", converted=converted,
                               steam_appid=appid, identity_source=identity_source, language=language)
                result["steam_appid"] = appid
                return result
        return None


def enrich_documents(master, catalog, client):
    """Only calendar-qualified rows make optional Steam requests; mutate names only."""
    # Older attempts go first so a request budget never starves newly eligible games.
    pending = sorted(catalog["games"], key=lambda public: (
        (master["games"][public["id"]].get("steam_name_lookup") or {}).get("checked_at", ""), public["id"]))
    for public in pending:
        row = master["games"][public["id"]]
        evidence = row.get("name_evidence")
        if row.get("name_zh_tw") and isinstance(evidence, dict) and evidence.get("converted") is False:
            continue
        before, failures = client.request_count, client.failure_count
        result = client.lookup(row["raw"])
        if client.request_count > before:
            row["steam_name_lookup"] = {"checked_at": master["generated_at"],
                                        "steam_appid": steam_identity(row["raw"])[0], "name_en": row["name_en"],
                                        "status": "matched" if result else "rate_limited" if client.rate_limited else
                                        "failed" if client.failure_count > failures else "unavailable"}
        if not result or (row.get("name_zh_tw") and result["name_evidence"]["converted"]):
            continue
        for key in NAME_FIELDS:
            if key in result:
                row[key], public[key] = deepcopy(result[key]), deepcopy(result[key])
            else:
                row.pop(key, None)
                public.pop(key, None)
    return {"steam_request_count": client.request_count, "steam_failure_count": client.failure_count,
            "steam_rate_limited": client.rate_limited}
