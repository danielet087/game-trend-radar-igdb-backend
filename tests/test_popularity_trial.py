from datetime import date, datetime, timezone

from scripts.popularity_trial import build_rows, exact_releases, percentile, report, spearman

TODAY = date(2026, 11, 19)


def stamp(day):
    return int(datetime.fromisoformat(day).replace(hour=3, tzinfo=timezone.utc).timestamp())


def game(id_, day, platform=508, hypes=None, kind="Main Game", **changes):
    row = {"id": id_, "name": f"Game {id_}", "hypes": hypes, "game_type": {"type": kind},
           "release_dates": [{"platform": {"id": platform}, "date": stamp(day), "date_format": {"format": "YYYYMMMMDD"}}],
           "involved_companies": [{"publisher": True, "company": {"name": "Nintendo"}},
                                  {"publisher": False, "company": {"name": "Developer"}}]}
    row.update(changes)
    return row


def data(games, primitives):
    return {"today": TODAY.isoformat(), "start": "2026-08-21", "end": "2027-11-19", "request_count": 9,
            "games": games, "primitives": primitives,
            "types": [{"id": 1, "name": "Visits", "popularity_source": 121},
                      {"id": 2, "name": "Want to Play", "popularity_source": 121}]}


def test_exact_days_only_inside_the_window_and_not_cancelled():
    raw = game(1, "2026-12-03", release_dates=[
        {"platform": {"id": 508}, "date": stamp("2026-12-03"), "date_format": {"format": "YYYYMMMMDD"}},
        {"platform": {"id": 130}, "date": stamp("2026-12-01"), "date_format": {"format": "YYYYMMMM"}},
        {"platform": {"id": 167}, "date": stamp("2026-12-05"), "category": 0},
        {"platform": {"id": 167}, "date": stamp("2026-12-04"), "date_format": {"format": "YYYYMMMMDD"},
         "status": {"name": "Cancelled"}},
        {"platform": {"id": 6}, "date": stamp("2026-12-02"), "date_format": {"format": "YYYYMMMMDD"}},
        {"platform": {"id": 508}, "date": stamp("2028-01-01"), "date_format": {"format": "YYYYMMMMDD"}}])
    assert exact_releases(raw, date(2026, 8, 21), date(2027, 11, 19)) == {"NS2": "2026-12-03", "PS5": "2026-12-05"}


def test_rows_keep_switch_standalone_games_with_their_popularity_and_publishers():
    games = [game(1, "2026-12-03", hypes=40), game(2, "2026-12-10", kind="DLC"),
             game(3, "2026-12-04", platform=167, hypes=31), game(4, "2025-01-01")]
    rows, names = build_rows(data(games, [{"game_id": 1, "popularity_type": 2, "value": 0.5},
                                         {"game_id": 1, "popularity_type": 9, "value": 3}]))
    assert [row["igdb_id"] for row in rows] == [1, 3, 2]
    first = rows[0]
    assert first["switch"] and first["standalone"] and first["upcoming"] and first["hypes_qualified"]
    assert first["popularity"] == {"2": 0.5, "9": 3.0} and first["publishers"] == ["Nintendo"]
    assert names[9] == "type 9"
    assert not rows[2]["standalone"] and not rows[1]["switch"]


def test_report_ranks_by_the_best_covered_type_and_counts_new_admissions():
    games = [game(id_, f"2026-12-{id_ + 1:02d}", hypes=40 if id_ == 1 else 3) for id_ in range(1, 21)]
    primitives = [{"game_id": id_, "popularity_type": 2, "value": id_ / 100} for id_ in range(1, 21)]
    text, rows = report(data(games, primitives))
    assert "NS/NS2 standalone games: 20 (20 still to come)" in text
    assert "## Top 60 NS/NS2 games by Want to Play" in text
    assert "| 1 | Game 20 | NS2 | 2026-12-21 | 3 | 0.2 | Nintendo |" in text
    assert "| top 10% | 0.181 | 2 | 2 | 2 |" in text
    assert len(rows) == 20


def test_percentile_and_rank_correlation():
    assert percentile([1, 2, 3, 4, 5], 50) == 3 and percentile([], 90) == 0
    assert spearman([(1, 10), (2, 20), (3, 30)]) == 1.0
    assert spearman([(1, 1)]) is None
