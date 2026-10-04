from copy import deepcopy
from datetime import date, datetime, timezone
import json

import pytest
import requests

from nintendo_backend.catalog import build_documents
from nintendo_backend.chinese_names import SteamNameClient, enrich_documents, names
import scripts.publish as P
from scripts.publish import ALLOWED_PATHS, BACKEND, FILES, FRONTEND, GitHub, PublishError, _serialize, main, publish, validate_bundle

NOW = datetime(2026, 10, 3, 16, 15, tzinfo=timezone.utc)
CHECKED = "2026-10-03T16:05:00Z"


def bundle():
    raw = {"id": 12345, "name": "Nintendo test adventure", "hypes": 30, "category": 0,
           "platforms": [{"id": 130, "name": "Nintendo Switch"}], "summary": "A family adventure across an island.",
           "url": "https://www.igdb.com/games/nintendo-test-adventure",
           "release_dates": [{"id": 77, "platform": {"id": 130}, "category": 0,
                              "date": int(datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()), "region": 8}]}
    docs = build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED)
    return dict(zip(FILES, docs))


class RecordingClient:
    def __init__(self, backend_error=None, frontend_error=None):
        self.backend_error = backend_error
        self.frontend_error = frontend_error
        self.calls = []

    def preflight(self, repo):
        self.calls.append(("preflight", repo))
        if repo == BACKEND and self.backend_error:
            raise self.backend_error
        if repo == FRONTEND and self.frontend_error:
            raise self.frontend_error

    def commit_files(self, repo, files, message):
        assert set(files) == ALLOWED_PATHS
        self.calls.append(("commit", repo, {path: json.loads(content) for path, content in files.items()}))
        return "a" * 40 if repo == BACKEND else "b" * 40


def test_backend_permission_failure_still_persists_complete_frontend_master(capsys):
    client = RecordingClient(backend_error=PublishError("github_http_error", 403))
    result = publish(bundle(), client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert result["complete"] is True and result["backend_mirror"] == "blocked"
    frontend = next(call[2] for call in client.calls if call[:2] == ("commit", FRONTEND))
    assert frontend["data/nintendo_master.json"]["games"]["igdb:12345"]["raw"]["hypes"] == 30
    receipt = frontend["data/nintendo_refresh_status.json"]
    assert receipt["status"] == "published" and receipt["published_run_id"] == "1234"
    assert receipt["backend_error"] == {"reason": "github_http_error", "http_status": 403}
    assert "::warning::" in capsys.readouterr().out


def test_backend_state_saved_before_frontend_and_does_not_claim_publication():
    client = RecordingClient()
    result = publish(bundle(), client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    commits = [call for call in client.calls if call[0] == "commit"]
    assert [call[1] for call in commits] == [BACKEND, FRONTEND]
    assert commits[0][2]["data/nintendo_refresh_status.json"]["status"] == "prepared"
    assert commits[0][2]["data/nintendo_refresh_status.json"]["complete"] is False
    assert result["backend_mirror"] == "published"


def test_frontend_permission_failure_cannot_publish_backend_or_report_complete():
    client = RecordingClient(frontend_error=PublishError("repository_write_denied", 403))
    with pytest.raises(PublishError):
        publish(bundle(), client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert not any(call[0] == "commit" for call in client.calls)


def test_backend_read_access_with_denied_contents_write_uses_same_durable_fallback():
    class WriteDenied(RecordingClient):
        def commit_files(self, repo, files, message):
            if repo == BACKEND:
                raise PublishError("github_http_error", 403)
            return super().commit_files(repo, files, message)
    client = WriteDenied()
    result = publish(bundle(), client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert result["backend_mirror"] == "blocked" and result["complete"] is True
    assert any(call[:2] == ("commit", FRONTEND) for call in client.calls)


def test_frontend_write_denial_raises_after_preserving_only_prepared_backend_state():
    class WriteDenied(RecordingClient):
        def commit_files(self, repo, files, message):
            if repo == FRONTEND:
                raise PublishError("github_http_error", 403)
            return super().commit_files(repo, files, message)
    client = WriteDenied()
    with pytest.raises(PublishError):
        publish(bundle(), client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    commits = [call for call in client.calls if call[0] == "commit"]
    assert len(commits) == 1 and commits[0][1] == BACKEND
    assert commits[0][2]["data/nintendo_refresh_status.json"]["complete"] is False


@pytest.mark.parametrize("mutation", [
    lambda data: data["nintendo_refresh_status.json"].update(complete=False),
    lambda data: data["nintendo_refresh_status.json"].update(pending_count=1),
    lambda data: data["nintendo_refresh_status.json"].update(public_count=99),
    lambda data: data["nintendo_master.json"]["source"].update(complete=False),
    lambda data: data["nintendo_upcoming.json"]["games"][0].update(hypes=True),
    lambda data: data["nintendo_upcoming.json"]["games"][0]["releases"][0].update(date="2027-11-01"),
    lambda data: data["nintendo_upcoming.json"]["games"].append(deepcopy(data["nintendo_upcoming.json"]["games"][0])),
    lambda data: data["nintendo_upcoming.json"]["games"][0]["exclusivity"].update(status="confirmed"),
    lambda data: data["nintendo_master.json"]["games"]["igdb:12345"]["raw"].update(hypes=29),
    lambda data: data["nintendo_master.json"]["games"]["igdb:12345"]["raw"].update(summary="A hentai sex game", themes=[{"name": "Erotic"}]),
    lambda data: data["nintendo_upcoming.json"]["games"][0]["exclusivity"].update(
        status="confirmed", platform="NS", source="Nintendo official", url="https://www.nintendo.com/",
        evidence="Only on Nintendo Switch", checked_at=CHECKED, evidence_title="Nintendo test adventure", evidence_method="product_metadata"),
    lambda data: data["nintendo_upcoming.json"]["games"][0].update(display_name="Unverified relabel"),
    lambda data: data["nintendo_master.json"]["games"]["igdb:12345"].update(hypes=5000),
    lambda data: data["nintendo_refresh_status.json"].update(public_count=True),
])
def test_gate_rejects_incomplete_or_ineligible_data_before_remote_writes(mutation):
    data = bundle()
    mutation(data)
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


def test_unmodified_bundle_passes_full_formal_qualification_gate():
    validate_bundle(bundle(), now=NOW)


@pytest.fixture
def ps5_bundle(tmp_path, monkeypatch):
    raw = deepcopy(bundle()["nintendo_master.json"]["games"]["igdb:12345"]["raw"])
    raw["platforms"] = [{"id": 167, "name": "PlayStation 5"}]
    raw["release_dates"][0]["platform"] = {"id": 167}
    product = "JP0005-PPSA23593_00-APPLICATION00000"
    url = "https://store.playstation.com/zh-hant-tw/product/" + product
    releases = {"igdb:12345": {"igdb_id": 12345, "name_en": raw["name"], "releases": [{
        "platform": "PS5", "date": "2026-10-16", "verified_source_date": "2026-10-15",
        "verified_at": CHECKED, "source": "PlayStation Store 台灣官方", "url": url,
        "product_id": product, "release_time_utc": "2026-10-15T16:00:00Z"}]}}
    languages = {"igdb:12345": {"igdb_id": 12345, "name_en": raw["name"], "platforms": {"PS5": {
        "region": "taiwan", "supported_languages": ["en", "ja"], "complete": True,
        "source": "PlayStation Store 台灣商品語言", "source_url": url,
        "product_id": product, "checked_at": CHECKED, "evidence_type": "official_product_languages"}}}}
    for field, filename, registry in (("PLAYSTATION_RELEASE_REGISTRY", "ps5_dates.json", releases),
                                      ("PLAYSTATION_LANGUAGE_REGISTRY", "ps5_languages.json", languages)):
        path = tmp_path / filename
        path.write_text(json.dumps({"schema_version": 1, "games": registry}))
        monkeypatch.setattr(P, field, path)
    return dict(zip(FILES, build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED,
        release_registry=releases, language_registry=languages,
        source={"platform_ids_verified": [130, 167, 508]})))


def test_ps5_bundle_rebuilds_reviewed_regional_date_and_separate_languages(ps5_bundle):
    validate_bundle(ps5_bundle, now=NOW)
    row = ps5_bundle["nintendo_upcoming.json"]["games"][0]
    assert row["releases"][0]["date"] == "2026-10-16"
    assert row["releases"][0]["official_product_id"] == "JP0005-PPSA23593_00-APPLICATION00000"
    assert row["platform_language_support"]["PS5"]["languages"]["tchinese"] is False


def test_ps5_publication_requires_verified_native_platform_id(ps5_bundle):
    for payload in ps5_bundle.values():
        payload["source"]["platform_ids_verified"] = [130, 508]
    with pytest.raises(PublishError, match="qualification_gate_failed"):
        validate_bundle(ps5_bundle, now=NOW)


@pytest.mark.parametrize("change", ["date", "language", "store_identity"])
def test_ps5_matching_public_master_and_investigation_cannot_self_authorize_proof(ps5_bundle, change):
    ps5_bundle["nintendo_refresh_status.json"]["playstation_investigation"] = {
        "review_only": True, "evidence": {"date": "2026-10-17", "supported_languages": ["zh-Hant"]}}
    for row in (ps5_bundle["nintendo_master.json"]["games"]["igdb:12345"], ps5_bundle["nintendo_upcoming.json"]["games"][0]):
        if change == "date":
            row["releases"][0]["date"] = "2026-10-17"
        elif change == "language":
            row["platform_language_support"]["PS5"]["languages"].update(tchinese=True, chinese=True)
        else:
            row["releases"][0]["official_product_id"] = "10000001"
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(ps5_bundle, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


@pytest.fixture
def language_bundle(tmp_path, monkeypatch):
    assert P.NINTENDO_LANGUAGE_REGISTRY.is_absolute()
    raw = deepcopy(bundle()["nintendo_master.json"]["games"]["igdb:12345"]["raw"])
    registry = {"igdb:12345": {"igdb_id": 12345, "name_en": raw["name"], "platforms": {
        "NS": {"region": "taiwan", "supported_languages": ["en", "ja"], "complete": True,
               "source": "Nintendo Taiwan official product languages",
               "source_url": "https://www.nintendo.com/tw/games/switch/lineup", "checked_at": CHECKED,
               "evidence_type": "official_product_languages"}}}}
    path = tmp_path / "nintendo_languages.json"
    path.write_text(json.dumps({"schema_version": 1, "games": registry}))
    monkeypatch.setattr(P, "NINTENDO_LANGUAGE_REGISTRY", path)
    data = dict(zip(FILES, build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED,
                                           language_registry=registry)))
    return data, path


def test_language_gate_rebuilds_trusted_registry_from_any_working_directory(language_bundle, tmp_path, monkeypatch):
    data, _ = language_bundle
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    monkeypatch.chdir(outside)
    validate_bundle(data, now=NOW)
    native = data["nintendo_upcoming.json"]["games"][0]["platform_language_support"]["NS"]
    assert native["languages"]["tchinese"] is False and native["checked_at"] == CHECKED


@pytest.mark.parametrize("mutate", [
    lambda row: row["languages"].update(tchinese=True, chinese=True),
    lambda row: row.update(source_url="https://www.nintendo.com/tw/fake-product"),
    lambda row: row.update(checked_at="2026-10-03T16:14:00Z"),
    lambda row: row.update(region="north_america"),
    lambda row: row.update(complete=False),
])
def test_matching_master_and_public_cannot_forge_nintendo_language_evidence(language_bundle, mutate):
    data, _ = language_bundle
    for row in (data["nintendo_master.json"]["games"]["igdb:12345"], data["nintendo_upcoming.json"]["games"][0]):
        mutate(row["platform_language_support"]["NS"])
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


def test_removing_approved_language_registry_withdraws_existing_confirmation(language_bundle):
    data, path = language_bundle
    path.write_text('{"schema_version":1,"games":{}}')
    with pytest.raises(PublishError):
        validate_bundle(data, now=NOW)


@pytest.fixture
def edition_bundle(language_bundle):
    data, path = language_bundle
    doc = json.loads(path.read_text())
    row = doc["games"]["igdb:12345"]["platforms"]["NS"]
    row.update(edition_type="deluxe", edition_label="Deluxe 版", official_title="Nintendo test adventure Deluxe Edition",
               product_id="70010000114443", identity_relation="base_game_included",
               identity_evidence="官方商品描述包含 Nintendo test adventure 本體與追加內容。",
               identity_source_url="https://ec.nintendo.com/TW/zh/titles/70010000114443")
    path.write_text(json.dumps(doc))
    raw = data["nintendo_master.json"]["games"]["igdb:12345"]["raw"]
    result = dict(zip(FILES, build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED,
                                             language_registry=doc["games"])))
    return result, path


def test_edition_gate_accepts_independently_rebuilt_trusted_product_metadata(edition_bundle):
    data, _ = edition_bundle
    validate_bundle(data, now=NOW)
    expected = data["nintendo_master.json"]["games"]["igdb:12345"]["platform_editions"]
    assert data["nintendo_upcoming.json"]["games"][0]["platform_editions"] == expected
    assert expected["NS"]["label"] == "Deluxe 版"


@pytest.mark.parametrize("change", [
    {"type": "base_plus_dlc", "label": "本體＋偽造 DLC"},
    {"product_id": "70010000114444"}, {"source_url": "https://www.nintendo.com/tw/fake-product"},
])
def test_matching_master_and_public_cannot_forge_edition_metadata(edition_bundle, change):
    data, _ = edition_bundle
    for row in (data["nintendo_master.json"]["games"]["igdb:12345"], data["nintendo_upcoming.json"]["games"][0]):
        row["platform_editions"]["NS"].update(change)
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


def test_edition_claim_is_withdrawn_when_registry_review_is_removed(edition_bundle):
    data, path = edition_bundle
    doc = json.loads(path.read_text())
    row = doc["games"]["igdb:12345"]["platforms"]["NS"]
    row.pop("edition_type")
    row.pop("edition_label")
    path.write_text(json.dumps(doc))
    with pytest.raises(PublishError):
        validate_bundle(data, now=NOW)


@pytest.fixture
def taiwan_date_bundle(tmp_path, monkeypatch):
    assert P.TAIWAN_RELEASE_REGISTRY.is_absolute()
    raw = deepcopy(bundle()["nintendo_master.json"]["games"]["igdb:12345"]["raw"])
    registry = {"igdb:12345": {"igdb_id": 12345, "name_en": raw["name"], "releases": [{
        "platform": "NS", "date": "2026-10-16", "verified_source_date": "2026-10-15",
        "verified_at": CHECKED, "source": "Nintendo Taiwan official product",
        "url": "https://www.nintendo.com/tw/schedule"}]}}
    path = tmp_path / "nintendo_release_dates.json"
    path.write_text(json.dumps({"schema_version": 1, "games": registry}))
    monkeypatch.setattr(P, "TAIWAN_RELEASE_REGISTRY", path)
    data = dict(zip(FILES, build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED,
                                           release_registry=registry)))
    return data, path


def test_gate_accepts_only_repository_verified_taiwan_date_and_source(taiwan_date_bundle, tmp_path, monkeypatch):
    data, _ = taiwan_date_bundle
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    monkeypatch.chdir(outside)
    validate_bundle(data, now=NOW)
    public = data["nintendo_upcoming.json"]["games"][0]["releases"][0]
    assert public["date"] == "2026-10-16" and public["source_date"] == "2026-10-15"
    assert public["taiwan_release_confirmed"] is True


@pytest.mark.parametrize("field,value", [
    ("date", "2026-10-17"), ("official_source_url", "https://www.nintendo.com/tw/fake-product"),
    ("official_source_name", "A self-attested announcement"), ("source_timestamp", 1000000000),
    ("time_zone", "UTC"), ("timestamp_taipei_date", "2026-10-18"),
])
def test_matching_master_and_public_cannot_forge_taiwan_date_evidence(taiwan_date_bundle, field, value):
    data, _ = taiwan_date_bundle
    for row in (data["nintendo_master.json"]["games"]["igdb:12345"], data["nintendo_upcoming.json"]["games"][0]):
        row["releases"][0][field] = value
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


def test_registry_removed_or_changed_source_date_cannot_keep_taiwan_confirmation(taiwan_date_bundle):
    data, path = taiwan_date_bundle
    path.write_text('{"schema_version":1,"games":{}}')
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


@pytest.fixture
def official_name_bundle(tmp_path, monkeypatch):
    assert P.CHINESE_NAME_REGISTRY.is_absolute()
    raw = deepcopy(bundle()["nintendo_master.json"]["games"]["igdb:12345"]["raw"])
    raw["game_localizations"] = [{"name": "IGDB 中文別名", "region": {"identifier": "zh-TW"}}]
    registry = {"igdb:12345": {"igdb_id": 12345, "name_en": raw["name"],
                "name_zh_tw": "任天堂官方冒險", "source": "Nintendo official Traditional Chinese product page",
                "url": "https://www.nintendo.com/tw/store/products/test-adventure/"}}
    path = tmp_path / "chinese_names.json"
    path.write_text(json.dumps({"schema_version": 1, "games": registry}), encoding="utf-8")
    monkeypatch.setattr(P, "CHINESE_NAME_REGISTRY", path)
    docs = build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED, name_registry=registry)
    return dict(zip(FILES, docs)), registry, path


def test_gate_rebuild_uses_repository_official_name_before_conflicting_igdb_chinese(official_name_bundle, monkeypatch, tmp_path):
    data, _, _ = official_name_bundle
    # Publication can be invoked outside the checkout; registry identity stays fixed.
    outside = tmp_path / "another-working-directory"
    outside.mkdir()
    monkeypatch.chdir(outside)
    original = deepcopy(data)
    validate_bundle(data, now=NOW)
    assert data == original
    client = RecordingClient()
    publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    frontend = next(call[2] for call in client.calls if call[:2] == ("commit", FRONTEND))
    assert frontend["data/nintendo_upcoming.json"]["games"][0]["display_name"] == "任天堂官方冒險"


@pytest.mark.parametrize("fields", [
    {"display_name": "捏造的遊戲", "name_zh_tw": "捏造的遊戲"},
    {"name_source": "An unreviewed claim of an official source"},
    {"name_url": "https://example.test/fake-official-title"},
    {"steam_appid": 987654},
])
def test_gate_rejects_self_reported_official_name_changes_before_remote_writes(official_name_bundle, fields):
    data, _, _ = official_name_bundle
    data["nintendo_master.json"]["games"]["igdb:12345"].update(fields)
    data["nintendo_upcoming.json"]["games"][0].update(fields)
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


def test_gate_does_not_accept_an_official_registry_claim_missing_from_repository_registry(official_name_bundle):
    data, registry, path = official_name_bundle
    path.write_text(json.dumps({"schema_version": 1, "games": {}}), encoding="utf-8")
    # The bundle still carries valid-looking identity, Chinese locale and HTTPS
    # evidence, but none of them authorize an absent curated registry entry.
    assert data["nintendo_master.json"]["games"]["igdb:12345"]["name_evidence"] == names(
        data["nintendo_master.json"]["games"]["igdb:12345"]["raw"], registry=registry)["name_evidence"]
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


def test_gate_accepts_identity_verified_steam_names_and_preserves_master_only_lookup_state():
    raw = deepcopy(bundle()["nintendo_master.json"]["games"]["igdb:12345"]["raw"])
    raw["websites"] = [{"url": "https://store.steampowered.com/app/21/Test_adventure/"}]
    master, public, status = build_documents([raw], start=date(2026, 10, 4), checked_at=CHECKED)

    class NameSession:
        def get(self, *_args, **_kwargs):
            return Response(200, {"21": {"success": True, "data": {
                "steam_appid": 21, "type": "game", "name": "測試冒險"}}})

    enrich_documents(master, public, SteamNameClient(session=NameSession()))
    data = dict(zip(FILES, (master, public, status)))
    validate_bundle(data, now=NOW)
    assert master["games"]["igdb:12345"]["steam_name_lookup"]["status"] == "matched"
    assert "steam_name_lookup" not in public["games"][0]
    assert public["games"][0]["display_name"] == "測試冒險"

    # Both copies agreeing cannot authorize a name URL for a different game.
    master["games"]["igdb:12345"]["name_url"] = "https://store.steampowered.com/app/22/"
    public["games"][0]["name_url"] = "https://store.steampowered.com/app/22/"
    client = RecordingClient()
    with pytest.raises(PublishError):
        publish(data, client, now=NOW, slot=CHECKED, trigger_source="manual", run_id="1234")
    assert client.calls == []


class Response:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


class ConcurrentSession:
    def __init__(self):
        self.head_reads = 0
        self.requests = []
        self.ref_writes = 0

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        assert url.startswith("https://api.github.com/repos/" + FRONTEND + "/")
        assert kwargs["allow_redirects"] is False
        if url.endswith("/git/ref/heads/main"):
            self.head_reads += 1
            return Response(200, {"object": {"sha": str(self.head_reads) * 40}})
        if "/git/commits/" in url:
            return Response(200, {"tree": {"sha": "a" * 39 + str(self.head_reads)}})
        if url.endswith("/git/blobs"):
            return Response(201, {"sha": "b" * 40})
        if url.endswith("/git/trees"):
            return Response(201, {"sha": "c" * 40})
        if url.endswith("/git/commits"):
            return Response(201, {"sha": "d" * 39 + str(self.head_reads)})
        if url.endswith("/git/refs/heads/main"):
            self.ref_writes += 1
            return Response(422 if self.ref_writes == 1 else 200, {"object": {"sha": kwargs["json"]["sha"]}})
        raise AssertionError("Unexpected request")


def test_fast_forward_conflict_rebuilds_tree_from_new_head_and_preserves_other_paths():
    session = ConcurrentSession()
    client = GitHub("masked-secret", session)
    files = {path: "{}\n" for path in ALLOWED_PATHS}
    result = client.commit_files(FRONTEND, files, "Publish Nintendo")
    assert result == "d" * 39 + "2"
    trees = [item[2]["json"] for item in session.requests if item[1].endswith("/git/trees")]
    assert [tree["base_tree"] for tree in trees] == ["a" * 39 + "1", "a" * 39 + "2"]
    assert all({row["path"] for row in tree["tree"]} == ALLOWED_PATHS for tree in trees)
    refs = [item[2]["json"] for item in session.requests if item[0] == "PATCH"]
    assert all(ref["force"] is False for ref in refs)


def test_path_whitelist_rejects_overwriting_steam_data():
    client = GitHub("masked-secret", ConcurrentSession())
    with pytest.raises(PublishError, match="unapproved_publication_paths"):
        client.commit_files(FRONTEND, {"data/steam_upcoming.json": "{}"}, "Unsafe")


def test_repository_whitelist_never_sends_credentials_to_another_host():
    session = ConcurrentSession()
    client = GitHub("masked-secret", session)
    with pytest.raises(PublishError, match="unapproved_repository"):
        client.request("GET", "/repos/unapproved/repository")
    assert session.requests == []


def test_dense_candidate_ledger_compacts_transport_without_losing_raw_evidence():
    data = bundle()
    master = data["nintendo_master.json"]
    # Real IGDB references contain many nested records. This fixture checks the
    # request budget and round trip, including non-ASCII and quoted evidence.
    master["games"]["igdb:12345"]["raw"]["age_ratings"] = [
        {"id": index, "rating_content_descriptions": [
            {"description": "繁體中文 \"證據\"", "description_type": {"id": sub, "name": "Mild fantasy violence"}}
            for sub in range(5)]} for index in range(100)]
    files = _serialize(data)
    compact = files["data/nintendo_master.json"]
    assert json.loads(compact) == master
    pretty = json.dumps(master, ensure_ascii=False, indent=2) + "\n"
    compact_request = json.dumps({"content": compact, "encoding": "utf-8"}).encode()
    pretty_request = json.dumps({"content": pretty, "encoding": "utf-8"}).encode()
    assert len(compact_request) < len(pretty_request) * 0.65
    assert files["data/nintendo_upcoming.json"].count("\n") > 2


def test_large_master_publication_transport_is_resumable_by_the_collector(tmp_path, monkeypatch):
    from nintendo_backend import persistence
    from nintendo_backend.collect import load_existing
    monkeypatch.setattr(persistence, "ENCODE_THRESHOLD_BYTES", 1)
    data = bundle()
    data["nintendo_master.json"]["playstation_investigation"] = {"review_only": True, "games": {
        "igdb:12345": {"checked_at": CHECKED, "status": "budget_exhausted", "attempted_urls": []}}}
    transport = _serialize(data)["data/nintendo_master.json"]
    assert json.loads(transport)["storage_format"] == "gzip-base64"
    path = tmp_path / "nintendo_master.json"
    path.write_text(transport)
    assert load_existing(path) == data["nintendo_master.json"]


@pytest.mark.parametrize("method,suffix,operation", [("POST", "/git/blobs", "blob"),
    ("POST", "/git/trees", "tree"), ("POST", "/git/commits", "commit"),
    ("PATCH", "/git/refs/heads/main", "ref"), ("GET", "", "read")])
def test_http_failure_identifies_only_fixed_operation_and_sanitized_error_detail(method, suffix, operation):
    class FailedSession:
        def request(self, *args, **kwargs):
            return Response(422, {"message": "Invalid request SECRET-CREDENTIAL and private response"})
    client = GitHub("SECRET-CREDENTIAL", FailedSession())
    with pytest.raises(PublishError) as caught:
        client.request(method, "/repos/" + FRONTEND + suffix, {})
    error = caught.value
    assert error.reason == f"github_{operation}_http_error" and error.status == 422
    assert error.detail == "invalid_request" and "SECRET" not in str(error)


def test_main_does_not_print_request_exception_or_credentials(tmp_path, monkeypatch, capsys):
    for name, payload in bundle().items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("FRONTEND_REPO_TOKEN", "DO-NOT-LOG-THIS-TOKEN")
    monkeypatch.setenv("GITHUB_RUN_ID", "1234")
    def fail(*args, **kwargs):
        raise requests.ConnectionError("DO-NOT-LOG-THIS-TOKEN and response body")
    monkeypatch.setattr("scripts.publish.GitHub.preflight", fail)
    # Use a runtime-current window so the gate reaches the failing transport.
    monkeypatch.setattr("scripts.publish.validate_bundle", lambda *args, **kwargs: None)
    monkeypatch.setattr("scripts.guard.target_slot", lambda *args, **kwargs: CHECKED)
    assert main(["--output-dir", str(tmp_path), "--target-slot", CHECKED]) == 1
    output = capsys.readouterr().out
    assert "publication_failed" in output and "DO-NOT-LOG" not in output and "response body" not in output
