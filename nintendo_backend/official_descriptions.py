"""Bounded Chinese descriptions from verified native product pages.

A page is requested only for a reviewed registry product or, for a PS5 game,
the PlayStation Store item that IGDB itself links to the game; that item must
then name the same English game. Descriptions establish neither the game's
supported languages nor a release date or platform version.
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

from .playstation import (PRODUCT_ID as _SONY_PRODUCT, concept_id as sony_concept_id,
                          official_url as playstation_url, product_id as sony_product_id)

# The IGDB-linked PlayStation Store items (about 130 PS5 games, rechecked
# every three days until release) share this daily budget with the registry
# products.
MAX_REQUESTS = 60
MAX_HTML_BYTES = 2_000_000
MAX_DESCRIPTION_CHARS = 20_000
TIMEOUT_SECONDS = 5
DEADLINE_SECONDS = 150
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")
_EDITION = re.compile(r"\b(?:deluxe(?:\s+edition)?|ultimate\s+edition|season\s+pass|dlc|bundle)\b|豪華版|豪华版|終極版|终极版|擴充票|扩充票", re.I)
_LANGUAGE_SUFFIX = re.compile(r"\s*[（(](?:[英中日韓泰西法德義葡俄文語语版简簡繁體体/、,，\s]|English|Chinese|Traditional|Simplified|Japanese|Korean|Ver\.?|Version)+[）)]\s*$", re.I)
# Roman and Arabic sequel numbers name the same game (Dragon's Dogma II/2).
_ROMAN = {"II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8", "IX": "9", "X": "10"}
_ROMAN_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:VIII|VII|VI|IV|IX|III|II|X|V)(?![A-Za-z0-9])")
_SONY_ID = _SONY_PRODUCT + r"|[0-9]{1,12}"
_INVISIBLE = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
# Storefront notes rather than the game's own description: purchase, edition,
# trial, account/subscription and language-support notices.
_STORE_NOTE = re.compile(
    r"^[※＊*]|^(?:注意|註|備註|免責)[：:]|PlayStation\s*[®™]?\s*Plus|PS\s*Plus|網際網路連線|帳戶|"
    r"需單獨購買|另行購買|需另外購買|遊戲內購買|內購|預購|特典|早期購入|豪華版|終極版|同捆|套組|季票|擴充票|"
    r"購買遊戲|購買本|購買此|購買《|免費試玩|試玩版|體驗版|收藏清單|願望清單|^(?:內含|包含|內容包含)[：:]|"
    r"trademark|all rights reserved|版權所有|商標|https?://|條款|隱私權|@[A-Za-z0-9_]{3,}|"
    r"(?:支援|對應|新增|收錄|提供)[^。！？\n]{0,12}(?:語言|語音|配音|字幕)|(?:語言|語音|配音|字幕)[^。！？\n]{0,6}(?:支援|對應|[：:])|"
    r"^(?:支援|對應)[^。！？\n]*?(?:英文|日文|中文|韓文|法文|德文)[^。！？\n]*?(?:英文|日文|中文|韓文|法文|德文|西班牙文)",
    re.I)
_SENTENCE_END = re.compile(r"[。！？!?…」』）)]$")
# A description the store itself calls machine translated is not used.
# A copyright notice ends the description, even mid-line.
_COPYRIGHT = re.compile(r"\s*(?:copyright\s*[：:]?|©|Ⓒ|\(C\)\s*\d{4})", re.I)
_MACHINE_TRANSLATED = re.compile(r"(?:人工智慧|人工智能|機器|机器|AI\s*)翻譯|自動翻譯")
# A notes paragraph ("附註：" and the lines under it) and separator rules.
_NOTE_HEADING = re.compile(r"^(?:注意|註|附註|備註|免責)(?:事項|聲明)?[：:]?$")
_SEPARATOR = re.compile(r"^[-=_~*＊─━—–·•.。\s]{3,}$")
# Sony appends the platforms to some titles ("It Takes Two PS4 & PS5").
_PLATFORM_SUFFIX = re.compile(r"\s*(?:PS4\s*(?:&|和|與|/)\s*PS5|PS5\s*(?:&|和|與|/)\s*PS4|PS5|PS4)\s*$", re.I)


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


_LINKED_STORE = re.compile(r"/[a-z]{2}(?:-[a-z]{2,4}){1,2}/(product|concept)/(" + _SONY_ID + r")/?")


def linked_playstation_urls(raw_game):
    """The PlayStation Store items IGDB itself links to this PS5 game, read on
    the Taiwan Chinese storefront. The page must still name the same game."""
    if (not isinstance(raw_game, dict) or not isinstance(raw_game.get("platforms"), list)
            or not any(isinstance(row, dict) and row.get("id") == 167 for row in raw_game["platforms"])):
        return []
    urls = []
    for website in raw_game.get("websites") or []:
        value = website.get("url") if isinstance(website, dict) else None
        if not isinstance(value, str) or len(value) > 2000:
            continue
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        if (parsed.scheme != "https" or parsed.hostname != "store.playstation.com" or parsed.username
                or parsed.password or parsed.port is not None or parsed.query or parsed.fragment):
            continue
        match = _LINKED_STORE.fullmatch(parsed.path)
        if match:
            url = f"https://store.playstation.com/zh-hant-tw/{match[1]}/{match[2]}"
            if url not in urls:
                urls.append(url)
    return urls


def candidates(raw_game, *, language_registry=None, release_registry=None, name_registry=None):
    """Reuse exact regional products, bound to the same IGDB ID and English name.

    A reviewed Nintendo product ID also permits its precise regional eShop page;
    a language registry's generic lineup URL itself is never requested. After
    the reviewed products, a PS5 game's IGDB-linked store item is tried.
    """
    language = _bound(raw_game, language_registry)
    release = _bound(raw_game, release_registry)
    name = _bound(raw_game, name_registry)
    valid = (isinstance(raw_game, dict) and type(raw_game.get("id")) is int and raw_game["id"] > 0
             and isinstance(raw_game.get("name"), str) and bool(raw_game["name"].strip()))
    linked = linked_playstation_urls(raw_game) if valid else []
    if not any((language, release, name)) and not linked:
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
            # Nintendo's reviewed NSUID identifies this same product. A
            # regional storefront may not exist yet, so try the other Chinese
            # storefront for this exact ID rather than searching by title.
            for candidate_region in (region, "HK" if region == "TW" else "TW"):
                urls.append(f"https://ec.nintendo.com/{candidate_region}/zh/titles/{product}")
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
    # IGDB's English alternative names may be the store's English title.
    aliases = list(dict.fromkeys(titles + [row["name"] for row in raw_game.get("alternative_names") or []
                                           if isinstance(row, dict) and isinstance(row.get("name"), str)]))
    for url in linked:
        if not any(product in url for product in excluded_products):
            found.setdefault(url, {"url": url, "platform": "PS5", "titles": aliases})
    return list(found.values())


def _normalize_title(value):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 500 or _EDITION.search(value):
        return None
    value = _LANGUAGE_SUFFIX.sub("", unescape(value)).replace("™", "").replace("®", "").replace("©", "")
    value = _PLATFORM_SUFFIX.sub("", value)
    value = _ROMAN_TOKEN.sub(lambda match: _ROMAN[match[0]], unicodedata.normalize("NFKC", value))
    return "".join(char for char in value.casefold() if char.isalnum())


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


def _store_copy(value):
    """A PlayStation Store description without its storefront notes."""
    text = _clean(_INVISIBLE.sub("", value)) if isinstance(value, str) else None
    if not text:
        return None
    if _MACHINE_TRANSLATED.search(text):
        return None
    text = text.strip("\"“”").strip()
    # A notes heading drops itself and the paragraph under it.
    lines, skipping = [], None
    for line in text.split("\n"):
        cut = _COPYRIGHT.search(line)
        if cut:
            line = line[:cut.start()]
        stripped = line.strip()
        if _NOTE_HEADING.fullmatch(stripped):
            skipping = "heading"
            continue
        if skipping == "heading" and not stripped:
            continue
        if skipping:
            skipping = "paragraph" if stripped and not _SEPARATOR.fullmatch(stripped) else None
            if skipping:
                continue
        if not _SEPARATOR.fullmatch(stripped) and not _STORE_NOTE.search(stripped):
            lines.append(line)
        if cut:
            break
    while lines and not lines[0].strip():
        lines.pop(0)
    # A heading left without the section a note removed.
    while lines and (not lines[-1].strip() or (len(lines[-1].strip()) <= 20 and not _SENTENCE_END.search(lines[-1].strip()))):
        lines.pop()
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    # A tagline or an English description is not a usable Chinese description.
    return text if len(_HAN.findall(text)) >= 10 and not _KANA.search(text) else None


def store_chinese_name(name, invariant=None):
    """The Taiwan storefront's own Chinese title, without marks or a language label.

    "English (中文)" keeps the Chinese part when the English part is the
    store's invariant English title. Editions and Japanese titles are refused.
    """
    if not isinstance(name, str) or not 0 < len(name) <= 240:
        return None
    value = _INVISIBLE.sub("", name).replace("™", "").replace("®", "").replace("©", "").strip()
    value = _PLATFORM_SUFFIX.sub("", _LANGUAGE_SUFFIX.sub("", value)).strip()
    if re.fullmatch(r"《[^《》]+》", value):
        value = value[1:-1].strip()
    # "English (中文)" or "中文 (English)": keep the Chinese part when the
    # other part is the store's own English title.
    pair = re.fullmatch(r"(.+?)\s*[（(]([^()（）]+)[)）]", value)
    if pair and _normalize_title(invariant):
        first, second = pair[1].strip(), pair[2].strip()
        for chinese, english in ((second, first), (first, second)):
            if (_HAN.search(chinese) and not _HAN.search(english)
                    and _normalize_title(english) == _normalize_title(invariant)):
                value = chinese
                break
    if (not _HAN.search(value) or _KANA.search(value) or _EDITION.search(value)
            or "<" in value or ">" in value or any(ord(char) < 32 for char in value)):
        return None
    return value


def _store_row(scripts, kind, identity, locale):
    """One Product or Concept cache row, merged across the page's documents.

    Sony splits a row's fields over several documents; a field that differs
    between them rejects the page. Descriptions merge by their type.
    """
    key, argument = ("Product:", "productId") if kind == "product" else ("Concept:", "conceptId")
    merged, descriptions = {}, {}
    for script_kind, script in scripts:
        if script_kind != "application/json":
            continue
        try:
            document = json.loads(script)
        except (ValueError, RecursionError):
            continue
        if (not isinstance(document, dict) or not isinstance(document.get("cache"), dict)
                or not isinstance(document.get("overrides"), dict) or document["overrides"].get("locale") != locale
                or not isinstance(document.get("args"), dict) or document["args"].get(argument) != identity):
            continue
        row = document["cache"].get(key + identity)
        if row is None:
            continue
        if (not isinstance(row, dict) or row.get("id", identity) != identity
                or row.get("__typename", key[:-1]) != key[:-1]):
            return None
        for field, value in row.items():
            if field in {"id", "__typename"}:
                continue
            if field == "descriptions":
                if not isinstance(value, list):
                    return None
                for item in value:
                    if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                        return None
                    if "value" not in item:
                        continue
                    if descriptions.get(item["type"], item["value"]) != item["value"]:
                        return None
                    descriptions[item["type"]] = item["value"]
            elif field == "edition":
                if not isinstance(value, dict):
                    return None
                previous = merged.setdefault("edition", {})
                for name in ("name", "type"):
                    if name in value:
                        if name in previous and previous[name] != value[name]:
                            return None
                        previous[name] = value[name]
            elif field in {"name", "invariantName", "platforms", "type", "topCategory",
                           "storeDisplayClassification", "longDescription", "description", "shortDescription"}:
                if field in merged and merged[field] != value:
                    return None
                merged[field] = value
    if not merged and not descriptions:
        return None
    merged["descriptions"] = descriptions
    return merged


_STANDARD_EDITIONS = {None, "", "Standard Edition", "標準版", "數位標準版"}


def _store_description(scripts, safe, aliases, platform):
    """Description and title of the exact Sony item the URL names, or None."""
    path = urlsplit(safe).path
    kind = "product" if "/product/" in path else "concept"
    identity = sony_product_id(safe) if kind == "product" else sony_concept_id(safe)
    if not identity:
        return None
    row = _store_row(scripts, kind, identity, path.split("/")[1])
    if row is None:
        return None
    invariant, name = row.get("invariantName"), row.get("name")
    # The English invariant title names the game. A Chinese display name may
    # differ from every alias; an English one must still agree.
    if invariant:
        if (_normalize_title(invariant) not in aliases
                or (isinstance(name, str) and not _HAN.search(name) and _normalize_title(name) not in aliases)):
            return None
    elif _normalize_title(name) not in aliases:
        return None
    if not _platform_matches(row.get("platforms"), platform):
        return None
    if kind == "product":
        edition = row.get("edition", {})
        if (row.get("type") not in {None, "GAME"} or row.get("topCategory") not in {None, "GAME"}
                or row.get("storeDisplayClassification") not in {None, "FULL_GAME"}
                or edition.get("type") not in {None, "STANDARD"} or edition.get("name") not in _STANDARD_EDITIONS):
            return None
    text = None
    for value in (row["descriptions"].get("LONG"), row.get("longDescription"), row.get("description"),
                  row.get("shortDescription")):
        text = _store_copy(value)
        if text:
            break
    if not text:
        return None
    result = {"text": text, "source_url": safe, "provider": "PlayStation",
              "official_title": invariant if invariant else name}
    chinese = store_chinese_name(name, invariant)
    if chinese:
        result["official_name_zh"] = chinese
    return result


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


def _platform_matches(value, expected):
    """A declared page platform cannot contradict the reviewed native version."""
    if not expected or value is None or value == []:
        return True  # Existing exact product-registry identity still applies; [] names no platform.
    names = value if isinstance(value, list) else [value]
    normalized = {"".join(char for char in unicodedata.normalize("NFKC", name).casefold() if char.isalnum())
                  for name in names if isinstance(name, str)}
    allowed = {"NS": {"nintendoswitch"}, "NS2": {"nintendoswitch2"},
               "PS5": {"playstation5", "ps5"}}.get(expected, set())
    return bool(normalized & allowed)


def parse_description(html, url, game_name, titles=None, *, platform=None):
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
    if urlsplit(safe).hostname == "store.playstation.com" and _store_row(
            parser.scripts, "product" if "/product/" in safe else "concept",
            sony_product_id(safe) if "/product/" in safe else sony_concept_id(safe),
            urlsplit(safe).path.split("/")[1]) is not None:
        # The exact item's own cache row decides. Its embedded summary
        # (JSON-LD) drops the paragraphing and the storefront-note cleanup.
        return _store_description(parser.scripts, safe, aliases, platform)
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
                if _normalize_title(title) in aliases:
                    if not _platform_matches(row.get("gamePlatform"), platform):
                        return None
                    if text:
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
            if not _platform_matches(row.get("platforms"), platform):
                return None
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
                result = parse_description(html, url, raw_game["name"], row.get("titles"), platform=row["platform"])
                if result:
                    return result
            if self.stopped:
                break
        return None

    def close(self):
        if self._owns_session:
            self.session.close()
