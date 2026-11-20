from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from nintendo_backend import official_store as S
from nintendo_backend.catalog import normalize_game
from nintendo_backend.languages import unknown_support

START, END = date(2026, 10, 4), date(2027, 10, 4)
CHECKED = "2026-10-04T03:00:00Z"
PRODUCT = "HP0001-PPSA12345_00-BASEGAME00000000"
REQUEST = "https://store.playstation.com/en-tw/product/" + PRODUCT
PUBLIC = "https://store.playstation.com/zh-hant-tw/product/" + PRODUCT


def game(**changes):
    stamp = int(datetime(2027, 3, 1, tzinfo=timezone.utc).timestamp())
    row = {"id": 123, "name": "A game", "hypes": 30, "category": 0, "summary": "An adventure.",
           "platforms": [{"id": 167}], "websites": [{"url": REQUEST.replace("en-tw", "en-us")}],
           "release_dates": [{"id": 9, "platform": {"id": 167}, "category": 0, "date": stamp, "region": 8}]}
    row.update(changes)
    return row


def evidence(**changes):
    row = {"product_id": PRODUCT, "concept_id": None, "official_title": "A game", "region": "taiwan",
           "source_url": PUBLIC, "supported_languages": ["zh-Hant", "en"], "complete": True,
           "evidence_type": "official_product_languages", "release_time_utc": "2027-03-01T16:00:00Z",
           "date": "2027-03-02"}
    row.update(changes)
    return row


def state(confirmed=CHECKED, checked=CHECKED, **changes):
    entry = {"outcome": "confirmed", "urls": [REQUEST], "checked_at": checked, "attempted_at": checked,
             "request_url": REQUEST, "evidence": evidence(), "confirmed_at": confirmed}
    entry.update(changes)
    return {"igdb_id": 123, "name_en": "A game", "PS5": entry}


def report(outcome="confirmed", found=None):
    found = evidence() if found is None and outcome == "confirmed" else found
    return {"taiwan": {"outcome": outcome, "urls": [REQUEST], "url": REQUEST if outcome == "confirmed" else None,
                       "evidence": found}}


@pytest.mark.parametrize("changes", [
    {"region": "hong_kong"},
    {"source_url": PUBLIC.replace("zh-hant-tw", "zh-hant-hk")},
    {"source_url": PUBLIC.replace("zh-hant-tw", "en-tw")},
    {"product_id": "HP0001-PPSA12345_00-DIFFERENT0000000"},
    {"date": "2027-03-01"},
    {"release_time_utc": "2027-03-01T16:00:00"},
    {"supported_languages": ["zh-Hant", "xx"]},
    {"supported_languages": ["en", "en"]},
    {"complete": True, "evidence_type": "official_chinese_unspecified", "supported_languages": ["zh"]},
    {"complete": "yes"},
    {"official_title": ""},
    {"concept_id": "10000001"},
])
def test_invalid_taiwan_evidence_is_refused(changes):
    assert S.valid_evidence(evidence(**changes)) is None


def test_a_concept_needs_its_release_instant():
    concept = "https://store.playstation.com/zh-hant-tw/concept/10000001"
    row = evidence(source_url=concept, product_id=None, concept_id="10000001", supported_languages=[],
                   complete=False, evidence_type="official_concept_release_time")
    assert S.valid_evidence(row)["concept_id"] == "10000001"
    assert S.valid_evidence({**row, "release_time_utc": None, "date": None}) is None
    # Without a day, a product still proves its languages.
    assert S.valid_evidence(evidence(release_time_utc=None, date=None))["date"] is None


def test_state_is_bound_to_the_game_and_to_an_item_igdb_still_links():
    assert S.validated_state(game(), state(), CHECKED)["PS5"]["evidence"]["product_id"] == PRODUCT
    assert S.validated_state(game(name="A game Deluxe"), state(), CHECKED) is None
    assert S.validated_state(game(id=124), state(), CHECKED) is None
    unlinked = S.validated_state(game(websites=[]), state(), CHECKED)
    assert unlinked["PS5"]["evidence"] is None and unlinked["PS5"]["checked_at"] == CHECKED
    future = state(checked="2026-10-05T00:00:00Z")
    assert S.validated_state(game(), future, CHECKED) is None
    assert S.validated_state(game(), state(outcome="maybe"), CHECKED) is None


def test_evidence_stays_current_for_14_days_or_for_good_once_confirmed_after_release():
    current = S.validated_state(game(), state(confirmed="2026-09-20T03:00:00Z", checked="2026-09-20T03:00:00Z"), CHECKED)
    assert S.current_evidence(current, CHECKED)[0] is not None
    stale = S.validated_state(game(), state(confirmed="2026-09-19T02:00:00Z", checked="2026-09-19T02:00:00Z"), CHECKED)
    assert S.current_evidence(stale, CHECKED) == (None, None)
    released = state(confirmed="2027-03-02T02:00:00Z", checked="2027-03-02T02:00:00Z")
    later = "2027-06-01T00:00:00Z"
    assert S.current_evidence(S.validated_state(game(), released, later), later)[1] == "2027-03-02T02:00:00Z"


def test_taiwan_release_keeps_igdb_day_as_the_corrected_source():
    stamp = int(datetime(2027, 3, 1, tzinfo=timezone.utc).timestamp())
    igdb = {"source_date": "2027-03-01", "source_timestamp": stamp, "source_region": "worldwide",
            "timestamp_taipei_date": "2027-03-01"}
    row = S.taiwan_release(evidence(), CHECKED, igdb)
    assert (row["date"], row["source_date"], row["source_timestamp"], row["timestamp_taipei_date"]) == (
        "2027-03-02", "2027-03-01", stamp, "2027-03-01")
    assert row["official_verified_at"] == CHECKED and row["official_source_name"] == "PlayStation Store 台灣"
    alone = S.taiwan_release(evidence(), CHECKED)
    assert (alone["source_date"], alone["source_timestamp"], alone["source_region"]) == ("2027-03-02", None, "taiwan")
    assert S.taiwan_release(evidence(date=None, release_time_utc=None), CHECKED) is None


def test_store_languages_fill_ps5_unless_a_reviewed_complete_list_says_more():
    languages = {"PS5": unknown_support()}
    assert S.apply_languages(languages, evidence(), CHECKED)
    assert languages["PS5"]["languages"]["tchinese"] is True and languages["PS5"]["product_id"] == PRODUCT
    reviewed = {"PS5": {**unknown_support(), "complete": True, "status": "confirmed"}}
    assert not S.apply_languages(reviewed, evidence(complete=False, evidence_type="official_product_languages"), CHECKED)
    assert S.apply_languages(reviewed, evidence(), CHECKED)
    assert not S.apply_languages({"NS": unknown_support()}, evidence(), CHECKED)


@pytest.mark.parametrize("checked,release,today,due", [
    (None, date(2027, 3, 2), date(2026, 10, 4), True),                  # never answered
    ("2026-10-02T03:00:00Z", date(2027, 3, 2), date(2026, 10, 4), False),  # far: every 3 days
    ("2026-10-01T03:00:00Z", date(2027, 3, 2), date(2026, 10, 4), True),
    ("2027-02-20T03:00:00Z", date(2027, 3, 2), date(2027, 2, 20), False),  # last 14 days: daily
    ("2027-02-20T03:00:00Z", date(2027, 3, 2), date(2027, 2, 21), True),
    ("2027-03-01T03:00:00Z", date(2027, 3, 2), date(2027, 3, 5), True),    # released since: one final check
    ("2027-03-02T03:00:00Z", date(2027, 3, 2), date(2027, 3, 5), False),   # checked on the day: done
    ("2026-10-02T03:00:00Z", None, date(2026, 10, 4), False),              # no day: every 3 days
    ("2026-10-01T03:00:00Z", None, date(2026, 10, 4), True),
])
def test_cadence(checked, release, today, due):
    assert S.is_due(state(checked=checked or CHECKED, **({"checked_at": None} if checked is None else {})),
                    game(), release, today) is due


def test_a_changed_igdb_link_is_checked_at_once():
    other = game(websites=[{"url": "https://store.playstation.com/en-us/concept/10000001"}])
    assert S.is_due(state(checked="2026-10-03T03:00:00Z"), other, date(2027, 3, 2), date(2026, 10, 4))


def test_merge_answers_replace_evidence_and_errors_keep_it():
    old = S.validated_state(game(), state(confirmed="2026-10-01T03:00:00Z", checked="2026-10-01T03:00:00Z"), CHECKED)
    confirmed = S.merge(old, game(), report(), CHECKED)["PS5"]
    assert confirmed["confirmed_at"] == confirmed["checked_at"] == CHECKED and confirmed["evidence"]["date"] == "2027-03-02"
    for outcome in ("unconfirmed", "no_url"):
        gone = S.merge(old, game(), report(outcome), CHECKED)["PS5"]
        assert gone["evidence"] is None and gone["checked_at"] == CHECKED
    for outcome in ("unavailable", "rate_limited", "budget_exhausted"):
        kept = S.merge(old, game(), report(outcome), CHECKED)["PS5"]
        assert kept["evidence"] == old["PS5"]["evidence"] and kept["checked_at"] == "2026-10-01T03:00:00Z"
        assert kept["attempted_at"] == CHECKED
    assert S.merge(old, game(), None, CHECKED) == old
    assert S.merge(None, game(), None, CHECKED) is None
    # A confirmed answer whose data does not hold up is not kept either.
    assert S.merge(old, game(), report(found=evidence(region="hong_kong")), CHECKED)["PS5"]["evidence"] is None


def test_normalized_game_puts_the_taiwan_store_day_first_inside_the_window_only():
    row = normalize_game(game(), START, END, CHECKED, store_state=state())
    assert row["releases"][0]["date"] == "2027-03-02" and row["releases"][0]["source"] == "official_registry"
    assert row["release_records"][0]["source"] == "IGDB"  # The audit keeps IGDB's own record.
    assert row["playstation_url"] == PUBLIC and row["official_store"]["PS5"]["evidence"]["product_id"] == PRODUCT
    outside = state(evidence=evidence(date="2027-10-05", release_time_utc="2027-10-04T16:00:00Z"))
    late = normalize_game(game(), START, END, CHECKED, store_state=outside)
    assert late["releases"] == [] and late["calendar_eligible"] is False
    # Once stale, IGDB's day is back.
    old = "2026-09-01T00:00:00Z"
    stale = normalize_game(game(), START, END, CHECKED, store_state=state(confirmed=old, checked=old))
    assert stale["releases"][0]["source"] == "IGDB" and stale["playstation_url"] is None


def test_without_ps5_a_store_state_is_dropped_and_the_master_copy_is_what_counts():
    switch = game(platforms=[{"id": 508}], release_dates=[{"id": 9, "platform": {"id": 508}, "category": 0,
                  "date": int(datetime(2027, 3, 1, tzinfo=timezone.utc).timestamp()), "region": 8}])
    assert "official_store" not in normalize_game(switch, START, END, CHECKED, store_state=state())
    previous = {"official_store": state()}
    assert normalize_game(game(), START, END, CHECKED, previous=previous)["releases"][0]["date"] == "2027-03-02"
    # An explicit state for this run wins over the stored one.
    fresh = normalize_game(game(), START, END, CHECKED, previous=previous,
                           store_state=S.merge(state(), game(), report("unconfirmed"), CHECKED))
    assert fresh["releases"][0]["source"] == "IGDB"


def test_a_conflict_igdb_cannot_settle_is_settled_by_the_taiwan_store():
    stamp = int(datetime(2027, 3, 1, tzinfo=timezone.utc).timestamp())
    conflicting = game(release_dates=[{"id": 9, "platform": {"id": 167}, "category": 0, "date": stamp, "region": 8},
                                      {"id": 10, "platform": {"id": 167}, "category": 0, "date": stamp + 86400, "region": 8}])
    assert normalize_game(conflicting, START, END, CHECKED)["releases"] == []
    row = normalize_game(conflicting, START, END, CHECKED, store_state=state())
    assert row["releases"][0]["date"] == "2027-03-02" and row["releases"][0]["source_date"] == "2027-03-02"
