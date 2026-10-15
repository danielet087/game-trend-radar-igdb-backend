"""Official Chinese descriptions with original IGDB text as the fallback.

Only verified native product pages may replace an IGDB original. Failed
refreshes retain usable official Chinese, independently of English updates.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from opencc import OpenCC

from .official_descriptions import candidates, title_matches

PUBLIC_FIELDS = ("short_description", "short_description_en", "short_description_language",
                 "short_description_source", "description_source_url")
PRIVATE_FIELDS = ("description_official", "description_lookup")
_S2TW = OpenCC("s2tw")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")
MAX_TEXT = 20_000


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "br", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def clean_text(value):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_TEXT * 4:
        return ""
    parser = _PlainText()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, RecursionError):
        return ""
    text = unescape("".join(parser.parts)).replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text or len(text) > MAX_TEXT or any(ord(char) < 32 and char != "\n" for char in text):
        return ""
    return text


def _chinese(value):
    return bool(value and _HAN.search(value) and not _KANA.search(value)
                and len(_HAN.findall(value)) >= max(2, len(re.sub(r"\s", "", value)) * .1))


def original(game):
    """IGDB summary is preferred; an absent/empty summary falls back to storyline."""
    for field in ("summary", "storyline"):
        if text := clean_text(game.get(field)):
            language = "ja" if _KANA.search(text) else "zh-TW" if _chinese(text) else "en"
            return text, language, field
    return "", None, None


def _source_url(game):
    value = game.get("url")
    try:
        parsed = urlsplit(value) if isinstance(value, str) and len(value) <= 2000 else None
        if (parsed and parsed.scheme == "https" and parsed.hostname in {"igdb.com", "www.igdb.com"}
                and not parsed.username and not parsed.password and parsed.port is None
                and parsed.path.startswith("/games/")):
            return value
    except ValueError:
        pass
    return None


def _identity(state, game):
    return (isinstance(state, dict) and type(state.get("igdb_id")) is int
            and state["igdb_id"] == game.get("id")
            and state.get("name_en") == game.get("name", "").strip())


def _stamp(value):
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return checked if checked.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _cached_official(game, previous, sources):
    state = (previous or {}).get("description_official")
    if not _identity(state, game):
        return None
    source, text = clean_text(state.get("original_text")), clean_text(state.get("text"))
    bound = next((row for row in sources if row.get("url") == state.get("source_url")), None)
    if (not _chinese(source) or not _chinese(text) or source != state.get("original_text")
            or text != state.get("text") or text != _S2TW.convert(source)
            or not bound or not _stamp(state.get("checked_at"))
            or state.get("source_language", "zh-Hant") not in {"zh-Hant", "zh-Hans", "zh"}
            or state.get("provider") != ("PlayStation" if bound["platform"] == "PS5" else "Nintendo")
            or not title_matches(state.get("official_title"), bound.get("titles", []))
            or not isinstance(state.get("official_title"), str) or not state["official_title"].strip()):
        return None
    return deepcopy(state)


def describe(game, *, previous=None, official_candidates=None):
    text, language, _ = original(game)
    result = {"short_description": _S2TW.convert(text) if language == "zh-TW" else text,
              "short_description_en": text if language == "en" else "",
              "short_description_language": language, "short_description_source": "igdb_original" if text else None,
              "description_source_url": _source_url(game) if text else None}
    official = _cached_official(game, previous, official_candidates or [])
    if official:
        result["description_official"] = official
        result.update(short_description=official["text"], short_description_en=text if language == "en" else "",
                      short_description_language="zh-TW",
                      # Regional Nintendo TW/HK and Sony zh-hant locales prove
                      # Traditional Chinese. OpenCC normalizing a HK glyph is
                      # not evidence that the original was Simplified Chinese.
                      short_description_source="official_schinese_converted" if official.get("source_language") == "zh-Hans" else "official_zh_tw",
                      description_source_url=official["source_url"])
    lookup = (previous or {}).get("description_lookup")
    if (_identity(lookup, game) and _stamp(lookup.get("checked_at")) and isinstance(lookup.get("urls"), list)
            and all(isinstance(url, str) for url in lookup["urls"])
            and lookup.get("status") in {"confirmed", "unavailable", "failed", "no_verified_official_url"}):
        result["description_lookup"] = deepcopy(lookup)
    return result


def _is_due(row, urls, checked_at):
    lookup = row.get("description_lookup") or {}
    now, checked = _stamp(checked_at), _stamp(lookup.get("checked_at"))
    if now is None or checked is None or checked > now or lookup.get("urls") != urls:
        return True
    if row.get("description_official"):
        days = 7
    else:
        releases = [date.fromisoformat(r["date"]) for r in row.get("releases", []) if isinstance(r.get("date"), str)]
        days = 1 if any(-30 <= (day - now.date()).days <= 60 for day in releases) else 7
    return now - checked >= timedelta(days=days)


def _copy_to_public(row, public):
    for field in PUBLIC_FIELDS:
        public[field] = deepcopy(row.get(field))


def enrich_documents(master, catalog, *, official_client=None,
                     language_registry=None, release_registry=None, name_registry=None):
    stats = {"official_chinese_count": 0, "official_failure_count": 0}
    # Oldest official attempts go first so a bounded optional client rotates fairly.
    pending = sorted(catalog["games"], key=lambda row: (
        (master["games"][row["id"]].get("description_lookup") or {}).get("checked_at", ""), row["id"]))
    for public in pending:
        row, game = master["games"][public["id"]], master["games"][public["id"]]["raw"]
        sources = candidates(game, language_registry=language_registry, release_registry=release_registry,
                             name_registry=name_registry)
        urls = [source["url"] for source in sources]
        if official_client is not None and _is_due(row, urls, master["generated_at"]):
            before = getattr(official_client, "request_count", 0)
            try:
                found = official_client.lookup(game, sources) if sources else None
            except Exception:
                # This stage is optional; preserve existing verified text on errors.
                found = None
                stats["official_failure_count"] += 1
            after = getattr(official_client, "request_count", before)
            valid_official = False
            if isinstance(found, dict):
                source_text = clean_text(found.get("text"))
                official = {"igdb_id": game["id"], "name_en": game["name"].strip(),
                    "text": _S2TW.convert(source_text), "original_text": source_text,
                    "source_url": found.get("source_url"), "provider": found.get("provider"),
                    "official_title": found.get("official_title"), "source_language": "zh-Hant",
                    "checked_at": master["generated_at"]}
                if _cached_official(game, {"description_official": official}, sources):
                    row["description_official"] = official
                    valid_official = True
            # Do not mark a request-budget skip as checked; it gets priority next run.
            if not sources or after > before:
                row["description_lookup"] = {"igdb_id": game["id"], "name_en": game["name"].strip(),
                    "checked_at": master["generated_at"], "urls": urls,
                    "status": "confirmed" if valid_official else "unavailable" if sources else "no_verified_official_url"}
        # Re-evaluate official sources independently of IGDB English changes.
        row.update(describe(game, previous=row, official_candidates=sources))
        if row.get("description_official"):
            stats["official_chinese_count"] += 1
        _copy_to_public(row, public)
    stats["english_fallback_count"] = sum(row.get("short_description_language") == "en" for row in catalog["games"])
    stats["missing_count"] = sum(not row.get("short_description") for row in catalog["games"])
    stats["official_request_count"] = getattr(official_client, "request_count", 0)
    stats["official_failure_count"] += getattr(official_client, "failure_count", 0)
    stats["official_rate_limited"] = getattr(official_client, "rate_limited", False)
    return stats
