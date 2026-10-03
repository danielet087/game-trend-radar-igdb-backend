"""Confirm exclusivity only from game-specific Nintendo evidence.

IGDB listing a single platform is insufficient. Network enrichment is optional;
unavailable or conflicting evidence retains the collector's conservative label.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import requests


MAX_PAGES = 30
TIMEOUT_SECONDS = 5
DEADLINE_SECONDS = 90
MAX_HTML_BYTES = 2_000_000
PLATFORM_IDS = {130: "NS", 508: "NS2"}
_HOSTS = {"www.nintendo.com", "nintendo.com", "www.nintendo.co.jp", "nintendo.co.jp"}


def _normalize(value: str) -> str:
    value = value.replace("™", "").replace("®", "").replace("©", "")
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold()
                   if char.isalnum())


# These are discovery URLs, not an assertion based on Nintendo branding.
_PRODUCT_URLS = {
    _normalize("Fire Emblem: Fortune's Weave"): "https://www.nintendo.com/us/store/products/fire-emblem-fortunes-weave-switch-2/",
    _normalize("The Duskbloods"): "https://www.nintendo.com/us/store/products/the-duskbloods-switch-2/",
    _normalize("Nintendo Switch Sports Resort"): "https://www.nintendo.com/us/store/products/nintendo-switch-sports-resort-switch-2/",
}

# Verified against this precise IGDB identity and the Nintendo product page on
# 2026-10-03. This fallback expires, so a later platform change is not hidden.
_VERIFIED_FACTS = {
    366896: {
        "name": "Fire Emblem: Fortune's Weave",
        "platform": "NS2",
        "url": _PRODUCT_URLS[_normalize("Fire Emblem: Fortune's Weave")],
        "evidence": "Exclusively for Nintendo Switch 2",
        "checked_at": "2026-10-03T16:13:39Z",
    },
}


def _official_url(value) -> str | None:
    """Allow HTTPS Nintendo product pages, with redirects disabled by callers."""
    if not isinstance(value, str) or len(value) > 1500:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in _HOSTS
                or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            return None
    except ValueError:
        return None
    path = parsed.path
    if parsed.hostname.endswith("nintendo.com"):
        if not re.fullmatch(r"/(?:us|en-ca|en-gb|au|tw)/store/products/[a-z0-9-]+/?", path):
            return None
    elif not re.fullmatch(r"/switch2?/[a-zA-Z0-9_/-]+(?:\.html)?", path):
        return None
    return urlunsplit(("https", parsed.hostname, path, "", ""))


class _ProductMetadata(HTMLParser):
    """Read scoped metadata; unrelated body, navigation and footer are ignored."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = []
        self.meta = {}
        self.json_ld = []
        self._title = False
        self._json = False
        self._parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._title = True
        elif tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key in {"og:title", "og:description", "description"}:
                self.meta[key] = attrs.get("content", "")
        elif tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._json = True
            self._parts = []

    def handle_data(self, data):
        if self._title:
            self.title.append(data)
        if self._json:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        elif tag == "script" and self._json:
            self.json_ld.append("".join(self._parts))
            self._json = False


_TITLE_SUFFIX = re.compile(
    r"(?:\s*[-–—|]\s*Nintendo Official Site(?: for [\w ]+)?|"
    r"\s+for Nintendo Switch(?:[™®])?\s*(?:2)?|"
    r"\s*[-–—|]\s*Nintendo Switch(?:[™®])?\s*(?:2)?\s+Exclusive)\s*$",
    re.IGNORECASE,
)
_EXCLUSIVE_PATTERNS = (
    re.compile(r"(?:available\s+)?(?:exclusively\s+(?:for|on)|only\s+on)\s+"
               r"(?:the\s+)?Nintendo\s+Switch[™®]?\s*(2)?\b(?:\s+system)?", re.I),
    re.compile(r"Nintendo\s+Switch[™®]?\s*(2)?\s*[-–—]?\s+exclusive"
               r"(?=\s*(?:[.,;:!?–—-]|$)|\s+(?:game|title)\b)", re.I),
)


def _title_matches(title: str, name: str) -> bool:
    title = unescape(title).strip()
    previous = None
    while previous != title:
        previous, title = title, _TITLE_SUFFIX.sub("", title).strip()
    return _normalize(title) == _normalize(name)


def _exclusive_phrase(value: str):
    value = re.sub(r"\s+", " ", unescape(value)).strip()
    if re.search(r"\b(?:not|non)\s*[- ]?exclusive\b", value, re.I):
        return None
    found = []
    for pattern in _EXCLUSIVE_PATTERNS:
        for match in pattern.finditer(value):
            # An exclusive offer, feature, content pack or mode is not the game.
            following = value[match.end():].lstrip().lower()
            if re.match(r"(?:features?|content|offers?|bonuses?|items?|modes?)\b", following):
                continue
            found.append(("NS2" if match.group(1) == "2" else "NS", match.group(0)[:200]))
    return found[0] if found and len({item[0] for item in found}) == 1 else None


def _json_products(value):
    if isinstance(value, list):
        for row in value:
            yield from _json_products(row)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if isinstance(types, list) and any(item in {"Product", "VideoGame"} for item in types):
            yield value
        if isinstance(value.get("@graph"), list):
            yield from _json_products(value["@graph"])


def _page_evidence(html: str, name: str):
    parser = _ProductMetadata()
    parser.feed(html)
    titles = ["".join(parser.title), parser.meta.get("og:title", "")]
    if any(_title_matches(title, name) for title in titles):
        for title in titles:
            if _title_matches(title, name):
                evidence = _exclusive_phrase(title)
                if evidence:
                    return (*evidence, title)
        for field in ("og:description", "description"):
            description = parser.meta.get(field, "")
            # Metadata descriptions are only used when they name this game;
            # "exclusive hardware offers" on generic pages cannot qualify.
            if _normalize(name) in _normalize(description):
                evidence = _exclusive_phrase(description)
                if evidence:
                    return (*evidence, next(title for title in titles if _title_matches(title, name)))
    for raw in parser.json_ld:
        try:
            products = _json_products(json.loads(raw))
            for product in products:
                product_name = product.get("name")
                description = product.get("description")
                if (isinstance(product_name, str) and _title_matches(product_name, name)
                        and isinstance(description, str)):
                    evidence = _exclusive_phrase(description)
                    if evidence:
                        return (*evidence, product_name)
        except (ValueError, TypeError, RecursionError):
            continue
    return None


def _platform(game):
    if game.get("platform_data_complete") is not True:
        return None
    rows = game.get("known_platforms")
    if not isinstance(rows, list) or not rows:
        return None
    ids = {row.get("id") for row in rows if isinstance(row, dict) and type(row.get("id")) is int}
    if len(ids) != 1 or len(rows) != len([row for row in rows if isinstance(row, dict) and type(row.get("id")) is int]):
        return None
    platform = PLATFORM_IDS.get(next(iter(ids)))
    native = game.get("platforms", [])
    if not isinstance(native, list) or any(not isinstance(row, dict) or type(row.get("id")) is not int for row in native):
        return None
    native_ids = {row["id"] for row in native}
    if (not platform or any(row.get("code") != platform for row in native)
            or any(row.get("code", platform) != platform for row in rows)):
        return None
    return platform if ids == native_ids else None


def _game_urls(game):
    candidates = [game.get("nintendo_url")]
    for website in game.get("websites", []) if isinstance(game.get("websites"), list) else []:
        candidates.append(website.get("url") if isinstance(website, dict) else website)
    name = game.get("name_en")
    if isinstance(name, str):
        candidates.append(_PRODUCT_URLS.get(_normalize(name)))
    seen = set()
    for candidate in candidates:
        url = _official_url(candidate)
        if url and url not in seen:
            seen.add(url)
            yield url


def _confirm(game, platform, url, evidence, checked_at, evidence_title, method="product_metadata"):
    game["exclusivity"] = {
        "status": "confirmed", "platform": platform, "source": "Nintendo official",
        "url": url, "evidence": evidence[:200], "checked_at": checked_at,
        "evidence_title": evidence_title, "evidence_method": method,
    }
    game["nintendo_url"] = url


def enrich_exclusivity(catalog, session=None):
    """Return an enriched copy; missing or failed evidence never implies exclusivity."""
    result = deepcopy(catalog)
    games = result.get("games") if isinstance(result, dict) else None
    if not isinstance(games, list):
        return result
    started = time.monotonic()
    checked = datetime.now(timezone.utc)
    requester = session or requests.Session()
    if session is None:
        requester.trust_env = False
    pages = 0
    cache = {}
    try:
        for game in games:
            if not isinstance(game, dict):
                continue
            name, platform = game.get("name_en"), _platform(game)
            if not isinstance(name, str) or not name or not platform:
                continue
            igdb_id = game.get("igdb_id")
            fact = _VERIFIED_FACTS.get(igdb_id) if type(igdb_id) is int else None
            if fact and _normalize(name) == _normalize(fact["name"]) and platform == fact["platform"]:
                fact_date = datetime.fromisoformat(fact["checked_at"].replace("Z", "+00:00"))
                if timedelta(0) <= checked - fact_date < timedelta(days=30):
                    _confirm(game, platform, fact["url"], fact["evidence"], fact["checked_at"],
                             fact["name"], "curated_verified_fact")
                    continue
            for url in _game_urls(game):
                remaining = DEADLINE_SECONDS - (time.monotonic() - started)
                if remaining <= 0 or pages >= MAX_PAGES:
                    break
                if url not in cache:
                    pages += 1
                    try:
                        response = requester.get(url, timeout=min(TIMEOUT_SECONDS, remaining),
                                                 allow_redirects=False)
                        if response.status_code != 200 or len(response.content) > MAX_HTML_BYTES:
                            cache[url] = None
                        elif _official_url(response.url) != url:
                            cache[url] = None
                        else:
                            cache[url] = response.text
                    except (requests.RequestException, ValueError, UnicodeError):
                        cache[url] = None
                if cache[url] is None:
                    continue
                try:
                    evidence = _page_evidence(cache[url], name)
                except (ValueError, TypeError, RecursionError):
                    evidence = None
                if evidence and evidence[0] == platform:
                    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                    _confirm(game, platform, url, evidence[1], stamp, evidence[2])
                    break
    finally:
        if session is None:
            requester.close()
    return result


def validate_enriched_game(base_game, enriched_game):
    """Offline publisher guard: enrichment can alter only verified evidence fields."""
    if not isinstance(base_game, dict) or not isinstance(enriched_game, dict):
        return False
    if base_game == enriched_game:
        return True
    without_evidence = deepcopy(enriched_game)
    for field in ("exclusivity", "nintendo_url"):
        if field in base_game:
            without_evidence[field] = deepcopy(base_game[field])
        else:
            without_evidence.pop(field, None)
    if without_evidence != base_game:
        return False
    evidence = enriched_game.get("exclusivity")
    name, platform = base_game.get("name_en"), _platform(base_game)
    if not isinstance(evidence, dict) or not isinstance(name, str) or not platform:
        return False
    allowed = {"status", "platform", "source", "url", "evidence", "checked_at", "evidence_title", "evidence_method"}
    if set(evidence) != allowed or evidence.get("status") != "confirmed" or evidence.get("platform") != platform:
        return False
    url = _official_url(evidence.get("url"))
    snippet, title = evidence.get("evidence"), evidence.get("evidence_title")
    if (evidence.get("source") != "Nintendo official" or not url
            or evidence.get("url") != url or enriched_game.get("nintendo_url") != url
            or not isinstance(snippet, str) or not 0 < len(snippet) <= 200
            or not isinstance(title, str) or len(title) > 500
            or not _title_matches(title, name)):
        return False
    phrase = _exclusive_phrase(snippet)
    if not phrase or phrase[0] != platform:
        return False
    try:
        stamp = datetime.fromisoformat(evidence["checked_at"].replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - stamp
        if stamp.utcoffset() is None or not timedelta(0) <= age < timedelta(days=30):
            return False
    except (AttributeError, TypeError, ValueError):
        return False
    method = evidence.get("evidence_method")
    if method == "curated_verified_fact":
        igdb_id = base_game.get("igdb_id")
        fact = _VERIFIED_FACTS.get(igdb_id) if type(igdb_id) is int else None
        return bool(fact and _normalize(name) == _normalize(fact["name"])
                    and fact["platform"] == platform and fact["url"] == url
                    and fact["checked_at"] == evidence["checked_at"]
                    and fact["evidence"] == snippet and fact["name"] == title)
    return method == "product_metadata" and url in set(_game_urls(base_game))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    args = parser.parse_args(argv)
    public_path = args.output_dir / "nintendo_upcoming.json"
    master_path = args.output_dir / "nintendo_master.json"
    status_path = args.output_dir / "nintendo_refresh_status.json"
    catalog = json.loads(public_path.read_text(encoding="utf-8"))
    master = json.loads(master_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    enriched = enrich_exclusivity(catalog)
    for base, game in zip(catalog["games"], enriched["games"]):
        if not validate_enriched_game(base, game):
            raise ValueError("invalid_exclusivity_enrichment")
        entry = master["games"][game["id"]]
        updated = deepcopy(entry)
        for field in ("exclusivity", "nintendo_url"):
            updated[field] = deepcopy(game[field])
        if not validate_enriched_game(entry, updated):
            raise ValueError("inconsistent_master_exclusivity")
        master["games"][game["id"]] = updated
    counts = {kind: sum(game.get("exclusivity", {}).get("status") == kind
                       for game in enriched["games"])
              for kind in ("confirmed", "listed_only", "multi_platform", "unknown")}
    status["exclusivity_counts"] = counts
    for path, document in ((public_path, enriched), (master_path, master), (status_path, status)):
        pending = path.with_suffix(".json.tmp")
        pending.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        pending.replace(path)
    print(json.dumps({"status": "ok", "confirmed_count": counts["confirmed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
