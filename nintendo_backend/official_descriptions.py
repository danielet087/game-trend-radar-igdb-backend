"""Bounded Chinese descriptions from previously verified native product pages.

Registry identity is required before requesting a page. Descriptions establish
neither the game's supported languages nor a release date or platform version.
"""
from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
import time
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit

import requests

from .playstation import official_url as playstation_url, product_id as sony_product_id

MAX_REQUESTS = 30
MAX_HTML_BYTES = 2_000_000
MAX_DESCRIPTION_CHARS = 20_000
TIMEOUT_SECONDS = 5
DEADLINE_SECONDS = 90
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")
_EDITION = re.compile(r"\b(?:deluxe(?:\s+edition)?|ultimate\s+edition|season\s+pass|dlc|bundle)\b|豪華版|豪华版|終極版|终极版|擴充票|扩充票", re.I)
_LANGUAGE_SUFFIX = re.compile(r"\s*[（(](?:[英中日韓文語语版简簡繁體体文/、,\s]|English|Chinese|Traditional|Simplified|Japanese|Korean|Ver\.?|Version)+[）)]\s*$", re.I)


def _official_url(value):
    if not isinstance(value, str) or not 0 < len(value) <= 2000:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port is not None
                or parsed.query or parsed.fragment or "\\" in unquote(parsed.path)
                or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))):
            return None
        if parsed.hostname in {"store.playstation.com", "www.playstation.com"}:
            safe = playstation_url(value)
            if not safe:
                return None
            # Request the Chinese locale for the exact same regional identity.
            path = urlsplit(safe).path.replace("/en-tw/", "/zh-hant-tw/").replace("/en-hk/", "/zh-hant-hk/")
            return urlunsplit(("https", parsed.hostname, path, "", ""))
        if parsed.hostname == "ec.nintendo.com":
            valid = re.fullmatch(r"/(?:TW|HK)/zh/titles/[0-9]{14}/?", parsed.path)
        elif parsed.hostname == "www.nintendo.com":
            valid = re.fullmatch(r"/(?:tw|hk)/(?:games|software)/(?:switch2?/)?[a-zA-Z0-9_-]+(?:/index\.html)?/?", parsed.path)
        elif parsed.hostname in {"www.nintendo.com.hk", "nintendo.com.hk"}:
            valid = re.fullmatch(r"/(?:switch|switch2|software)/[a-zA-Z0-9_-]+(?:/index\.html)?/?", parsed.path)
        elif parsed.hostname == "store.nintendo.com.hk":
            valid = re.fullmatch(r"/[0-9]{14}(?:\.html)?/?", parsed.path)
        else:
            return None
        if not valid or any(part in {"lineup", "list", "schedule", "index"} for part in parsed.path.split("/")):
            return None
        return urlunsplit(("https", parsed.hostname, parsed.path.rstrip("/"), "", ""))
    except ValueError:
        return None


def _bound(raw_game, registry):
    if (not isinstance(raw_game, dict) or type(raw_game.get("id")) is not int or raw_game["id"] <= 0
            or not isinstance(raw_game.get("name"), str) or not raw_game["name"].strip()
            or not isinstance(registry, dict)):
        return None
    row = registry.get("igdb:" + str(raw_game["id"]))
    return row if (isinstance(row, dict) and type(row.get("igdb_id")) is int and row.get("igdb_id") == raw_game["id"]
                   and row.get("name_en") == raw_game["name"].strip()) else None


def candidates(raw_game, *, language_registry=None, release_registry=None, name_registry=None):
    """Reuse exact regional products, bound to the same IGDB ID and English name.

    A reviewed Nintendo product ID also permits its precise regional eShop page;
    a language registry's generic lineup URL itself is never requested.
    """
    language = _bound(raw_game, language_registry)
    release = _bound(raw_game, release_registry)
    name = _bound(raw_game, name_registry)
    if not any((language, release, name)):
        return []
    titles = [raw_game["name"].strip()]
    if name and isinstance(name.get("name_zh_tw"), str) and not _EDITION.search(name["name_zh_tw"]):
        titles.append(name["name_zh_tw"])
    rows = []
    if language and isinstance(language.get("platforms"), dict):
        rows.extend((platform, row) for platform, row in language["platforms"].items())
    if release and isinstance(release.get("releases"), list):
        rows.extend((row.get("platform"), row) for row in release["releases"] if isinstance(row, dict))
    found, eligible_platforms, excluded_products = {}, set(), set()
    for platform, row in rows:
        if (platform not in {"NS", "NS2", "PS5"} or not isinstance(row, dict)
                or row.get("edition_type") or row.get("edition_label")
                or _EDITION.search(str(row.get("official_title") or ""))):
            if isinstance(row, dict) and isinstance(row.get("product_id"), str):
                excluded_products.add(row["product_id"])
            continue
        eligible_platforms.add(platform)
        aliases = list(dict.fromkeys(titles + ([row["official_title"]] if isinstance(row.get("official_title"), str) else [])))
        urls = [row.get("source_url"), row.get("url")]
        product = row.get("product_id")
        if platform in {"NS", "NS2"} and row.get("region", "taiwan") in {"taiwan", "hong_kong"} and isinstance(product, str) and re.fullmatch(r"[0-9]{14}", product):
            region = "TW" if row.get("region", "taiwan") == "taiwan" else "HK"
            urls.append(f"https://ec.nintendo.com/{region}/zh/titles/{product}")
        for value in urls:
            url = _official_url(value)
            if not url or (platform == "PS5") != ("playstation.com" in urlsplit(url).hostname):
                continue
            if platform == "PS5" and product and "/product/" in url and sony_product_id(url) != product:
                continue
            existing = found.setdefault(url, {"url": url, "platform": platform, "titles": []})
            existing["titles"] = list(dict.fromkeys(existing["titles"] + aliases))
    # A reviewed localized name supplies an alias, not independent product proof.
    # Only use its URL when the game already has native language/release evidence.
    if name and eligible_platforms:
        url = _official_url(name.get("url"))
        if url:
            platform = ("PS5" if "PS5" in eligible_platforms else None) if "playstation.com" in urlsplit(url).hostname else next((p for p in ("NS2", "NS") if p in eligible_platforms), None)
            if platform and not any(product in url for product in excluded_products):
                found.setdefault(url, {"url": url, "platform": platform, "titles": list(dict.fromkeys(titles))})
    return list(found.values())


def _normalize_title(value):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 500 or _EDITION.search(value):
        return None
    value = _LANGUAGE_SUFFIX.sub("", unescape(value)).replace("™", "").replace("®", "").replace("©", "")
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def title_matches(title, titles):
    """Match only known identity aliases, preserving edition/remaster labels."""
    normalized = _normalize_title(title)
    return bool(normalized and isinstance(titles, (list, tuple))
                and normalized in {_normalize_title(value) for value in titles})


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "iframe", "object", "template"}:
            self.skip += 1
        if not self.skip and tag in {"p", "br", "div", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "iframe", "object", "template"}:
            self.skip = max(0, self.skip - 1)
        if not self.skip and tag in {"p", "div", "li", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _clean(value):
    if not isinstance(value, str) or not value.strip():
        return None
    parser = _Text()
    parser.feed(unescape(value))
    text = "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if (not 0 < len(text) <= MAX_DESCRIPTION_CHARS or not _HAN.search(text) or _KANA.search(text)
            or any(ord(char) < 32 and char not in "\n\t" for char in text)):
        return None
    return text


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.scripts = {}, []
        self._script, self._parts = None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {"og:title", "og:description"} and isinstance(attrs.get("content"), str):
                self.meta.setdefault(key, []).append(attrs["content"])
        if tag == "script" and attrs.get("type", "").lower() in {"application/ld+json", "application/json"}:
            self._script, self._parts = attrs["type"].lower(), []

    def handle_data(self, data):
        if self._script:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script:
            self.scripts.append((self._script, "".join(self._parts)))
            self._script, self._parts = None, []


def _jsonld_rows(document):
    if isinstance(document, list):
        for row in document:
            yield from _jsonld_rows(row)
    elif isinstance(document, dict):
        yield document
        # Never pull an unrelated recommended Product from arbitrary subtrees.
        if isinstance(document.get("@graph"), list):
            yield from _jsonld_rows(document["@graph"])
        if isinstance(document.get("mainEntity"), (dict, list)):
            yield from _jsonld_rows(document["mainEntity"])


def parse_description(html, url, game_name, titles=None):
    """Extract Chinese text only after exact title or Sony product-ID matching."""
    safe = _official_url(url)
    if not safe or not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        return None
    aliases = {_normalize_title(title) for title in [game_name, *(titles or [])]}
    aliases.discard(None)
    if not aliases:
        return None
    parser = _Page()
    try:
        parser.feed(html)
    except (ValueError, RecursionError):
        return None
    extracted = []
    product_id = sony_product_id(safe) if "/product/" in safe and urlsplit(safe).hostname == "store.playstation.com" else None
    for kind, script in parser.scripts:
        try:
            document = json.loads(script)
        except (ValueError, RecursionError):
            continue
        if kind == "application/ld+json":
            for row in _jsonld_rows(document):
                types = row.get("@type")
                types = types if isinstance(types, list) else [types]
                if not any(value in {"Product", "VideoGame"} for value in types if isinstance(value, str)):
                    continue
                title, text = row.get("name"), _clean(row.get("description"))
                if _normalize_title(title) in aliases and text:
                    extracted.append((title, text))
        elif product_id and isinstance(document, dict):
            if (not isinstance(document.get("args"), dict) or document["args"].get("productId") != product_id
                    or not isinstance(document.get("overrides"), dict)
                    or document["overrides"].get("locale") != urlsplit(safe).path.split("/")[1]
                    or not isinstance(document.get("cache"), dict)):
                continue
            row = document["cache"].get("Product:" + product_id)
            if (not isinstance(row, dict) or row.get("id") != product_id or row.get("__typename") != "Product"
                    or _normalize_title(row.get("name")) not in aliases
                    or (row.get("invariantName") and _normalize_title(row["invariantName"]) not in aliases)
                    or row.get("type") not in {None, "GAME"} or row.get("topCategory") not in {None, "GAME"}
                    or row.get("storeDisplayClassification") not in {None, "FULL_GAME"}):
                continue
            edition = row.get("edition", {})
            if not isinstance(edition, dict) or edition.get("type") not in {None, "STANDARD"} or edition.get("name") not in {None, "", "Standard Edition"}:
                continue
            for key in ("longDescription", "description", "shortDescription"):
                text = _clean(row.get(key))
                if text:
                    extracted.append((row["name"], text))
                    break
    if not extracted:
        meta_titles, descriptions = parser.meta.get("og:title", []), parser.meta.get("og:description", [])
        if len(set(meta_titles)) == 1 and len(set(descriptions)) == 1 and _normalize_title(meta_titles[0]) in aliases:
            text = _clean(descriptions[0])
            if text:
                extracted.append((meta_titles[0], text))
    # Conflicting same-product descriptions may be cache/localization mistakes.
    if not extracted or len({text for _, text in extracted}) != 1:
        return None
    return {"text": extracted[0][1], "source_url": safe,
            "provider": "PlayStation" if "playstation.com" in urlsplit(safe).hostname else "Nintendo",
            "official_title": extracted[0][0]}


class OfficialDescriptionClient:
    """Small optional stage: no redirects, retries, search, or credential use."""
    def __init__(self, session=None, *, max_requests=MAX_REQUESTS, deadline_seconds=DEADLINE_SECONDS,
                 timeout=TIMEOUT_SECONDS, clock=time.monotonic):
        self.session = session or requests.Session()
        self._owns_session = session is None
        if self._owns_session:
            self.session.trust_env = False
        self.max_requests = max(0, min(MAX_REQUESTS, max_requests))
        self.deadline_seconds = max(0, min(DEADLINE_SECONDS, deadline_seconds))
        self.timeout = max(0, min(TIMEOUT_SECONDS, timeout))
        self.clock, self.deadline = clock, None
        self.request_count, self.failure_count = 0, 0
        self.stopped, self.rate_limited = False, False
        self._cache = {}

    def _fetch(self, url):
        if url in self._cache:
            return self._cache[url]
        if self.deadline is None:
            self.deadline = self.clock() + self.deadline_seconds
        remaining = self.deadline - self.clock()
        if self.stopped or self.request_count >= self.max_requests or remaining <= 0 or self.timeout <= 0:
            return None
        self.request_count += 1
        self._cache[url] = None
        response = None
        try:
            response = self.session.get(url, timeout=min(self.timeout, remaining), allow_redirects=False, stream=True)
            headers = response.headers
            if response.status_code == 429 or (response.status_code == 503 and headers.get("Retry-After")):
                self.rate_limited, self.stopped = True, True
            if response.status_code != 200 or _official_url(response.url) != url:
                self.failure_count += 1
                return None
            if headers.get("Content-Length", "").isdigit() and int(headers["Content-Length"]) > MAX_HTML_BYTES:
                self.failure_count += 1
                return None
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > MAX_HTML_BYTES or self.clock() >= self.deadline:
                    self.failure_count += 1
                    return None
                chunks.append(chunk)
            body = b"".join(chunks)
            charset = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", headers.get("Content-Type", ""), re.I)
            encoding = charset[1] if charset else "utf-8"
            self._cache[url] = body.decode(encoding)
            return self._cache[url]
        except (requests.RequestException, UnicodeError, LookupError, ValueError):
            self.failure_count += 1
            return None
        finally:
            if response is not None:
                response.close()

    def lookup(self, raw_game, candidates):
        if (not isinstance(raw_game, dict) or type(raw_game.get("id")) is not int or raw_game["id"] <= 0
                or not isinstance(raw_game.get("name"), str) or not raw_game["name"].strip()):
            return None
        for row in candidates:
            if not isinstance(row, dict) or row.get("platform") not in {"NS", "NS2", "PS5"}:
                continue
            url = _official_url(row.get("url"))
            if not url or (row["platform"] == "PS5") != ("playstation.com" in urlsplit(url).hostname):
                continue
            html = self._fetch(url)
            if html:
                result = parse_description(html, url, raw_game["name"], row.get("titles"))
                if result:
                    return result
            if self.stopped:
                break
        return None

    def close(self):
        if self._owns_session:
            self.session.close()
