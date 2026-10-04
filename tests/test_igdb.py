from collections import deque

import pytest
import requests

from nintendo_backend.igdb import CollectionError, IGDBClient, TOKEN_URL, paginated_releases


class Response:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class Session:
    def __init__(self, responses):
        self.responses, self.calls = deque(responses), []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.responses.popleft()
        if isinstance(value, Exception):
            raise value
        return value


class Clock:
    def __init__(self):
        self.time, self.waits = 0, []

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.time += seconds


def client(responses, **kwargs):
    session, clock = Session(responses), Clock()
    result = IGDBClient("client-id", "private-secret", session=session, clock=clock.now, sleep=clock.sleep, **kwargs)
    return result, session, clock


def test_auth_secret_is_form_body_never_url_and_no_redirect():
    api, session, clock = client([Response({"access_token": "token"}), Response([])])
    assert api.query("games", "fields id;") == []
    url, kwargs = session.calls[0]
    assert url == TOKEN_URL and "private-secret" not in url
    assert kwargs["data"]["client_secret"] == "private-secret"
    assert kwargs["allow_redirects"] is False
    assert session.calls[1][1]["headers"]["Authorization"] == "Bearer token"
    assert clock.waits == [0.4]


def test_429_bounded_retry_honors_server_delay_and_rate_limit():
    api, session, clock = client([Response({"access_token": "token"}), Response({}, 429, {"Retry-After": "3"}), Response([])])
    assert api.query("games", "fields id;") == []
    assert api.retry_count == 1 and api.request_count == 3
    assert clock.waits == [0.4, 3.0]


def test_exhausted_rate_limit_has_sanitized_error_and_three_attempts():
    api, session, _ = client([Response({"access_token": "token"}), *[Response({"error": "private-secret"}, 429) for _ in range(3)]])
    with pytest.raises(CollectionError) as raised:
        api.query("games", "fields id;")
    assert str(raised.value) == "http_error"
    assert raised.value.http_status == 429
    assert len(session.calls) == 4


def test_network_exception_body_not_exposed():
    api, _, _ = client([requests.ConnectionError("private-secret token URL")])
    with pytest.raises(CollectionError) as raised:
        api.authenticate()
    assert str(raised.value) == "network_error"


def test_huge_retry_after_fails_without_sleeping_or_requesting_again():
    api, session, clock = client([Response({"access_token": "token"}), Response({}, 429, {"Retry-After": "3600"})])
    with pytest.raises(CollectionError, match="retry_delay_out_of_bounds"):
        api.query("games", "fields id;")
    assert len(session.calls) == 2
    assert clock.waits == [0.4]


def test_deadline_rejects_cooldown_that_does_not_fit():
    api, session, clock = client([Response({"access_token": "token"}), Response({}, 429, {"Retry-After": "5"})], deadline_seconds=4)
    with pytest.raises(CollectionError, match="collection_deadline"):
        api.query("games", "fields id;")
    assert len(session.calls) == 2 and clock.time == 0.4


def test_platform_ids_runtime_names_must_match():
    api, _, _ = client([Response({"access_token": "token"}), Response([{"id": 130, "name": "Nintendo Switch"}, {"id": 508, "name": "Wrong console"}])])
    with pytest.raises(CollectionError, match="platform_identity_mismatch"):
        api.verify_platforms()


def test_platform_identity_verification_includes_native_ps5():
    rows = [{"id": 130, "name": "Nintendo Switch"}, {"id": 167, "name": "PlayStation 5"}, {"id": 508, "name": "Nintendo Switch 2"}]
    api, session, _ = client([Response({"access_token": "token"}), Response(rows)])
    assert api.verify_platforms() == rows
    assert "(130,167,508)" in session.calls[-1][1]["data"]


class QueryClient:
    def __init__(self, results):
        self.results, self.calls = deque(results), []

    def query(self, endpoint, body):
        self.calls.append((endpoint, body))
        return self.results.popleft()


def record(id_, game=1):
    return {"id": id_, "game": game, "platform": 508}


def test_cursor_pagination_complete_and_rechecked_count():
    api = QueryClient([{"count": 3}, [record(2), record(4)], [record(8)], {"count": 3}])
    rows = paginated_releases(api, "platform = (130,508)", page_size=2)
    assert [row["id"] for row in rows] == [2, 4, 8]
    assert "id > 4" in api.calls[2][1]
    assert api.calls[-1][0] == "release_dates/count"


@pytest.mark.parametrize("results,reason", [
    ([{"count": 3}, [record(2), record(4)], []], "pagination_changed"),
    ([{"count": 1}, [record(2)], {"count": 2}], "pagination_changed"),
    ([{"count": 2}, [record(2), record(2)]], "invalid_release_page"),
])
def test_incomplete_drifting_or_duplicate_pages_fail(results, reason):
    with pytest.raises(CollectionError, match=reason):
        paginated_releases(QueryClient(results), "platform=(130,508)", page_size=2)


def test_page_cap_never_misreported_as_complete():
    with pytest.raises(CollectionError, match="pagination_incomplete"):
        paginated_releases(QueryClient([{"count": 2}, [record(2), record(4)]]), "platform=(130,508)", page_size=2, max_pages=1)
