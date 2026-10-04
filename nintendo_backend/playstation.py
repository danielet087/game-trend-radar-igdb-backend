"""Bounded investigations of exact PlayStation Store products.

Only official Taiwan/Hong Kong metadata for the same English game identity and
native PS5 version is usable. A store listing does not establish exclusivity.
The client returns evidence for review; it never writes trusted registries.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests

PRODUCT_ID = r"[A-Z]{2}[0-9]{4}-[A-Z0-9]{9}_[A-Z0-9]{2}-[A-Z0-9]{16}"
_STORE_PATH = re.compile(r"/([a-z-]+)/(?:(product)/(" + PRODUCT_ID + r")|(concept)/([0-9]{1,12}))/?")
_LOCALES = {"taiwan": {"zh-hant-tw", "en-tw"}, "hong_kong": {"zh-hant-hk", "en-hk"}}
MAX_PAGES = 30
MAX_HTML_BYTES = 2_000_000
TIMEOUT_SECONDS = 5
DEADLINE_SECONDS = 90


def official_url(value, *, region=None):
    """Allow regional product/game URLs, with no credentials or query payload."""
    if not isinstance(value, str) or not 0 < len(value) <= 2000:
        return None
    if region is not None and region not in _LOCALES:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port is not None or parsed.query or parsed.fragment):
            return None
        if parsed.hostname == "store.playstation.com":
            match = _STORE_PATH.fullmatch(parsed.path)
            locales = _LOCALES.get(region, set().union(*_LOCALES.values()))
            if not match or match[1] not in locales:
                return None
        elif parsed.hostname == "www.playstation.com":
            locales = _LOCALES.get(region, set().union(*_LOCALES.values()))
            if not any(re.fullmatch(r"/" + locale + r"/games/[a-z0-9-]+/?", parsed.path) for locale in locales):
                return None
        else:
            return None
        return urlunsplit(("https", parsed.hostname, parsed.path.rstrip("/"), "", ""))
    except ValueError:
        return None


def product_id(value):
    """Return the legacy store identity (a product or concept ID)."""
    url = official_url(value)
    if not url or urlsplit(url).hostname != "store.playstation.com":
        return None
    match = _STORE_PATH.fullmatch(urlsplit(url).path)
    return match[3] or match[5]


def concept_id(value):
    """Return a concept identity only for an explicit regional concept route."""
    url = official_url(value)
    if not url or urlsplit(url).hostname != "store.playstation.com":
        return None
    return _STORE_PATH.fullmatch(urlsplit(url).path)[5]


def _normalize(value):
    value = value.replace("™", "").replace("®", "").replace("©", "")
    return "".join(c for c in unicodedata.normalize("NFKC", unescape(value)).casefold()
                   if c.isalnum())


def _title_matches(title, name):
    # Remove only a recognizable language-version suffix, never Deluxe, DLC,
    # remaster or bundle labels that change the product identity.
    suffix = re.search(r"\s*\(([^()]*)\)\s*$", title)
    if suffix:
        label = re.sub(r"\s+(?:Ver\.|Version)$", "", suffix[1], flags=re.I).strip()
        parts = re.split(r"\s*[/,]\s*", label)
        recognized = {value.casefold() for value in _LANGUAGES}
        if parts and all(part.casefold() in recognized for part in parts):
            title = title[:suffix.start()].strip()
    return _normalize(title) == _normalize(name)


class _StoreHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self._read = False
        self._parts = []
        self.subtitles = []
        self._subtitles = False
        self._subtitle_parts = []
        self.release_times = []
        self.native_headers = []
        self._proof_tag = None
        self._proof_depth = 0
        self._proof_kind = None
        self._proof_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self._proof_tag == tag:
            self._proof_depth += 1
        if self._proof_tag is None:
            qa = attrs.get("data-qa") or ""
            kind = ("release_times" if qa == "mfe-game-title#release-date" else
                    "native_headers" if re.fullmatch(r"mfe-compatibility-notices#notices#notice[0-9]+#compatTextHeader", qa) else None)
            if kind:
                self._proof_tag, self._proof_depth, self._proof_kind = tag, 1, kind
                self._proof_parts = []
        if tag == "script" and attrs.get("type", "").lower() == "application/json":
            self._read = True
            self._parts = []
        if tag == "dd" and attrs.get("data-qa") == "gameInfo#releaseInformation#subtitles-value":
            self._subtitles = True
            self._subtitle_parts = []

    def handle_data(self, value):
        if self._read:
            self._parts.append(value)
        if self._subtitles:
            self._subtitle_parts.append(value)
        if self._proof_tag:
            self._proof_parts.append(value)

    def handle_endtag(self, tag):
        if tag == self._proof_tag:
            self._proof_depth -= 1
            if self._proof_depth == 0:
                getattr(self, self._proof_kind).append("".join(self._proof_parts).strip())
                self._proof_tag = self._proof_kind = None
                self._proof_parts = []
        if tag == "script" and self._read:
            self.scripts.append("".join(self._parts))
            self._read = False
        if tag == "dd" and self._subtitles:
            self.subtitles.append("".join(self._subtitle_parts).strip())
            self._subtitles = False


_LANGUAGES = {
    "Chinese (Traditional)": "zh-Hant", "Traditional Chinese": "zh-Hant",
    "Chinese (Simplified)": "zh-Hans", "Simplified Chinese": "zh-Hans",
    "Chinese": "zh", "English": "en", "Japanese": "ja", "Korean": "ko",
    "French (France)": "fr", "French": "fr", "German": "de", "Italian": "it",
    "Spanish": "es", "Spanish (Spain)": "es", "Spanish (Mexico)": "es-419",
    "Portuguese": "pt", "Portuguese (Brazil)": "pt-BR", "Russian": "ru",
    "Dutch": "nl", "Polish": "pl", "Czech": "cs", "Ukrainian": "uk",
    "Arabic": "ar", "Thai": "th", "Turkish": "tr", "Hungarian": "hu",
    "Swedish": "sv", "Danish": "da", "Finnish": "fi", "Norwegian": "no",
}


def _concept_release(parser, documents, match, name_en, region):
    """Read an announced native concept only with a displayed UTC instant.

    Some future games have no purchasable product yet. Their exact concept
    cache, native version notice, and displayed time can still authenticate a
    release instant. A date-only value or a generic site PS5 label cannot.
    """
    identity = match[5]
    if not identity:
        return None
    concept = {}
    for document in documents:
        if document.get("args", {}).get("conceptId") != identity:
            continue
        row = document["cache"].get("Concept:" + identity)
        if row is None:
            continue
        if (not isinstance(row, dict) or row.get("id") != identity
                or row.get("__typename") != "Concept"):
            return None
        for key in ("name", "invariantName", "defaultProduct", "isAnnounce", "products",
                    "platforms", "compatibilityNoticesByPlatform", "releaseDate"):
            if key not in row:
                continue
            value = row[key]
            if key == "releaseDate":
                if not isinstance(value, dict):
                    return None
                previous = concept.setdefault(key, {})
                for field in ("type", "value"):
                    if field not in value:
                        continue
                    if field in previous and previous[field] != value[field]:
                        return None
                    previous[field] = deepcopy(value[field])
            elif key in concept and concept[key] != value:
                return None
            else:
                concept[key] = deepcopy(value)
    if (not isinstance(concept.get("name"), str) or not _title_matches(concept["name"], name_en)
            or (concept.get("invariantName") and _normalize(concept["invariantName"]) != _normalize(name_en))
            or concept.get("isAnnounce") is not True or concept.get("defaultProduct") is not None
            or concept.get("products") != [] or concept.get("platforms") not in (None, [], ["PS5"])):
        return None
    notices = concept.get("compatibilityNoticesByPlatform")
    if (not isinstance(notices, dict)
            or any(value for code, value in notices.items() if code not in {"__typename", "PS5", "Common"})):
        return None
    native = notices.get("PS5")
    if (not isinstance(native, list) or not native
            or not all(isinstance(row, dict) and row.get("targetPlatforms") == ["PS5"] for row in native)
            or not parser.native_headers
            or any(label not in {"PS5版本", "PS5 Version", "PS5 version"} for label in parser.native_headers)):
        return None
    release = concept.get("releaseDate")
    if (not isinstance(release, dict) or release.get("type") != "DAY_MONTH_YEAR"
            or not isinstance(release.get("value"), str) or not parser.release_times):
        return None
    try:
        stamp = datetime.fromisoformat(release["value"].replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            return None
        utc = stamp.astimezone(timezone.utc)
        displayed = [datetime.strptime(re.sub(r"\s+", " ", text), "%d/%m/%Y %I:%M %p UTC").replace(tzinfo=timezone.utc)
                     for text in parser.release_times]
        if any(value != utc for value in displayed):
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return {"product_id": None, "concept_id": identity, "official_title": concept["name"],
            "region": region, "source_url": "https://store.playstation.com/" +
            ("zh-hant-tw" if region == "taiwan" else "zh-hant-hk") + "/concept/" + identity,
            "supported_languages": [], "complete": False, "evidence_type": "official_concept_release_time",
            "release_time_utc": utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "date": utc.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()}


def parse_store_metadata(html, url, name_en):
    """Read only the page's exact product caches; reject conflicts and editions."""
    safe = official_url(url)
    if not safe or urlsplit(safe).hostname != "store.playstation.com" or not isinstance(name_en, str):
        return None
    match = _STORE_PATH.fullmatch(urlsplit(safe).path)
    region = next(key for key, locales in _LOCALES.items() if match[1] in locales)
    parser = _StoreHTML()
    parser.feed(html)
    products = {}
    documents = []
    exact_id = match[3]
    for script in parser.scripts:
        try:
            document = json.loads(script)
        except (TypeError, ValueError, RecursionError):
            continue
        if not isinstance(document, dict) or not isinstance(document.get("cache"), dict):
            continue
        overrides = document.get("overrides")
        if not isinstance(overrides, dict) or overrides.get("locale") != match[1]:
            continue
        args = document.get("args", {})
        if not isinstance(args, dict):
            continue
        documents.append(document)
        selected = args.get("productId") if isinstance(args, dict) else None
        if exact_id:
            if selected != exact_id:
                continue
            rows = [("Product:" + exact_id, document["cache"].get("Product:" + exact_id))]
        elif args.get("conceptId") == match[5]:
            rows = [(key, row) for key, row in document["cache"].items() if key.startswith("Product:")]
        elif isinstance(selected, str) and re.fullmatch(PRODUCT_ID, selected):
            rows = [("Product:" + selected, document["cache"].get("Product:" + selected))]
        else:
            continue
        for cache_key, row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                    or not re.fullmatch(PRODUCT_ID, row["id"]) or row.get("__typename") != "Product"
                    or cache_key != "Product:" + row["id"]):
                continue
            product = products.setdefault(row["id"], {})
            concept = row.get("concept")
            if isinstance(concept, dict) and "__ref" in concept:
                if "concept_ref" in product and product["concept_ref"] != concept["__ref"]:
                    return None
                product["concept_ref"] = concept["__ref"]
            for key, value in row.items():
                if key not in {"id", "name", "invariantName", "releaseDate", "platforms", "edition", "screenLanguagesByPlatform", "topCategory", "storeDisplayClassification", "type"}:
                    continue
                if key == "edition":
                    if not isinstance(value, dict):
                        return None
                    previous = product.setdefault("edition", {})
                    for field in ("name", "type"):
                        if field not in value:
                            continue
                        if field in previous and previous[field] != value[field]:
                            return None
                        previous[field] = deepcopy(value[field])
                    continue
                if key in product and product[key] != value:
                    return None
                product[key] = deepcopy(value)
    # Concepts can contain premium editions and DLC. Match one precise base
    # identity before reading date/languages; unrelated products stay unused.
    candidates = [row for row in products.values() if isinstance(row.get("name"), str)
                  and _title_matches(row["name"], name_en)
                  and (not match[5] or row.get("concept_ref") == "Concept:" + match[5])]
    if not products:
        return _concept_release(parser, documents, match, name_en, region)
    if len(candidates) != 1:
        return None
    product = candidates[0]
    if (not product or not isinstance(product.get("name"), str)
            or not _title_matches(product["name"], name_en)
            or (product.get("invariantName") and _normalize(product["invariantName"]) != _normalize(name_en))
            or not isinstance(product.get("platforms"), list) or "PS5" not in product["platforms"]
            or product.get("topCategory") not in {None, "GAME"}
            or product.get("type") not in {None, "GAME"}
            or product.get("storeDisplayClassification") != "FULL_GAME"
            or product.get("edition", {}).get("name") not in {None, "", "Standard Edition"}
            or product.get("edition", {}).get("type") not in {None, "STANDARD"}):
        return None
    public_url = "https://store.playstation.com/" + ("zh-hant-tw" if region == "taiwan" else "zh-hant-hk") + "/product/" + product["id"]
    result = {"product_id": product["id"], "official_title": product["name"],
              "region": region, "source_url": public_url, "supported_languages": [], "complete": False}
    release = product.get("releaseDate")
    if isinstance(release, str):
        try:
            stamp = datetime.fromisoformat(release.replace("Z", "+00:00"))
            if stamp.utcoffset() is not None:
                result["release_time_utc"] = stamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                result["date"] = stamp.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
        except ValueError:
            pass
    versions = product.get("screenLanguagesByPlatform")
    if isinstance(versions, list):
        native = [row.get("screenLanguages") for row in versions if isinstance(row, dict) and row.get("platform") == "PS5"]
        if len(native) == 1 and isinstance(native[0], list) and native[0]:
            values = [row.get("value") if isinstance(row, dict) else row for row in native[0]]
            if all(isinstance(value, str) and value in _LANGUAGES for value in values):
                result["supported_languages"] = list(dict.fromkeys(_LANGUAGES[value] for value in values))
                result["complete"] = True
                result["evidence_type"] = "official_product_languages"
            elif product["platforms"] == ["PS5"] and len(parser.subtitles) == 1:
                # Sony's scalar codes (including ambiguous `ch`/`zh`) are not
                # IGDB/ISO language codes. Use its displayed regional language
                # names only when this product is solely a native PS5 version.
                labels = [value.strip() for value in parser.subtitles[0].split(",")]
                if labels and all(label in _LANGUAGES for label in labels):
                    result["supported_languages"] = list(dict.fromkeys(_LANGUAGES[label] for label in labels))
                    result["complete"] = True
                    result["evidence_type"] = "official_product_languages"
    if not result["supported_languages"]:
        suffix = re.search(r"\(([^()]*)\)\s*$", product["name"])
        labels = [label.strip() for label in re.split(r"[/,]", suffix[1])] if suffix else []
        if (labels and any(label in {"Traditional Chinese", "Simplified Chinese"} for label in labels)
                and all(label in _LANGUAGES for label in labels)):
            # An exact native product title can prove listed languages, but its
            # abbreviated label does not prove that unlisted languages lack support.
            result.update(supported_languages=list(dict.fromkeys(_LANGUAGES[label] for label in labels)),
                          evidence_type="official_product_languages")
    if not result["supported_languages"] and re.search(r"\([^)]*\bChinese\b[^)]*\)", product["name"], re.I):
        # A generic Chinese storefront suffix cannot prove script variants.
        result.update(supported_languages=["zh"], evidence_type="official_chinese_unspecified")
    return result


def _discovery_store_url(value, region):
    """Reuse an exact IGDB-linked store ID with another regional storefront."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname != "store.playstation.com"
                or parsed.username or parsed.password or parsed.port is not None or parsed.query or parsed.fragment):
            return None
        match = _STORE_PATH.fullmatch(parsed.path)
        if not match:
            return None
        path = "/" + ("en-tw" if region == "taiwan" else "en-hk") + "/" + (match[2] or match[4]) + "/" + (match[3] or match[5])
        return "https://store.playstation.com" + path
    except ValueError:
        return None


class PlayStationStoreClient:
    """Investigate linked products without unauthenticated search or retries."""
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._owns_session = session is None
        if self._owns_session:
            self.session.trust_env = False

    def investigate(self, games):
        started = time.monotonic()
        pages, cache, result, rate_limited = 0, {}, {}, False
        try:
            for game in games:
                if (not isinstance(game, dict) or type(game.get("id")) is not int or not isinstance(game.get("name"), str)
                        or not any(isinstance(row, dict) and row.get("id") == 167 for row in game.get("platforms", []))):
                    continue
                found, attempted, last_status = None, [], "rate_limited" if rate_limited else "no_exact_store_url"
                websites = game.get("websites") if isinstance(game.get("websites"), list) else []
                for region in ("taiwan", "hong_kong"):
                    for website in websites:
                        if rate_limited:
                            break
                        url = _discovery_store_url(website.get("url") if isinstance(website, dict) else None, region)
                        if not url or url in attempted:
                            continue
                        remaining = DEADLINE_SECONDS - (time.monotonic() - started)
                        if remaining <= 0 or pages >= MAX_PAGES:
                            last_status = "budget_exhausted"
                            break
                        attempted.append(url)
                        if url not in cache:
                            pages += 1
                            try:
                                response = self.session.get(url, timeout=min(TIMEOUT_SECONDS, remaining), allow_redirects=False)
                                if response.status_code == 429:
                                    cache[url] = None
                                    rate_limited, last_status = True, "rate_limited"
                                    break
                                if response.status_code != 200 or len(response.content) > MAX_HTML_BYTES or official_url(response.url) != url:
                                    cache[url] = None
                                else:
                                    cache[url] = response.text
                            except (requests.RequestException, ValueError, UnicodeError):
                                cache[url] = None
                        if cache[url] is None:
                            last_status = "unavailable"
                            continue
                        try:
                            found = parse_store_metadata(cache[url], url, game["name"])
                        except (ValueError, TypeError, AttributeError, RecursionError):
                            found = None
                        if found:
                            break
                        last_status = "identity_or_metadata_unconfirmed"
                    if found or rate_limited:
                        break
                result["igdb:" + str(game["id"])] = {"igdb_id": game["id"], "name_en": game["name"],
                    "status": ("confirmed_concept" if found.get("concept_id") else "confirmed_product") if found else last_status,
                    "attempted_urls": attempted,
                    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "evidence": found}
        finally:
            if self._owns_session:
                self.session.close()
        return {"schema_version": 1, "provider": "PlayStation Store", "request_count": pages, "games": result}
