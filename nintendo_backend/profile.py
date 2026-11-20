"""What the game page shows about a console game beyond its dates: IGDB's own
genres and themes, the companies behind it, its series, and its official
screenshots, artworks and trailers.

Only IGDB's identifiers are kept (image and YouTube IDs); the page builds the
image and video addresses itself, so no other host can appear. Names are
IGDB's own; the page decides how they read in Chinese. Every list is short:
the console file is read by every calendar page.
"""
from __future__ import annotations

import re

# IGDB image IDs ("sc6h3s", "ar1x0b") and YouTube video IDs.
_IMAGE_ID = re.compile(r"[a-z0-9]{1,40}")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# A theme that only exists for adult screening is not a feature of the game.
_HIDDEN_THEMES = {"erotic", "erotica"}
LIMITS = {"igdb_genres": 6, "igdb_themes": 6, "developers": 4, "publishers": 4, "franchises": 2,
          "screenshots": 8, "artworks": 3, "videos": 3}


def _name(value):
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text if 0 < len(text) <= 120 else None


def _names(rows, limit, *, key="name"):
    names = []
    for row in rows if isinstance(rows, list) else []:
        name = _name(row.get(key)) if isinstance(row, dict) else None
        if name and name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
    return names[:limit]


def _images(rows, limit):
    images = []
    for row in rows if isinstance(rows, list) else []:
        image = row.get("image_id") if isinstance(row, dict) else None
        if isinstance(image, str) and _IMAGE_ID.fullmatch(image) and image not in images:
            images.append(image)
    return images[:limit]


def _videos(rows, limit):
    videos, seen = [], set()
    for row in rows if isinstance(rows, list) else []:
        video = row.get("video_id") if isinstance(row, dict) else None
        if isinstance(video, str) and _VIDEO_ID.fullmatch(video) and video not in seen:
            seen.add(video)
            videos.append({"id": video, "name": _name(row.get("name"))})
    return videos[:limit]


def _companies(rows, role, limit):
    names = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get(role) is not True:
            continue
        company = row.get("company")
        name = _name(company.get("name")) if isinstance(company, dict) else None
        if name and name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
    return names[:limit]


def game_profile(game):
    """The published profile fields of one IGDB game record."""
    themes = [name for name in _names(game.get("themes"), LIMITS["igdb_themes"] + len(_HIDDEN_THEMES))
              if name.casefold() not in _HIDDEN_THEMES][:LIMITS["igdb_themes"]]
    involved = game.get("involved_companies")
    return {
        "igdb_genres": _names(game.get("genres"), LIMITS["igdb_genres"]),
        "igdb_themes": themes,
        "developers": _companies(involved, "developer", LIMITS["developers"]),
        "publishers": _companies(involved, "publisher", LIMITS["publishers"]),
        "franchises": _names(game.get("franchises"), LIMITS["franchises"]),
        "media": {"screenshots": _images(game.get("screenshots"), LIMITS["screenshots"]),
                  "artworks": _images(game.get("artworks"), LIMITS["artworks"]),
                  "videos": _videos(game.get("videos"), LIMITS["videos"])},
    }


def stored_raw(game):
    """The IGDB record as kept in the master: the profile lists cut to what is
    published (a big game lists dozens of screenshots), the rest untouched."""
    kept = dict(game)
    for field, limit in (("screenshots", LIMITS["screenshots"]), ("artworks", LIMITS["artworks"]),
                         ("videos", LIMITS["videos"]), ("involved_companies", 2 * (LIMITS["developers"] + LIMITS["publishers"]))):
        if isinstance(kept.get(field), list):
            kept[field] = kept[field][:limit]
    return kept
