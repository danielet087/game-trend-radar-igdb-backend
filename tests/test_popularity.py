from collections import deque
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from nintendo_backend import popularity
from nintendo_backend.catalog import build_documents
from nintendo_backend.collect import collect
from nintendo_backend.igdb import CollectionError
from scripts.publish import FILES, PublishError, validate_bundle

START = date(2026, 11, 19)
CHECKED = "2026-11-18T16:30:00Z"
NEXT_DAY = "2026-11-19T16:30:00Z"
NOW = datetime(2026, 11, 18, 16, 40, tzinfo=timezone.utc)


def release(day, platform=508):
    stamp = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    parsed = datetime.fromisoformat(day)
    return {"id": platform * 10000 + parsed.timetuple().tm_yday, "platform": {"id": platform}, "date": stamp,
            "category": 0, "y": parsed.year, "m": parsed.month, "d": parsed.day, "region": 8}


def game(id_, day="2026-12-03", platform=508, hypes=5, **changes):
    row = {"id": id_, "name": f"Game {id_}", "hypes": hypes, "platforms": [{"id": platform}], "category": 0,
           "release_dates": [release(day, platform)], "summary": "Lead your heroes through a tactical adventure.",
           "url": f"https://www.igdb.com/games/game-{id_}",
           "cover": {"url": "//images.igdb.com/igdb/image/upload/t_thumb/example.jpg"}}
    row.update(changes)
    return row


def values(*rows):
    return {id_: {"visits": visits, "want_to_play": want} for id_, visits, want in rows}


def ten_switch_games():
    """Ten NS2 games below the hypes bar; 9 and 10 lead on visits and Want to Play."""
    return [game(id_) for id_ in range(1, 11)], values(*[(id_, id_ / 1000, id_ / 2000) for id_ in range(1, 11)])


def test_the_top_fifth_of_switch_games_by_visits_or_want_to_play_is_admitted():
    raw, scores = ten_switch_games()
    master, public, status = build_documents(raw, start=START, checked_at=CHECKED, popscore=scores)
    shown = {row["id"]: row for row in public["games"]}
    assert set(shown) == {"igdb:9", "igdb:10"}
    admitted = master["games"]["igdb:10"]
    assert admitted["popularity_status"] == "below_threshold" and admitted["popularity_basis"] == "igdb_popscore"
    assert admitted["popscore_admitted_at"] == CHECKED and admitted["calendar_eligible"] is True
    assert admitted["eligibility_reasons"] == []
    assert admitted["popscore"] == {"visits": 0.01, "want_to_play": 0.005, "top_percent": 10}
    assert master["games"]["igdb:9"]["popscore"]["top_percent"] == 20
    assert master["games"]["igdb:8"]["popularity_basis"] is None
    assert "hypes_below_threshold" in master["games"]["igdb:8"]["eligibility_reasons"]
    summary = public["source"]["popscore"]
    assert summary["pool_size"] == 10 and summary["top_percent"] == 20 and summary["admitted_count"] == 2
    assert summary["cuts"]["visits"] == pytest.approx(0.0082)
    assert status["popscore_admitted_count"] == 2 and status["public_count"] == 2
    assert shown["igdb:10"]["popularity_basis"] == "igdb_popscore" and shown["igdb:10"]["popscore"]["top_percent"] == 10
    assert "popscore_admitted_at" not in shown["igdb:10"]


def test_hypes_still_admit_and_ps5_only_or_non_game_entries_never_use_popscore():
    raw, scores = ten_switch_games()
    raw += [game(20, platform=167, hypes=3), game(21, hypes=40), game(22, category=1), game(23, platform=167, hypes=31)]
    scores.update(values((20, 1.0, 1.0), (21, 0.02, 0.01), (22, 1.0, 1.0)))
    master, public, _ = build_documents(raw, start=START, checked_at=CHECKED, popscore=scores)
    rows = master["games"]
    assert rows["igdb:20"]["popscore"] is None and rows["igdb:20"]["calendar_eligible"] is False
    assert "not_standalone_game" in rows["igdb:22"]["eligibility_reasons"] and rows["igdb:22"]["calendar_eligible"] is False
    assert rows["igdb:21"]["popularity_basis"] == "hypes" and rows["igdb:23"]["popularity_basis"] == "hypes"
    # The hypes game is part of the NS/NS2 pool; the DLC is not.
    assert master["source"]["popscore"]["pool_size"] == 11
    assert {row["id"] for row in public["games"]} == {"igdb:9", "igdb:10", "igdb:21", "igdb:23"}


def test_an_admitted_game_stays_when_its_values_fall_and_old_dates_stay_out_of_the_pool():
    raw, scores = ten_switch_games()
    first, _, _ = build_documents(raw, start=START, checked_at=CHECKED, popscore=scores)
    turned = values(*[(id_, (11 - id_) / 1000, (11 - id_) / 2000) for id_ in range(1, 11)])
    later, public, _ = build_documents(raw, start=date(2026, 11, 20), checked_at=NEXT_DAY, previous=first, popscore=turned)
    assert {row["id"] for row in public["games"]} == {"igdb:1", "igdb:2", "igdb:9", "igdb:10"}
    assert later["games"]["igdb:10"]["popscore_admitted_at"] == CHECKED
    assert later["games"]["igdb:10"]["popscore"]["top_percent"] == 100
    assert later["games"]["igdb:1"]["popscore_admitted_at"] == NEXT_DAY
    early = game(30, day="2026-08-01")
    master, _, _ = build_documents([early, *raw], start=START, checked_at=CHECKED,
                                   popscore={**scores, **values((30, 1.0, 1.0))})
    assert master["source"]["popscore"]["pool_size"] == 10 and master["games"]["igdb:30"]["popularity_basis"] is None


def test_the_shown_rank_is_rounded_so_every_admitted_game_reads_top_20_or_better():
    assert [popularity.top_percent(rank, 791) for rank in (1, 8, 159, 160, 161, 791)] == [1, 1, 20, 20, 20, 100]
    raw = [game(id_) for id_ in range(1, 792)]
    scores = values(*[(id_, id_ / 100000, 0.0) for id_ in range(1, 792)])
    master, _, _ = build_documents(raw, start=START, checked_at=CHECKED, popscore=scores)
    admitted = [row for row in master["games"].values() if row["popularity_basis"] == "igdb_popscore"]
    assert len(admitted) == 159 and max(row["popscore"]["top_percent"] for row in admitted) == 20


def test_without_fresh_values_the_stored_ones_decide_so_the_publisher_rebuild_matches():
    raw, scores = ten_switch_games()
    master, public, status = build_documents(raw, start=START, checked_at=CHECKED, popscore=scores)
    rebuilt, rebuilt_public, _ = build_documents(raw, start=START, checked_at=CHECKED, previous=master)
    assert rebuilt["games"] == master["games"] and rebuilt_public["games"] == public["games"]
    validate_bundle(dict(zip(FILES, (master, public, status))), now=NOW)
    forged = deepcopy(master)
    forged["games"]["igdb:8"]["popscore"]["visits"] = 0.5
    with pytest.raises(PublishError):
        validate_bundle(dict(zip(FILES, (forged, public, status))), now=NOW)


class Pages:
    def __init__(self, pages):
        self.pages, self.calls = deque(pages), []

    def query(self, endpoint, query):
        self.calls.append((endpoint, query))
        return self.pages.popleft()


def test_switch_ids_and_fetch_read_both_types_in_pages_and_reject_bad_rows():
    dated_on_ns = game(3, platform=167, release_dates=[release("2026-12-03", 130)])
    assert popularity.switch_game_ids([game(1), game(2, platform=167), dated_on_ns, {"id": "x"}]) == [1, 3]
    client = Pages([[{"game_id": 1, "popularity_type": 1, "value": 0.002, "calculated_at": 2},
                     {"game_id": 1, "popularity_type": 1, "value": 0.003, "calculated_at": 1},  # older calculation
                     {"game_id": 1, "popularity_type": 2, "value": 0.001}],
                    [{"game_id": 150, "popularity_type": 2, "value": 0}]])
    assert popularity.fetch(client, range(150, 0, -1)) == {1: {"visits": 0.002, "want_to_play": 0.001},
                                                            150: {"visits": 0.0, "want_to_play": 0.0}}
    assert [endpoint for endpoint, _ in client.calls] == ["popularity_primitives"] * 2
    assert f"game_id = ({','.join(map(str, range(1, 101)))}) & popularity_type = (1,2)" in client.calls[0][1]
    assert f"game_id = ({','.join(map(str, range(101, 151)))})" in client.calls[1][1]
    for bad in ({"game_id": 999, "popularity_type": 1, "value": 1}, {"game_id": 1, "popularity_type": 9, "value": 1},
                {"game_id": 1, "popularity_type": 1, "value": -1}, {"game_id": 1, "popularity_type": 1, "value": True}):
        with pytest.raises(CollectionError, match="invalid_popscore_row"):
            popularity.fetch(Pages([[bad]]), [1])
    with pytest.raises(CollectionError, match="invalid_popscore_page"):
        popularity.fetch(Pages([[{"game_id": 1, "popularity_type": 1, "value": 0}] * 500]), [1])


def test_collect_admits_by_popscore_and_keeps_yesterdays_values_when_igdb_cannot_serve_them():
    raw, scores = ten_switch_games()
    rows = [{"game_id": id_, "popularity_type": type_, "value": scores[id_][name]}
            for id_ in scores for type_, name in popularity.TYPES.items()]

    class Client:
        request_count, retry_count = 4, 0

        def __init__(self, popscore):
            self.popscore = popscore
            self.results = deque([{"count": 10}, [{"id": 100 + row["id"], "game": row["id"], "platform": 508} for row in raw],
                                  {"count": 10}, raw])

        def verify_platforms(self):
            pass

        def query(self, endpoint, query):
            if endpoint == "popularity_primitives":
                if isinstance(self.popscore, Exception):
                    raise self.popscore
                return self.popscore
            return self.results.popleft()

    master, public, status = collect(Client(rows), start=START, checked_at=CHECKED)
    assert {row["id"] for row in public["games"]} == {"igdb:9", "igdb:10"}
    assert status["popscore_fetch"] == {"requested": 10, "games_with_values": 10, "status": "fresh"}
    later, later_public, later_status = collect(Client(CollectionError("http_error", 503)), start=date(2026, 11, 20),
                                                checked_at=NEXT_DAY, previous=master)
    assert {row["id"] for row in later_public["games"]} == {"igdb:9", "igdb:10"}
    assert later["games"]["igdb:9"]["popscore"] == master["games"]["igdb:9"]["popscore"]
    assert later_status["popscore_fetch"] == {"requested": 10, "games_with_values": 0,
                                              "status": "previous_values", "reason": "http_error"}
    # An empty answer for games that should have values is treated the same way.
    _, _, empty = collect(Client([]), start=START, checked_at=CHECKED)
    assert empty["popscore_fetch"]["status"] == "previous_values" and empty["popscore_fetch"]["reason"] == "empty_popscore"
