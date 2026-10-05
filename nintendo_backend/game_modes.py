"""Typed IGDB game modes and platform-specific multiplayer evidence.

These fields describe play modes, not eligibility or a platform guarantee.
Unknown data stays unknown; genres, keywords and free text are never evidence.
"""
from __future__ import annotations

from .igdb import CollectionError

BOOLEAN_FIELDS = ("campaigncoop", "dropin", "lancoop", "offlinecoop", "onlinecoop",
                  "splitscreen", "splitscreenonline")
COUNT_FIELDS = ("offlinecoopmax", "offlinemax", "onlinecoopmax", "onlinemax")


def _rows(game, field):
    value = game.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise CollectionError("invalid_" + field + "_metadata")
    return value


def game_modes(game):
    """Preserve named modes, including unfamiliar modes, without classifying them."""
    result = []
    for row in _rows(game, "game_modes"):
        if type(row) is int:
            row = {"id": row}
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
            raise CollectionError("invalid_game_modes_metadata")
        name = row.get("name")
        if name is not None and not isinstance(name, str):
            raise CollectionError("invalid_game_modes_metadata")
        normalized_name = (name.strip() or None) if name is not None else None
        result.append({"id": row["id"], "name": normalized_name})
    return result


def multiplayer_modes(game):
    """Keep each evidence record bound to its own optional IGDB platform ID."""
    result = []
    for row in _rows(game, "multiplayer_modes"):
        if not isinstance(row, dict):
            raise CollectionError("invalid_multiplayer_modes_metadata")
        normalized = {}
        platform = row.get("platform")
        if platform is not None:
            platform_id = platform if type(platform) is int else platform.get("id") if isinstance(platform, dict) else None
            if type(platform_id) is not int or platform_id <= 0:
                raise CollectionError("invalid_multiplayer_modes_platform")
            normalized["platform"] = {"id": platform_id}
        for field in BOOLEAN_FIELDS:
            value = row.get(field)
            if value is not None:
                if type(value) is not bool:
                    raise CollectionError("invalid_multiplayer_modes_boolean")
                normalized[field] = value
        for field in COUNT_FIELDS:
            value = row.get(field)
            if value is not None:
                if type(value) is not int or value < 0:
                    raise CollectionError("invalid_multiplayer_modes_count")
                normalized[field] = value
        if normalized:
            result.append(normalized)
    return result
