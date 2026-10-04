"""Bounded, serial IGDB client. Never include credentials in error messages."""
from __future__ import annotations

import time
from typing import Callable
import requests


API_ROOT = "https://api.igdb.com/v4/"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class CollectionError(RuntimeError):
    def __init__(self, reason: str, http_status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


class IGDBClient:
    def __init__(self, client_id: str, client_secret: str, *, session=None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 deadline_seconds: float = 480, request_interval: float = 0.4,
                 max_attempts: int = 3):
        if not client_id.strip() or not client_secret.strip():
            raise CollectionError("missing_credentials")
        if deadline_seconds <= 0 or request_interval < 1 / 3 or not 1 <= max_attempts <= 3:
            raise ValueError("invalid request bounds")
        self.client_id, self._client_secret = client_id, client_secret
        self.session = session or requests.Session()
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + deadline_seconds
        self.interval, self.max_attempts = request_interval, max_attempts
        self._next_request = clock()
        self._token = None
        self.request_count = 0
        self.retry_count = 0

    def _wait(self, seconds=0):
        wait = max(seconds, self._next_request - self.clock(), 0)
        if self.clock() + wait >= self.deadline:
            raise CollectionError("collection_deadline")
        if wait:
            self.sleep(wait)
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise CollectionError("collection_deadline")
        return min(20, remaining)

    def _post(self, url, **kwargs):
        for attempt in range(self.max_attempts):
            timeout = self._wait()
            self.request_count += 1
            try:
                response = self.session.post(url, timeout=timeout, allow_redirects=False, **kwargs)
            except requests.RequestException:
                raise CollectionError("network_error") from None
            finally:
                self._next_request = self.clock() + self.interval
            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except (ValueError, requests.RequestException):
                    raise CollectionError("invalid_json") from None
            if status not in (429, 500, 502, 503, 504) or attempt + 1 == self.max_attempts:
                raise CollectionError("http_error", status)
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = float(retry_after) if retry_after else 2 ** (attempt + 1)
            except (ValueError, TypeError):
                delay = 2 ** (attempt + 1)
            # Invalid/extreme server delays fail rather than silently overriding cooldown.
            if not 0 <= delay <= 30:
                raise CollectionError("retry_delay_out_of_bounds", status)
            self.retry_count += 1
            self._wait(max(delay, self.interval))
        raise CollectionError("retry_exhausted")

    def authenticate(self):
        data = self._post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        })
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise CollectionError("invalid_auth_response")
        self._token = data["access_token"]

    def query(self, endpoint: str, query: str):
        if endpoint not in {"platforms", "release_dates", "release_dates/count", "games"}:
            raise ValueError("unsupported endpoint")
        if self._token is None:
            self.authenticate()
        return self._post(API_ROOT + endpoint, data=query, headers={
            "Client-ID": self.client_id,
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
        })

    def verify_platforms(self):
        rows = self.query("platforms", "fields id,name; where id = (130,167,508); limit 3;")
        if not isinstance(rows, list) or len(rows) != 3:
            raise CollectionError("platform_identity_mismatch")
        actual = {row.get("id"): row.get("name") for row in rows if isinstance(row, dict)}
        if actual != {130: "Nintendo Switch", 167: "PlayStation 5", 508: "Nintendo Switch 2"}:
            raise CollectionError("platform_identity_mismatch")
        return rows


def paginated_releases(client, where: str, *, page_size=500, max_pages=100):
    """Count-checked ID cursor pagination; truncation or database drift is failure."""
    if not 1 <= page_size <= 500 or max_pages < 1:
        raise ValueError("invalid pagination bounds")
    def count():
        value = client.query("release_dates/count", f"where {where};")
        if not isinstance(value, dict) or type(value.get("count")) is not int or value["count"] < 0:
            raise CollectionError("invalid_count_response")
        return value["count"]
    expected = count()
    rows, last_id = [], 0
    for _ in range(max_pages):
        page = client.query("release_dates", (
            f"fields id,game,platform; where ({where}) & id > {last_id}; "
            f"sort id asc; limit {page_size};"
        ))
        if not isinstance(page, list) or len(page) > page_size:
            raise CollectionError("invalid_release_page")
        for row in page:
            if (not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= last_id
                    or type(row.get("game")) is not int or row["game"] <= 0
                    or row.get("platform") not in {130, 167, 508}):
                raise CollectionError("invalid_release_page")
            last_id = row["id"]
            rows.append(row)
        if len(page) < page_size:
            if len(rows) != expected or count() != expected:
                raise CollectionError("pagination_changed")
            return rows
    raise CollectionError("pagination_incomplete")
