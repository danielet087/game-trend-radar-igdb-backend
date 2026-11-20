from datetime import date

from nintendo_backend.catalog import build_documents, normalize_game
from nintendo_backend.profile import LIMITS, game_profile, stored_raw
from tests.test_catalog import CHECKED, END, START, game


def profile_game(**changes):
    fields = dict(
        genres=[{"id": 12, "name": "Role-playing (RPG)"}, {"id": 31, "name": "Adventure"},
                {"id": 99, "name": "adventure"}, {"id": 98, "name": ""}],
        themes=[{"id": 1, "name": "Fantasy"}, {"id": 42, "name": "Erotic"}, {"id": 38, "name": "Open world"}],
        involved_companies=[
            {"id": 1, "company": {"id": 70, "name": "Nintendo EPD"}, "developer": True, "publisher": False},
            {"id": 2, "company": {"id": 71, "name": "Nintendo"}, "developer": False, "publisher": True},
            {"id": 3, "company": {"id": 72, "name": "Monolith Soft"}, "developer": True, "publisher": False},
            {"id": 4, "company": {"id": 73, "name": "Porting House"}, "developer": False, "publisher": False},
            {"id": 5, "company": "not an object", "developer": True},
        ],
        franchises=[{"id": 9, "name": "Xenoblade Chronicles"}],
        screenshots=[{"id": i, "image_id": f"sc{i:04d}", "width": 1920, "height": 1080} for i in range(20)]
        + [{"id": 90, "image_id": "../evil", "width": 1, "height": 1}, {"id": 91, "image_id": "sc0001"}],
        artworks=[{"id": 1, "image_id": "ar1x0b", "width": 3840, "height": 2160},
                  {"id": 2, "image_id": "AR-UPPER"}],
        videos=[{"id": 1, "video_id": "dQw4w9WgXcQ", "name": "Announcement Trailer"},
                {"id": 2, "video_id": "javascript:1", "name": "bad"},
                {"id": 3, "video_id": "abcdefghijk", "name": None}],
    )
    return game(**{**fields, **changes})


def test_profile_keeps_igdb_names_companies_series_and_safe_media_ids():
    profile = game_profile(profile_game())
    assert profile["igdb_genres"] == ["Role-playing (RPG)", "Adventure"]
    # The adult-screening theme is not a feature of the game.
    assert profile["igdb_themes"] == ["Fantasy", "Open world"]
    assert profile["developers"] == ["Nintendo EPD", "Monolith Soft"]
    assert profile["publishers"] == ["Nintendo"]
    assert profile["franchises"] == ["Xenoblade Chronicles"]
    # Only the IDs: the page builds the IGDB image and YouTube addresses itself.
    assert profile["media"]["screenshots"] == [f"sc{i:04d}" for i in range(LIMITS["screenshots"])]
    assert profile["media"]["artworks"] == ["ar1x0b"]
    assert profile["media"]["videos"] == [{"id": "dQw4w9WgXcQ", "name": "Announcement Trailer"},
                                          {"id": "abcdefghijk", "name": None}]


def test_a_game_without_a_profile_publishes_empty_lists():
    profile = game_profile(game())
    assert profile == {"igdb_genres": [], "igdb_themes": [], "developers": [], "publishers": [], "franchises": [],
                       "media": {"screenshots": [], "artworks": [], "videos": []}}
    assert game_profile(game(genres="RPG", screenshots={"image_id": "sc1"}))["igdb_genres"] == []


def test_the_public_row_carries_the_profile_and_the_master_keeps_a_bounded_record():
    # (An erotic theme would hold the game for content review.)
    raw = profile_game(themes=[{"id": 1, "name": "Fantasy"}])
    master, public, _ = build_documents([raw], start=START, checked_at=CHECKED)
    row = public["games"][0]
    assert row["developers"] == ["Nintendo EPD", "Monolith Soft"]
    assert row["media"]["videos"][0]["id"] == "dQw4w9WgXcQ"
    stored = master["games"]["igdb:123"]["raw"]
    assert len(stored["screenshots"]) == LIMITS["screenshots"]
    assert stored["videos"] == raw["videos"][:LIMITS["videos"]]
    assert stored_raw(raw)["name"] == raw["name"]
    # A retained game rebuilt from its stored record keeps the same profile.
    again = normalize_game(stored, START, END, CHECKED)
    assert again["developers"] == row["developers"] and again["media"] == row["media"]
    assert date.fromisoformat(row["releases"][0]["date"])
