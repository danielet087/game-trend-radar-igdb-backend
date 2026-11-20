from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import requests

from nintendo_backend import nintendo_taiwan as N
from nintendo_backend import official_store as S
from nintendo_backend.catalog import normalize_game
from nintendo_backend.collect import collect

FIXTURE = (Path(__file__).parent / "fixtures" / "nintendo_tw_switch.html").read_text(encoding="utf-8")
START, END = date(2026, 11, 20), date(2027, 11, 20)
CHECKED = "2026-11-20T00:30:00Z"


def entry(title="雷頓教授與蒸氣新世界", day="2026-12-09", **changes):
    row = {"section": "upcoming", "title": title, "hardware": "Nintendo Switch", "date": day,
           "publisher": "LEVEL5", "nsuid": "70010000136811", "chinese": False}
    row.update(changes)
    return row


def raw_game(**changes):
    stamp = int(datetime(2026, 12, 10, tzinfo=timezone.utc).timestamp())
    row = {"id": 236665, "name": "Professor Layton and the New World of Steam", "hypes": 40, "category": 0,
           "summary": "A mystery adventure.", "platforms": [{"id": 130}, {"id": 508}],
           "game_localizations": [{"name": "雷頓教授與蒸氣新世界", "region": {"identifier": "zh-TW"}}],
           "release_dates": [{"id": 1, "platform": {"id": 130}, "category": 0, "date": stamp, "region": 8},
                             {"id": 2, "platform": {"id": 508}, "category": 0, "date": stamp, "region": 8}]}
    row.update(changes)
    return row


def state(day="2026-12-09", confirmed=CHECKED, **changes):
    evidence = {"region": "taiwan", "source_url": N.LIST_URL, "title": "雷頓教授與蒸氣新世界", "date": day,
                "nsuid": "70010000136811", "chinese": False, "section": "upcoming"}
    ns = {"outcome": "confirmed", "checked_at": confirmed, "attempted_at": confirmed,
          "evidence": evidence, "confirmed_at": confirmed}
    ns.update(changes)
    return {"igdb_id": 236665, "name_en": "Professor Layton and the New World of Steam", "NS": ns}


def test_the_real_list_markup_parses_into_dated_switch_entries():
    entries = N.parse_list(FIXTURE)
    assert len(entries) == 9
    assert [row["section"] for row in entries].count("on_sale") == 3
    layton = next(row for row in entries if row["title"] == "雷頓教授與蒸氣新世界")
    assert (layton["date"], layton["hardware"], layton["nsuid"], layton["publisher"]) == (
        "2026-12-09", "Nintendo Switch", "70010000136811", "LEVEL5")
    metroid = entries[0]
    assert metroid["chinese"] is True and metroid["date"] == "2025-12-04"
    # A month is not a day.
    assert next(row for row in entries if "Haunted Paws" in row["title"])["date"] is None


def test_separate_box_and_download_days_give_the_earliest():
    assert N.release_day("2024.10.17（盒裝版）/ 2025.9.9（下載版）") == date(2024, 10, 17)
    assert N.release_day("2027.5") is None and N.release_day("2026.2.30") is None


@pytest.mark.parametrize("page", [
    "<html><body>維護中</body></html>",
    FIXTURE.replace('id="section-will-be-released"', 'id="elsewhere"'),
    FIXTURE.split('id="section-will-be-released"')[0] + 'id="section-will-be-released"></section></body></html>',
    None,
])
def test_a_page_that_is_not_the_list_is_no_answer(page):
    assert N.parse_list(page) is None


def test_titles_match_exactly_in_either_language_of_a_paired_title():
    assert N.title_keys("傑登工業: 療癒系自動化解謎遊戲 (JADEN INDUSTRIES: Cozy Automation Puzzler)") >= {
        N.normalize("JADEN INDUSTRIES: Cozy Automation Puzzler"), N.normalize("傑登工業: 療癒系自動化解謎遊戲")}
    assert N.normalize("GRIME II™") == N.normalize("Grime II")


def test_match_requires_one_switch_entry_one_game_and_a_nearby_day():
    games = {"igdb:236665": (raw_game(), {}, date(2026, 12, 10))}
    assert N.match([entry()], games)["igdb:236665"]["date"] == "2026-12-09"
    # The English name, an alias or a Chinese name are all the same game's.
    english = {"igdb:1": ({"id": 1, "name": "Cat Me If You Can"}, {}, None)}
    assert N.match([entry("Cat Me If You Can", "2026-12-10")], english)
    # Ambiguity, another platform, a month-only day or a far day match nothing.
    assert N.match([entry(), entry(nsuid="70010000136812")], games) == {}
    assert N.match([entry(hardware="Nintendo Switch 2")], games) == {}
    assert N.match([entry(day=None)], games) == {}
    assert N.match([entry(day="2027-03-01")], games) == {}
    twins = {**games, "igdb:9": (raw_game(id=9, name="Professor Layton Remake"), {}, None)}
    assert N.match([entry()], twins) == {}


class Response:
    def __init__(self, status=200, text=FIXTURE):
        self.status_code, self.content = status, text.encode()


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.parametrize("response,status", [
    (Response(), "ok"), (Response(429), "rate_limited"), (Response(308), "unavailable"),
    (Response(500), "unavailable"), (Response(text="<html></html>"), "unavailable"),
    (requests.Timeout("slow"), "unavailable"),
])
def test_one_request_without_redirects(response, status):
    session = Session(response)
    result = N.NintendoTaiwanClient(session).fetch()
    assert result["status"] == status and len(session.calls) == 1
    assert session.calls[0] == (N.LIST_URL, {"timeout": N.TIMEOUT_SECONDS, "allow_redirects": False})
    assert (result["entries"] is not None) is (status == "ok")


def test_merge_confirms_drops_or_keeps_by_the_answer():
    game = raw_game()
    confirmed = S.merge_switch(None, game, "ok", entry(), CHECKED)["NS"]
    assert confirmed["evidence"]["date"] == "2026-12-09" and confirmed["confirmed_at"] == CHECKED
    # Gone from the list before the release day: the day no longer stands.
    gone = S.merge_switch(state(), game, "ok", None, "2026-11-21T00:30:00Z")["NS"]
    assert gone["evidence"] is None and gone["outcome"] == "unconfirmed"
    # Confirmed on the release day: kept after the list leaves the game out.
    final = state(confirmed="2026-12-09T00:30:00Z")
    kept = S.merge_switch(final, game, "ok", None, "2027-02-01T00:30:00Z")["NS"]
    assert kept["evidence"]["date"] == "2026-12-09" and kept["confirmed_at"] == "2026-12-09T00:30:00Z"
    # A list that could not be read keeps the earlier day.
    unread = S.merge_switch(state(), game, "unavailable", None, "2026-11-21T00:30:00Z")["NS"]
    assert unread["evidence"]["date"] == "2026-12-09" and unread["checked_at"] == CHECKED
    assert unread["attempted_at"] == "2026-11-21T00:30:00Z"
    # Nothing known and nothing found: nothing to keep.
    assert S.merge_switch(None, game, "ok", None, CHECKED) is None
    # A PS5 entry beside it is kept.
    both = {**state(), "PS5": {"outcome": "no_url"}}
    assert S.merge_switch(both, game, "ok", entry(), CHECKED)["PS5"] == {"outcome": "no_url"}


@pytest.mark.parametrize("changes", [
    {"source_url": "https://www.nintendo.com/hk/software/switch"}, {"region": "hong_kong"},
    {"date": "2026-12-9"}, {"nsuid": "7001"}, {"chinese": "yes"}, {"section": "later"}, {"title": ""},
])
def test_invalid_switch_evidence_is_refused(changes):
    evidence = deepcopy(state()["NS"]["evidence"])
    evidence.update(changes)
    assert S.valid_switch_evidence(evidence) is None


def test_the_taiwan_switch_day_comes_before_igdb_and_leaves_switch_2_alone():
    row = normalize_game(raw_game(), START, END, CHECKED, store_state=state())
    ns, ns2 = row["releases"]
    assert (ns["platform"], ns["date"], ns["source"], ns["region"]) == ("NS", "2026-12-09", "official_registry", "taiwan")
    assert ns["official_source_name"] == "任天堂台灣官網" and ns["official_release_time_utc"] is None
    assert ns["source_date"] == "2026-12-10" and ns["official_product_id"] is None
    assert (ns2["platform"], ns2["date"], ns2["source"]) == ("NS2", "2026-12-10", "IGDB")
    # Stale (14 days unconfirmed before release) gives IGDB's day back.
    old = "2026-11-01T00:30:00Z"
    assert normalize_game(raw_game(), START, END, CHECKED, store_state=state(confirmed=old))["releases"][0]["source"] == "IGDB"
    # A game without the Switch version keeps no Switch state.
    switch2 = raw_game(platforms=[{"id": 508}], release_dates=raw_game()["release_dates"][1:])
    assert "official_store" not in normalize_game(switch2, START, END, CHECKED, store_state=state())


def test_the_chinese_mark_proves_chinese_only_without_a_reviewed_list():
    marked = state()
    marked["NS"]["evidence"]["chinese"] = True
    row = normalize_game(raw_game(), START, END, CHECKED, store_state=marked)
    support = row["platform_language_support"]["NS"]
    assert support["status"] == "partial" and support["evidence_type"] == "official_chinese_unspecified"
    assert support["languages"] == {"tchinese": None, "schinese": None, "english": None, "chinese": True}
    assert support["source_url"] == N.LIST_URL and support["region"] == "taiwan"
    assert row["platform_language_support"]["NS2"]["status"] == "unknown"


class API:
    request_count, retry_count = 4, 0

    def __init__(self, results):
        self.results = list(results)

    def verify_platforms(self):
        pass

    def query(self, endpoint, query):
        return [] if endpoint == "popularity_primitives" else self.results.pop(0)


class ListClient:
    def __init__(self, result):
        self.result = result

    def fetch(self):
        return self.result


def test_collect_reads_the_list_once_and_publishes_the_taiwan_switch_day():
    api = API([{"count": 1}, [{"id": 1, "game": 236665, "platform": 130}], {"count": 1}, [raw_game()]])
    master, public, status = collect(api, start=START, checked_at=CHECKED,
                                     nintendo_taiwan_client=ListClient({"status": "ok", "entries": [entry()]}))
    [row] = public["games"]
    assert row["releases"][0]["date"] == "2026-12-09" and row["releases"][0]["source"] == "official_registry"
    assert master["games"]["igdb:236665"]["official_store"]["NS"]["confirmed_at"] == CHECKED
    summary = status["nintendo_taiwan"]
    assert (summary["status"], summary["entry_count"], summary["switch_game_count"], summary["matched_count"],
            summary["adopted_date_count"]) == ("ok", 1, 1, 1, 1)
    # The next day without the list keeps it; a list without the game drops it.
    later = "2026-11-21T00:30:00Z"
    for result, source in (({"status": "unavailable", "entries": None}, "official_registry"),
                           ({"status": "ok", "entries": [entry("Another game")]}, "IGDB")):
        api = API([{"count": 1}, [{"id": 1, "game": 236665, "platform": 130}], {"count": 1}, [raw_game()]])
        _, next_public, _ = collect(api, start=date(2026, 11, 21), checked_at=later, previous=master,
                                    nintendo_taiwan_client=ListClient(result))
        assert next_public["games"][0]["releases"][0]["source"] == source
