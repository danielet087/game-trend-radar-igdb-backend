"""Only unavailable historical, nonpublic evidence can survive a short lookup."""
from collections import deque
from copy import deepcopy
from datetime import date

import pytest

from nintendo_backend.catalog import build_documents, normalize_game, validate_retained_candidate
from nintendo_backend.collect import GameLookupError, collect, lookup_games
from nintendo_backend.igdb import CollectionError


START = date(2026, 10, 16)
CHECKED = "2026-10-16T08:00:00Z"
OLD_CHECKED = "2026-10-15T00:32:16Z"


def raw_game(id_=2, *, hypes=None):
    return {"id": id_, "name": "Game " + str(id_), "hypes": hypes,
            "platforms": [{"id": 508}], "category": 0, "summary": "A tactical adventure.",
            "release_dates": [{"id": id_ + 10, "platform": {"id": 508}, "category": 0,
                               "y": 2027, "m": 3, "d": 1, "region": 8}]}


def retained():
    row = normalize_game(raw_game(), START, date(2027, 10, 16), OLD_CHECKED)
    row["legacy_evidence"] = {"never_remeasure": True}
    row["raw"]["legacy_observation"] = "preserve exactly"
    return row


class Client:
    request_count, retry_count = 4, 0

    def __init__(self, results):
        self.results, self.calls = deque(results), []

    def verify_platforms(self):
        self.calls.append(("verify_platforms", ""))

    def query(self, endpoint, query):
        self.calls.append((endpoint, query))
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def collector_client(games):
    return Client([{"count": 1}, [{"id": 11, "game": 1, "platform": 508}],
                   {"count": 1}, games, [], []])


def test_unavailable_nonpublic_history_is_preserved_and_fresh_public_data_continues():
    old = retained()
    previous = {"games": {"igdb:2": old}}
    before = deepcopy(previous)
    api = collector_client([raw_game(1, hypes=30)])
    master, public, refresh = collect(api, start=START, checked_at=CHECKED, previous=previous)
    assert previous == before
    assert master["games"]["igdb:2"] == old
    assert master["games"]["igdb:2"] is not old
    assert master["games"]["igdb:1"]["checked_at"] == CHECKED
    assert [row["igdb_id"] for row in public["games"]] == [1]
    for source in (master["source"], public["source"], refresh["source"]):
        assert source["complete"] is False
        assert source["ledger_complete"] is True
        assert source["lookup_complete"] is False
        assert source["public_complete"] is True
        assert source["game_lookup"] == {
            "initial_missing_count": 1, "recovered_count": 0,
            "recovery_requests": 2, "max_recovery_requests": 20,
            "unavailable_ids": [2], "unavailable": [{"igdb_id": 2,
                "reason": "missing_after_bounded_recovery", "last_checked_at": OLD_CHECKED,
                "request_attempts": 3}]}
    assert refresh["complete"] is False and refresh["status"] == "partial"
    assert refresh["ledger_complete"] is True and refresh["public_complete"] is True
    assert refresh["lookup_complete"] is False
    assert refresh["pending_count"] == refresh["unavailable_count"] == 1
    assert "id = (1,2)" in api.calls[4][1]
    assert all("id = (2)" in query for _, query in api.calls[5:])


def test_next_collection_still_requests_retained_id_and_recovery_clears_partial_state():
    previous = {"games": {"igdb:2": retained()}}
    partial, _, _ = collect(collector_client([raw_game(1, hypes=30)]),
                            start=START, checked_at=CHECKED, previous=previous)
    api = Client([{"count": 1}, [{"id": 11, "game": 1, "platform": 508}], {"count": 1},
                  [raw_game(1, hypes=30)], [], [raw_game(2, hypes=30)]])
    master, public, refresh = collect(api, start=START, checked_at="2026-10-17T08:00:00Z", previous=partial)
    assert "id = (1,2)" in api.calls[4][1]
    assert master["games"]["igdb:2"]["checked_at"] == "2026-10-17T08:00:00Z"
    assert master["games"]["igdb:2"]["first_observed_at"] == OLD_CHECKED
    assert {row["igdb_id"] for row in public["games"]} == {1, 2}
    assert refresh["complete"] is True and refresh["status"] == "complete"
    assert refresh["pending_count"] == refresh["unavailable_count"] == 0
    assert refresh["source"]["game_lookup"]["recovered_count"] == 1
    assert refresh["source"]["game_lookup"]["unavailable_ids"] == []


def test_retained_rows_never_pass_normalization_or_optional_enrichment(monkeypatch):
    old = retained()
    original = normalize_game

    def only_fresh(raw, *args, **kwargs):
        assert raw["id"] != 2, "retained observations must not be normalized again"
        return original(raw, *args, **kwargs)

    monkeypatch.setattr("nintendo_backend.catalog.normalize_game", only_fresh)

    class NameClient:
        request_count = failure_count = 0
        rate_limited = False

        def lookup(self, raw):
            assert raw["id"] == 1
            self.request_count += 1
            return None

    names = NameClient()
    fresh = raw_game(1, hypes=30)
    fresh["websites"] = [{"url": "https://store.steampowered.com/app/789/"}]
    master, _, status = collect(collector_client([fresh]),
        start=START, checked_at=CHECKED, previous={"games": {"igdb:2": old}}, name_client=names)
    assert names.request_count == status["name_enrichment"]["steam_request_count"] == 1
    assert master["games"]["igdb:2"] == old


def test_unexpected_enrichment_mutation_of_retained_row_aborts_collection(monkeypatch):
    def mutate(master, *args, **kwargs):
        master["games"]["igdb:2"]["checked_at"] = CHECKED
        return {}

    monkeypatch.setattr("nintendo_backend.collect.enrich_descriptions", mutate)
    with pytest.raises(CollectionError, match="retained_candidate_changed"):
        collect(collector_client([raw_game(1, hypes=30)]), start=START, checked_at=CHECKED,
                previous={"games": {"igdb:2": retained()}})


@pytest.mark.parametrize("change", [
    lambda row: row.update(calendar_eligible=True),
    lambda row: row.pop("raw"),
    lambda row: row.pop("platform_history"),
    lambda row: row.update(checked_at="2026-10-17T00:00:00Z"),
    lambda row: row.update(checked_at="2026-10-15T00:32:16"),
    lambda row: row["raw"].update(id=99),
    lambda row: row.update(hypes=30),
    lambda row: row.update(eligibility_reasons=[]),
    lambda row: row.update(content_screening={}),
    lambda row: row.update(platforms=None),
])
def test_incomplete_public_corrupt_or_future_prior_rows_cannot_be_quarantined(change):
    old = retained()
    change(old)
    with pytest.raises(GameLookupError, match="game_lookup_incomplete"):
        lookup_games(Client([[], [], []]), [2], retained_candidates={"igdb:2": old}, checked_at=CHECKED)


def test_new_missing_candidate_still_blocks_publication_with_valid_retained_neighbor():
    with pytest.raises(GameLookupError, match="game_lookup_incomplete") as error:
        lookup_games(Client([[], [], [], []]), [2, 3],
                     retained_candidates={"igdb:2": retained()}, checked_at=CHECKED)
    assert error.value.lookup_details["missing_ids"] == [2, 3]


@pytest.mark.parametrize("response,reason", [
    (CollectionError("http_error", 429), "http_error"),
    (CollectionError("collection_deadline"), "collection_deadline"),
    ([{"id": 2}, {"id": 2}], "game_lookup_incomplete"),
    ([{"id": 99}], "game_lookup_incomplete"),
    ({"malformed": "response"}, "game_lookup_incomplete"),
])
def test_retained_candidate_never_masks_http_or_malformed_recovery(response, reason):
    with pytest.raises(GameLookupError, match=reason):
        lookup_games(Client([[], response]), [2],
                     retained_candidates={"igdb:2": retained()}, checked_at=CHECKED)


def test_retained_candidate_cannot_bypass_exhausted_recovery_budget():
    with pytest.raises(GameLookupError, match="game_lookup_incomplete"):
        lookup_games(Client([[], []]), [2], max_recovery_requests=1,
                     retained_candidates={"igdb:2": retained()}, checked_at=CHECKED)


def test_retained_unavailable_ids_are_unique_sorted_numeric_with_per_id_evidence():
    old = {"igdb:" + str(id_): normalize_game(raw_game(id_), START, date(2027, 10, 16), OLD_CHECKED)
           for id_ in (12, 3)}
    rows, report = lookup_games(Client([[], [], [], []]), [3, 12],
                                retained_candidates=old, checked_at=CHECKED)
    assert rows == []
    assert report["unavailable_ids"] == [3, 12]
    assert [row["igdb_id"] for row in report["unavailable"]] == [3, 12]
    assert all(row["request_attempts"] == 3 for row in report["unavailable"])


@pytest.mark.parametrize("ids,raw", [([2, 2], []), ([True], []), ([2], [raw_game()]), ([9], [])])
def test_build_documents_cannot_invent_duplicate_or_overlap_retained_identities(ids, raw):
    with pytest.raises(CollectionError, match="invalid_retained_candidate"):
        build_documents(raw, start=START, checked_at=CHECKED,
                        previous={"games": {"igdb:2": retained()}}, retained_unavailable_ids=ids)


def test_retained_validation_is_pure_and_returns_original_evidence():
    old = retained()
    before = deepcopy(old)
    assert validate_retained_candidate(old, 2, CHECKED) is old
    assert old == before
