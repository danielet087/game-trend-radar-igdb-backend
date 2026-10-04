from collections import deque
from copy import deepcopy
from datetime import date

import pytest
import requests

from nintendo_backend.catalog import build_documents, normalize_game
from nintendo_backend.chinese_names import NAME_FIELDS, SteamNameClient, enrich_documents, names, steam_identity, validate_registry
from nintendo_backend.igdb import CollectionError

START, END = date(2026, 10, 4), date(2027, 10, 4)
CHECKED = "2026-10-04T03:00:00Z"


def game(**changes):
    row = {"id": 123, "name": "Example Remastered", "hypes": 30, "category": 0,
           "platforms": [{"id": 508}], "summary": "A tactical adventure.",
           "release_dates": [{"id": 456, "platform": {"id": 508}, "category": 0,
                              "y": 2027, "m": 3, "d": 1, "region": 8}],
           "websites": [{"url": "https://store.steampowered.com/app/789/Example_Remastered/"}]}
    row.update(changes)
    return row


def registry(**changes):
    row = {"igdb_id": 123, "name_en": "Example Remastered", "name_zh_tw": "官方遊戲 重製版",
           "source": "Publisher official", "url": "https://publisher.example/zh-tw/game"}
    row.update(changes)
    return validate_registry({"schema_version": 1, "games": {"igdb:123": row}})


class Response:
    def __init__(self, title=None, *, appid=789, status=200, type_="game", success=True):
        self.status_code = status
        self.payload = {str(789): {"success": success, "data": {"steam_appid": appid, "type": type_, "name": title}}}

    def json(self):
        return self.payload


class Session:
    def __init__(self, results):
        self.results, self.calls = deque(results), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def client(results, **kwargs):
    session = Session(results)
    return SteamNameClient(session=session, clock=lambda: 0, sleep=lambda _: None, **kwargs), session


def test_curated_names_override_igdb_and_require_exact_game_edition():
    raw = game(game_localizations=[{"name": "資料庫遊戲", "region": {"identifier": "zh-TW"}}])
    assert names(raw, registry=registry())["display_name"] == "官方遊戲 重製版"
    assert names(raw, registry=registry(name_en="Example"))["display_name"] == "資料庫遊戲"
    assert names(game(id=321), registry=registry())["name_zh_tw"] is None


@pytest.mark.parametrize("change", [{"igdb_id": True}, {"igdb_id": 124}, {"name_zh_tw": "English"},
                                     {"url": "http://publisher.example/game"}, {"url": "https://user:pass@publisher.example/game"},
                                     {"url": "https://[invalid"}, {"source": ""}])
def test_invalid_registry_entries_fail_before_publication(change):
    with pytest.raises(CollectionError, match="invalid_chinese_name_registry"):
        registry(**change)
    with pytest.raises(CollectionError, match="invalid_chinese_name_registry"):
        validate_registry({"schema_version": True, "games": {}})


def test_alternative_names_require_chinese_label_and_never_infer_japanese_han_names():
    raw = game(alternative_names=[{"name": "遊戲", "comment": "Japanese title"},
                                 {"name": "遊戲", "comment": "Unofficial Chinese fan translation"},
                                 {"name": "正式遊戲", "comment": "Traditional Chinese title"}])
    assert names(raw)["display_name"] == "正式遊戲"
    assert names(game(alternative_names=[{"name": "遊戲", "comment": "Japanese title"}]))["name_zh_tw"] is None
    simplified = names(game(alternative_names=[{"name": "游戏测试", "comment": "Chinese title"}]))
    assert simplified["display_name"] == "遊戲測試"
    assert simplified["name_evidence"]["converted"] is True


def test_traditional_names_beat_simplified_and_verified_cache_survives_missing_localization():
    old = names(game(game_localizations=[{"name": "舊有繁中名", "region": {"identifier": "zh-Hant"}}]))
    raw = game(game_localizations=[{"name": "新的简中名", "region": {"identifier": "zh-CN"}}])
    assert names(raw, previous=old)["display_name"] == "舊有繁中名"
    assert names(game(), previous=old)["display_name"] == "舊有繁中名"
    assert names(game(name="Example"), previous=old)["name_zh_tw"] is None
    forged = deepcopy(old)
    forged["name_evidence"]["igdb_id"] = 124
    assert names(game(), previous=forged)["name_zh_tw"] is None


def test_english_localized_response_is_not_a_chinese_name():
    assert names(game(game_localizations=[{"name": "Example Remastered", "region": {"identifier": "zh-TW"}}]))["name_zh_tw"] is None


def test_steam_identity_uses_service_name_or_exact_product_url_and_rejects_conflicts():
    assert steam_identity(game()) == (789, "IGDB Steam product URL")
    external = {"uid": "789", "game": 123, "external_game_source": {"name": "Steam"}}
    assert steam_identity(game(external_games=[external], websites=[])) == (789, "IGDB external_games Steam")
    assert steam_identity(game(external_games=[{**external, "uid": "888"}])) is None
    assert steam_identity(game(external_games=[{**external, "game": 124}], websites=[])) is None
    assert steam_identity(game(external_games=[{"uid": "789", "category": 1}], websites=[])) is None
    assert steam_identity(game(websites=[{"url": "https://store.steampowered.com.evil.example/app/789/"}])) is None


@pytest.mark.parametrize("first", [Response("English title"), Response("遊戲", appid=888),
                                    Response("遊戲", type_="dlc"), Response("遊戲", success=False),
                                    Response("日本の遊戲")])
def test_steam_fallback_validates_identity_type_language_and_converts_simplified(first):
    api, session = client([first, Response("游戏测试")])
    result = api.lookup(game())
    assert result["display_name"] == "遊戲測試"
    assert result["steam_appid"] == 789
    assert result["name_evidence"]["locale"] == "zh-Hans"
    assert result["name_evidence"]["converted"] is True
    assert [call[1]["params"]["l"] for call in session.calls] == ["tchinese", "schinese"]
    assert all(call[1]["allow_redirects"] is False and call[1]["timeout"] <= 8 for call in session.calls)


def test_steam_traditional_name_needs_only_one_request_and_wins_simplified():
    api, session = client([Response("新的繁體遊戲")])
    docs = build_documents([game(game_localizations=[{"name": "简体游戏", "region": {"identifier": "zh-CN"}}])],
                           start=START, checked_at=CHECKED)
    original = deepcopy(docs[0]["games"]["igdb:123"])
    summary = enrich_documents(docs[0], docs[1], api)
    updated = docs[0]["games"]["igdb:123"]
    assert updated["display_name"] == docs[1]["games"][0]["display_name"] == "新的繁體遊戲"
    assert updated["raw"] == original["raw"]
    assert {k: v for k, v in updated.items() if k not in (*NAME_FIELDS, "steam_name_lookup")} == {
        k: v for k, v in original.items() if k not in (*NAME_FIELDS, "steam_name_lookup")}
    assert len(session.calls) == summary["steam_request_count"] == 1


def test_network_failure_preserves_verified_simplified_cache_and_is_not_rate_limit():
    prior = normalize_game(game(game_localizations=[{"name": "游戏测试", "region": {"identifier": "zh-CN"}}]), START, END, CHECKED)
    master, public, _ = build_documents([game()], start=START, checked_at=CHECKED, previous={"games": {"igdb:123": prior}})
    api, _ = client([requests.Timeout("timeout")])
    summary = enrich_documents(master, public, api)
    assert public["games"][0]["display_name"] == "遊戲測試"
    assert summary["steam_rate_limited"] is False and summary["steam_failure_count"] == 1
    assert master["games"]["igdb:123"]["steam_name_lookup"]["status"] == "failed"


def test_conflicting_current_steam_ids_invalidate_cached_steam_name_without_request():
    api, _ = client([Response("Steam 中文遊戲")])
    old = api.lookup(game())
    raw = game(external_games=[{"uid": "888", "external_game_source": {"name": "Steam"}}])
    assert names(raw, previous=old)["name_zh_tw"] is None
    empty, session = client([])
    assert empty.lookup(raw) is None and session.calls == []


def test_cached_steam_name_requires_matching_official_product_url_and_integer_identity():
    api, _ = client([Response("Steam 中文遊戲")])
    old = api.lookup(game())
    for url in ("https://store.steampowered.com/app/888/", "https://unrelated.example/game"):
        corrupt = deepcopy(old)
        corrupt["name_url"] = url
        assert names(game(), previous=corrupt)["name_zh_tw"] is None
    corrupt = deepcopy(old)
    corrupt["name_evidence"]["steam_appid"] = True
    assert names(game(), previous=corrupt)["name_zh_tw"] is None


def test_steam_enrichment_never_looks_up_unqualified_or_already_traditional_games():
    master, public, _ = build_documents([game(id=123, hypes=29),
                                        game(id=124, game_localizations=[{"name": "繁中遊戲", "region": {"identifier": "zh-TW"}}])],
                                       start=START, checked_at=CHECKED)
    api, session = client([])
    enrich_documents(master, public, api)
    assert session.calls == []


def test_request_budget_and_429_stop_without_calendar_failure():
    api, session = client([Response("English")] * 40)
    for _ in range(40):
        assert api.lookup(game()) is None
    assert len(session.calls) == api.request_count == 30
    throttled, session = client([Response(status=429)])
    for _ in range(5):
        assert throttled.lookup(game()) is None
    assert len(session.calls) == 1 and throttled.rate_limited is True


def test_deadline_prevents_more_requests_after_time_is_exhausted():
    now = [0]
    session = Session([Response("English")])
    original = session.get

    def timed_get(*args, **kwargs):
        response = original(*args, **kwargs)
        now[0] = 5
        return response

    session.get = timed_get
    api = SteamNameClient(session=session, clock=lambda: now[0], sleep=lambda _: None, deadline_seconds=5)
    assert api.lookup(game()) is None
    assert len(session.calls) == 1 and session.calls[0][1]["timeout"] == 5


def test_steam_deadline_starts_after_the_preceding_igdb_collection():
    now = [0]
    session = Session([Response("中文遊戲")])
    api = SteamNameClient(session=session, clock=lambda: now[0], sleep=lambda _: None)
    now[0] = 480
    assert api.lookup(game())["display_name"] == "中文遊戲"
    assert api.deadline == 570


def test_budget_rotates_to_games_without_recent_attempts_and_keeps_lookup_state():
    raw = [game(id=123), game(id=124)]
    master, public, _ = build_documents(raw, start=START, checked_at=CHECKED)
    api, _ = client([Response("English"), Response("English")], max_requests=2)
    enrich_documents(master, public, api)
    assert "steam_name_lookup" in master["games"]["igdb:123"]
    assert "steam_name_lookup" not in master["games"]["igdb:124"]
    second, catalog, _ = build_documents(raw, start=START, checked_at="2026-10-04T04:00:00Z", previous=master)
    api, _ = client([Response("新的中文遊戲")], max_requests=1)
    enrich_documents(second, catalog, api)
    assert second["games"]["igdb:124"]["display_name"] == "新的中文遊戲"
    assert second["games"]["igdb:123"]["steam_name_lookup"]["checked_at"] == CHECKED
    assert all("steam_name_lookup" not in row for row in catalog["games"])
