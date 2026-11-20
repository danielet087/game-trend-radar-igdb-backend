from pathlib import Path

import pytest
import requests

from nintendo_backend import nintendo_schedule as S

FIXTURES = Path(__file__).parent / "fixtures"
TAIWAN = (FIXTURES / "nintendo_tw_schedule.html").read_text(encoding="utf-8")
HONG_KONG = (FIXTURES / "nintendo_hk_schedule.html").read_text(encoding="utf-8")


def test_the_real_schedule_markup_parses_into_switch_and_switch_2_days():
    entries = S.parse_schedule(TAIWAN)
    assert [(row["platform"], row["date"]) for row in entries] == [
        ("NS", "2026-11-05"), ("NS2", "2026-11-05"), ("NS2", "2026-11-12"), ("NS", "2026-11-12"),
        ("NS2", "2026-11-20"), ("NS2", None), ("NS", None)]
    ocarina = entries[1]
    assert (ocarina["title"], ocarina["nsuid"], ocarina["publisher"], ocarina["formats"]) == (
        "薩爾達傳說 時之笛", "70010000130795", "Nintendo", "盒裝版 / 下載版")
    # 發售日未定: a month is not a day, and each hardware is its own product.
    undated = [row for row in entries if row["date"] is None]
    assert {row["nsuid"] for row in undated} == {"70010000133760", "70010000134111"}
    assert S.parse_schedule(HONG_KONG)[3]["title"] == "暗喻幻想：ReFantazio（Metaphor: ReFantazio）"


@pytest.mark.parametrize("page", ["", "<html><body>維護中</body></html>", '<div id="local-schedule"></div>',
                                  TAIWAN.replace('data-theme="switch2"', 'data-theme="switch"', 1), None])
def test_a_page_that_is_not_the_schedule_or_whose_marks_disagree_gives_less(page):
    entries = S.parse_schedule(page)
    if page and "listContent" in page:
        # A Switch 2 tag under a Switch theme is skipped, the rest still read.
        assert len(entries) == 6
    else:
        assert entries is None


class Response:
    def __init__(self, status=200, text=TAIWAN):
        self.status_code, self.content = status, text.encode()


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize("response,status", [
    (Response(), "ok"), (Response(429), "rate_limited"), (Response(301), "unavailable"),
    (Response(500), "unavailable"), (Response(text="<html></html>"), "unavailable"),
    (requests.ConnectionError("down"), "unavailable"),
])
def test_each_schedule_is_one_request_without_redirects(response, status):
    session = Session([response, Response(text=HONG_KONG)])
    result = S.NintendoScheduleClient(session).fetch()
    assert result["taiwan"]["status"] == status and result["hong_kong"]["status"] == "ok"
    assert [url for url, _ in session.calls] == [S.TW_URL, S.HK_URL]
    assert all(kwargs == {"timeout": S.TIMEOUT_SECONDS, "allow_redirects": False} for _, kwargs in session.calls)
    assert (result["taiwan"]["entries"] is not None) is (status == "ok")
