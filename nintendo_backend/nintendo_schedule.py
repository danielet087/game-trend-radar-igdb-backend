"""Nintendo's Taiwan and Hong Kong release schedules (NS and NS2).

https://www.nintendo.com/tw/schedule and https://www.nintendo.com/hk/schedule
list the Switch and Switch 2 games of this month onward, by day, each with its
eShop title ID (in the thumbnail's path). The Taiwan page's day is the Taiwan
release day; games without a day sit under 發售日未定. One request a day each,
no redirects or retries.
"""
from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
import re

import requests

TW_URL = "https://www.nintendo.com/tw/schedule"
HK_URL = "https://www.nintendo.com/hk/schedule"
SOURCE_NAMES = {TW_URL: "任天堂台灣官網", HK_URL: "任天堂香港官網"}
MAX_BYTES = 3_000_000
TIMEOUT_SECONDS = 10
THEMES = {"switch": "NS", "switch2": "NS2"}
_DAY = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")
_NSUID = re.compile(r"/o5v89n4kg6h4/(7001\d{10})")


class _Schedule(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root, self.entries = False, []
        self._day, self._day_text, self._in_day, self._reading_day = None, "", False, False
        self._entry, self._depth, self._field = None, 0, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if attrs.get("id") == "local-schedule":
            self.root = True
        if self._entry is not None and tag == "div":
            self._depth += 1
        if tag == "div" and "local-schedule__daysArea" in classes:
            # A dated day, or free text (a month, a year) under 發售日未定.
            self._day = None
        if tag == "p" and "local-schedule__dayFull" in classes:
            self._in_day, self._reading_day, self._day_text = True, True, ""
        if tag == "span" and self._reading_day:
            # The weekday after the day.
            self._reading_day = False
        if tag == "div" and "local-schedule__listContent" in classes:
            self._entry = {"platform": THEMES.get(attrs.get("data-theme")), "date": self._day, "title": "",
                           "publisher": "", "nsuid": None, "formats": "", "href": None, "tags": []}
            self._depth = 1
            self.entries.append(self._entry)
            return
        if self._entry is None:
            return
        if tag == "a" and self._entry["href"] is None and isinstance(attrs.get("href"), str):
            self._entry["href"] = attrs["href"]
        style = attrs.get("style") or ""
        if (found := _NSUID.search(style)) and self._entry["nsuid"] is None:
            self._entry["nsuid"] = found[1]
        for name in classes:
            if name.startswith("nc3-a-tag--"):
                self._entry["tags"].append(name[len("nc3-a-tag--"):])
        if tag == "strong" and self._field == "title_box":
            self._field = "title"
        elif tag == "span" and self._field == "title_box":
            self._field = "publisher"
        elif tag == "div" and "local-schedule__listTitle" in classes:
            self._field = "title_box"
        elif tag == "p" and "local-schedule__labelText" in classes:
            self._field = "formats"

    def handle_data(self, data):
        if self._reading_day:
            self._day_text += data
        if self._entry is not None and self._field in {"title", "publisher", "formats"}:
            self._entry[self._field] += data

    def handle_endtag(self, tag):
        if tag == "p" and self._in_day:
            self._day = _parse_day(self._day_text)
            self._in_day = self._reading_day = False
        if self._entry is not None:
            if tag == "strong" and self._field == "title":
                self._field = "title_box"
            elif tag == "span" and self._field == "publisher":
                self._field = "title_box"
            elif tag == "p" and self._field == "formats":
                self._field = None
            if tag == "div":
                self._depth -= 1
                if self._field == "title_box":
                    self._field = None
                if self._depth <= 0:
                    self._entry, self._field = None, None


def _parse_day(text):
    found = _DAY.fullmatch(text.strip())
    if not found:
        return None
    try:
        return date(int(found[1]), int(found[2]), int(found[3])).isoformat()
    except ValueError:
        return None


def parse_schedule(html):
    """The schedule's NS and NS2 entries, or None when the page is not the schedule."""
    if not isinstance(html, str) or len(html) > MAX_BYTES:
        return None
    parser = _Schedule()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, RecursionError):
        return None
    if not parser.root or not parser.entries:
        return None
    result = []
    for entry in parser.entries:
        title = " ".join(entry["title"].split())
        if not entry["platform"] or entry["tags"][:1] != [{"NS": "switch", "NS2": "switch2"}[entry["platform"]]]:
            continue
        if not title or len(title) > 240:
            continue
        publisher = " ".join(entry["publisher"].split()).strip("[] ")
        result.append({"platform": entry["platform"], "title": title, "date": entry["date"],
                       "publisher": publisher[:240], "nsuid": entry["nsuid"],
                       "formats": " ".join(entry["formats"].split())[:120]})
    return result if result else None


class NintendoScheduleClient:
    """The Taiwan and Hong Kong schedules: one request each, no redirects or retries."""
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._owns_session = session is None
        if self._owns_session:
            self.session.trust_env = False
        self.request_count = 0

    def _fetch(self, url):
        self.request_count += 1
        try:
            response = self.session.get(url, timeout=TIMEOUT_SECONDS, allow_redirects=False)
            if response.status_code == 429:
                return {"status": "rate_limited", "entries": None}
            if response.status_code != 200 or len(response.content) > MAX_BYTES:
                return {"status": "unavailable", "entries": None}
            entries = parse_schedule(response.content.decode("utf-8"))
            return {"status": "ok" if entries is not None else "unavailable", "entries": entries}
        except (requests.RequestException, UnicodeError, ValueError):
            return {"status": "unavailable", "entries": None}

    def fetch(self):
        try:
            return {"taiwan": self._fetch(TW_URL), "hong_kong": self._fetch(HK_URL)}
        finally:
            if self._owns_session:
                self.session.close()
