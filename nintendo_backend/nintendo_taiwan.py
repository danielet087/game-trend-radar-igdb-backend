"""Taiwan release days of Switch games from Nintendo Taiwan's software list.

https://www.nintendo.com/tw/software/switch lists the Switch games on sale and
coming, about three months ahead, each with its Taiwan day. One request a day.
An entry belongs to a game only by an exact title (the game's IGDB name or an
alias, or its Chinese name; an entry "中文 (English)" matches either part), on
the Switch hardware, unclaimed by another game and within 60 days of IGDB's
Switch day. Nintendo Taiwan has no Switch 2 list, so NS2 days stay IGDB's.
"""
from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
import re
import unicodedata

import requests

LIST_URL = "https://www.nintendo.com/tw/software/switch"
SOURCE_NAME = "任天堂台灣官網"
MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 10
MAX_DAYS_FROM_IGDB = 60
SECTIONS = {"section-now-on-sale": "on_sale", "section-will-be-released": "upcoming"}
_DAY = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?!\d)")
_NSUID = re.compile(r"/o5v89n4kg6h4/(7001\d{10})")
_PAIR = re.compile(r"(.+?)\s*[（(]([^()（）]+)[)）]\s*")


def normalize(value):
    if not isinstance(value, str):
        return ""
    # The marks go first: NFKC turns ™ into "TM".
    value = unicodedata.normalize("NFKC", value.replace("™", "").replace("®", "").replace("©", ""))
    return "".join(char for char in value.casefold() if char.isalnum())


def title_keys(title):
    """An entry's title, and both parts of a "中文 (English)" title."""
    keys = {normalize(title)}
    pair = _PAIR.fullmatch(title.strip()) if isinstance(title, str) else None
    if pair:
        keys |= {normalize(pair[1]), normalize(pair[2])}
    return keys - {""}


def game_keys(raw, row):
    """The names a game is known by: IGDB's name and aliases, and its Chinese names."""
    names = [raw.get("name")] + [item.get("name") for item in raw.get("alternative_names") or [] if isinstance(item, dict)]
    names += [item.get("name") for item in raw.get("game_localizations") or [] if isinstance(item, dict)]
    names += [row.get(field) for field in ("name_zh_tw", "display_name", "name_en")]
    return {normalize(name) for name in names if isinstance(name, str)} - {""}


class _List(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections, self.units = set(), []
        self._section, self._unit, self._field, self._depth = None, None, None, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "section" and attrs.get("id") in SECTIONS:
            self._section = SECTIONS[attrs["id"]]
            self.sections.add(self._section)
        if self._unit is not None and tag == "div":
            self._depth += 1
        if "ncmn-softUnit" in classes and "ncmn-softUnit--list" in classes and self._section:
            self._unit = {"section": self._section, "title": "", "hardware": "", "release": "", "publisher": "",
                          "format": "", "href": None, "nsuid": None, "chinese": False}
            self._depth = 1
            self.units.append(self._unit)
            return
        if self._unit is None:
            return
        if tag == "a" and isinstance(attrs.get("href"), str) and self._unit["href"] is None:
            self._unit["href"] = attrs["href"]
        style = attrs.get("style") or ""
        if (nsuid := _NSUID.search(style)) and self._unit["nsuid"] is None:
            self._unit["nsuid"] = nsuid[1]
        for name, field in (("name", "title"), ("hardware", "hardware"), ("release", "release"),
                            ("publisher", "publisher"), ("softcategory", "format")):
            if "ncmn-softUnit__" + name in classes:
                self._field = field
        if tag == "img" and (attrs.get("alt") or "").strip() == "對應中文" and "/support_cn" in (attrs.get("src") or ""):
            self._unit["chinese"] = True

    def handle_data(self, data):
        if self._unit is not None and self._field:
            self._unit[self._field] += data

    def handle_endtag(self, tag):
        if tag == "div" and self._unit is not None:
            self._field = None
            self._depth -= 1
            if self._depth <= 0:
                self._unit = None


def release_day(text):
    """The earliest exact Taiwan day of an entry ("2024.10.17（盒裝版）/ 2025.9.9（下載版）"); None for a month."""
    days = []
    for year, month, day in _DAY.findall(text or ""):
        try:
            days.append(date(int(year), int(month), int(day)))
        except ValueError:
            return None
    return min(days) if days else None


def parse_list(html):
    """The list's entries, or None when the page is not the software list."""
    if not isinstance(html, str) or len(html) > MAX_BYTES:
        return None
    parser = _List()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, RecursionError):
        return None
    if parser.sections != set(SECTIONS.values()) or not any(unit["section"] == "upcoming" for unit in parser.units):
        return None
    result = []
    for unit in parser.units:
        title = " ".join(unit["title"].split())
        if not title or len(title) > 240:
            continue
        day = release_day(unit["release"])
        result.append({"section": unit["section"], "title": title, "hardware": " ".join(unit["hardware"].split()),
                       "date": day.isoformat() if day else None, "publisher": " ".join(unit["publisher"].split())[:240],
                       "nsuid": unit["nsuid"], "chinese": unit["chinese"]})
    return result


def match(entries, games):
    """Each game's single Switch entry: {key: entry}. Ambiguity on either side matches nothing.

    games: {key: (raw, row, igdb_switch_day or None)}
    """
    switch = [entry for entry in entries if entry["hardware"] == "Nintendo Switch" and entry["date"]]
    claims = {}
    for key, (raw, row, igdb_day) in games.items():
        names = game_keys(raw, row)
        found = [entry for entry in switch if title_keys(entry["title"]) & names]
        if len(found) != 1:
            continue
        day = date.fromisoformat(found[0]["date"])
        if igdb_day is not None and abs((day - igdb_day).days) > MAX_DAYS_FROM_IGDB:
            continue
        claims.setdefault(id(found[0]), []).append((key, found[0]))
    return {key: entry for rows in claims.values() if len(rows) == 1 for key, entry in rows}


class NintendoTaiwanClient:
    """One request, no redirects or retries."""
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._owns_session = session is None
        if self._owns_session:
            self.session.trust_env = False
        self.request_count = 0

    def fetch(self):
        self.request_count += 1
        try:
            response = self.session.get(LIST_URL, timeout=TIMEOUT_SECONDS, allow_redirects=False)
            if response.status_code == 429:
                return {"status": "rate_limited", "entries": None}
            if response.status_code != 200 or len(response.content) > MAX_BYTES:
                return {"status": "unavailable", "entries": None}
            entries = parse_list(response.content.decode("utf-8"))
            return {"status": "ok" if entries is not None else "unavailable", "entries": entries}
        except (requests.RequestException, UnicodeError, ValueError):
            return {"status": "unavailable", "entries": None}
        finally:
            if self._owns_session:
                self.session.close()
