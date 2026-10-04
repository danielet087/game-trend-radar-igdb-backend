"""Publish a fully validated IGDB console snapshot with atomic, fast-forward commits."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import requests

BACKEND = "danielet087/game-trend-radar-igdb-backend"
FRONTEND = "danielet087/game-trend-radar"
REPOSITORIES = {BACKEND, FRONTEND}
FILES = ("nintendo_master.json", "nintendo_upcoming.json", "nintendo_refresh_status.json")
ALLOWED_PATHS = {"data/" + name for name in FILES}
SHA = re.compile(r"[0-9a-f]{40}\Z")
CHINESE_NAME_REGISTRY = Path(__file__).resolve().parents[1] / "data" / "chinese_names.json"
TAIWAN_RELEASE_REGISTRY = Path(__file__).resolve().parents[1] / "data" / "nintendo_release_dates.json"
NINTENDO_LANGUAGE_REGISTRY = Path(__file__).resolve().parents[1] / "data" / "nintendo_languages.json"
PLAYSTATION_RELEASE_REGISTRY = Path(__file__).resolve().parents[1] / "data" / "playstation_release_dates.json"
PLAYSTATION_LANGUAGE_REGISTRY = Path(__file__).resolve().parents[1] / "data" / "playstation_languages.json"


class PublishError(Exception):
    def __init__(self, reason: str, status: int | None = None, detail: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.detail = detail


def _sha(value) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise PublishError("invalid_github_response")
    return value


def _http_operation(method: str, path: str) -> str:
    if method == "POST" and path.endswith("/git/blobs"):
        return "blob"
    if method == "POST" and path.endswith("/git/trees"):
        return "tree"
    if method == "POST" and path.endswith("/git/commits"):
        return "commit"
    if method == "PATCH" and path.endswith("/git/refs/heads/main"):
        return "ref"
    return "read"


def _http_detail(response) -> str | None:
    """Map known error wording to an enum; never emit any response text."""
    try:
        payload = response.json()
    except Exception:
        return None
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, str):
        return None
    normalized = message.casefold()
    size_words = ("too large", "too big", "larger than", "exceed")
    if "blob" in normalized and any(word in normalized for word in size_words):
        return "blob_size_limit"
    if "content" in normalized and any(word in normalized for word in size_words):
        return "content_size_limit"
    if "invalid request" in normalized:
        return "invalid_request"
    if "not a fast forward" in normalized or "reference update failed" in normalized:
        return "non_fast_forward"
    if "validation failed" in normalized:
        return "validation_failed"
    return None


class GitHub:
    """Only fixed repositories and fixed API hosts are reachable with the token."""

    def __init__(self, token: str, session=None):
        self.session = session or requests.Session()
        if session is None:
            self.session.trust_env = False
        self.token = token

    def request(self, method: str, path: str, payload=None):
        if not any(path == f"/repos/{repo}" or path.startswith(f"/repos/{repo}/") for repo in REPOSITORIES):
            raise PublishError("unapproved_repository")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "game-trend-radar-nintendo-publisher"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            response = self.session.request(method, "https://api.github.com" + path,
                                            headers=headers, json=payload, timeout=30, allow_redirects=False)
        except requests.RequestException:
            raise PublishError("github_connection_failed") from None
        if response.status_code not in {200, 201}:
            raise PublishError(f"github_{_http_operation(method, path)}_http_error", response.status_code,
                               _http_detail(response))
        try:
            result = response.json()
        except (ValueError, requests.RequestException):
            raise PublishError("invalid_github_response") from None
        if not isinstance(result, dict):
            raise PublishError("invalid_github_response")
        return result

    def preflight(self, repo: str) -> None:
        result = self.request("GET", f"/repos/{repo}")
        if result.get("full_name", "").lower() != repo.lower():
            raise PublishError("unexpected_repository")
        permissions = result.get("permissions")
        if isinstance(permissions, dict) and permissions.get("push") is False:
            raise PublishError("repository_write_denied", 403)
        if result.get("archived") is True or result.get("disabled") is True:
            raise PublishError("repository_not_writable")

    def head(self, repo: str) -> tuple[str, str]:
        ref = self.request("GET", f"/repos/{repo}/git/ref/heads/main")
        head = _sha((ref.get("object") or {}).get("sha"))
        commit = self.request("GET", f"/repos/{repo}/git/commits/{head}")
        return head, _sha((commit.get("tree") or {}).get("sha"))

    def read_json(self, repo: str, path: str) -> dict | None:
        if path not in ALLOWED_PATHS:
            raise PublishError("unapproved_path")
        head, _ = self.head(repo)
        try:
            response = self.request("GET", f"/repos/{repo}/contents/{path}?ref={head}")
        except PublishError as error:
            if error.status == 404:
                return None
            raise
        if response.get("encoding") != "base64" or not isinstance(response.get("content"), str):
            raise PublishError("invalid_github_response")
        try:
            result = json.loads(base64.b64decode(response["content"]).decode("utf-8"))
        except (ValueError, UnicodeError):
            raise PublishError("invalid_published_json") from None
        if not isinstance(result, dict):
            raise PublishError("invalid_published_json")
        return result

    def commit_files(self, repo: str, files: dict[str, str], message: str) -> str:
        if repo not in REPOSITORIES or set(files) != ALLOWED_PATHS:
            raise PublishError("unapproved_publication_paths")
        blobs = []
        for path in sorted(files):
            blob = self.request("POST", f"/repos/{repo}/git/blobs",
                                {"content": files[path], "encoding": "utf-8"})
            blobs.append({"path": path, "mode": "100644", "type": "blob", "sha": _sha(blob.get("sha"))})
        # Every retry starts from the latest HEAD/tree; unrelated writers remain intact.
        for attempt in range(3):
            head, base_tree = self.head(repo)
            tree = self.request("POST", f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": blobs})
            commit = self.request("POST", f"/repos/{repo}/git/commits",
                                  {"message": message, "tree": _sha(tree.get("sha")), "parents": [head]})
            sha = _sha(commit.get("sha"))
            try:
                self.request("PATCH", f"/repos/{repo}/git/refs/heads/main", {"sha": sha, "force": False})
                return sha
            except PublishError as error:
                if error.status not in {409, 422} or attempt == 2:
                    raise
        raise PublishError("concurrent_publication_failed")


def _read_json(path: Path) -> dict:
    def reject_constant(_):
        raise ValueError("nonfinite_json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except (ValueError, OSError, UnicodeError):
        raise PublishError("invalid_output_file") from None
    if not isinstance(payload, dict):
        raise PublishError("invalid_output_file")
    return payload


def validate_bundle(bundle: dict[str, dict], *, now: datetime) -> None:
    from nintendo_backend.catalog import build_documents
    from nintendo_backend.chinese_names import NAME_FIELDS, load_registry, names
    from nintendo_backend.exclusivity import validate_enriched_game
    from nintendo_backend.igdb import CollectionError
    from nintendo_backend.languages import load_combined_registry as load_language_registry
    from nintendo_backend.taiwan_releases import load_combined_registry as load_release_registry
    from scripts.guard import parse_utc

    if set(bundle) != set(FILES):
        raise PublishError("incomplete_output_bundle")
    master, public, status = (bundle[name] for name in FILES)
    for payload in (master, public, status):
        provenance = payload.get("source") if isinstance(payload, dict) else None
        if (not isinstance(provenance, dict) or payload.get("schema_version") != 1 or
            provenance.get("complete") is not True or provenance.get("provider") != "IGDB" or
            type(provenance.get("hypes_threshold")) is not int or provenance["hypes_threshold"] != 30):
            raise PublishError("incomplete_collection")
    if status.get("complete") is not True or status.get("status") != "complete" or status.get("pending_count") != 0:
        raise PublishError("incomplete_collection")
    try:
        generated = parse_utc(master.get("generated_at"))
        start = date.fromisoformat(master["window"]["start"])
        if generated > now or start != now.astimezone(ZoneInfo("Asia/Taipei")).date() or generated.astimezone(ZoneInfo("Asia/Taipei")).date() != start:
            raise ValueError("stale_collection")
        rows = master["games"]
        if not isinstance(rows, dict) or any(not isinstance(row, dict) for row in rows.values()):
            raise ValueError("invalid_master")
        if any(any(platform.get("id") == 167 for platform in row.get("raw", {}).get("platforms", [])
                   if isinstance(platform, dict)) or
               any(platform.get("code") == "PS5" for platform in row.get("platforms", [])
                   if isinstance(platform, dict)) for row in rows.values()):
            verified = master["source"].get("platform_ids_verified")
            if (not isinstance(verified, list) or len(verified) != 3
                    or any(type(value) is not int for value in verified)
                    or set(verified) != {130, 167, 508}):
                raise ValueError("unverified_ps5_platform_identity")
        # Curated official names are approved by repository data, never by a
        # publication bundle's own claim to have verified a source URL.
        name_registry = load_registry(CHINESE_NAME_REGISTRY)
        release_registry = load_release_registry(TAIWAN_RELEASE_REGISTRY, PLAYSTATION_RELEASE_REGISTRY)
        language_registry = load_language_registry(NINTENDO_LANGUAGE_REGISTRY, PLAYSTATION_LANGUAGE_REGISTRY)
        for row in rows.values():
            evidence = row.get("name_evidence")
            if isinstance(evidence, dict) and evidence.get("provider") == "official_registry":
                approved = names(row["raw"], registry=name_registry)
                if (not isinstance(approved.get("name_evidence"), dict)
                        or approved["name_evidence"].get("provider") != "official_registry"
                        or any(row.get(field) != approved.get(field) for field in NAME_FIELDS)):
                    raise ValueError("unverified_official_name")
        rebuilt_master, rebuilt_public, rebuilt_status = build_documents(
            [row["raw"] for row in rows.values()], start=start, checked_at=master["generated_at"],
            previous=master, source=master["source"], name_registry=name_registry,
            release_registry=release_registry, language_registry=language_registry)
    except (ValueError, KeyError, TypeError, CollectionError):
        raise PublishError("qualification_gate_failed") from None
    if master["window"] != rebuilt_master["window"] or public.get("window") != rebuilt_master["window"]:
        raise PublishError("invalid_calendar_window")
    for key, entry in rows.items():
        if not validate_enriched_game(rebuilt_master["games"][key], entry):
            raise PublishError("master_qualification_gate_failed")
    for payload in (public, status):
        if payload.get("generated_at") != master["generated_at"] or payload.get("source") != master["source"]:
            raise PublishError("inconsistent_collection_metadata")
    games = public.get("games")
    if not isinstance(games, list) or any(not isinstance(row, dict) for row in games):
        raise PublishError("invalid_public_catalog")
    ids = [row.get("id") for row in games]
    expected = {row["id"]: row for row in rebuilt_public["games"]}
    if len(set(ids)) != len(ids) or set(ids) != set(expected):
        raise PublishError("public_master_mismatch")
    for row in games:
        reference = expected[row["id"]]
        # Reuse the collector's complete eligibility rules and official evidence
        # verifier. Only the two evidence fields may differ from the raw rebuild.
        if not validate_enriched_game(reference, row):
            raise PublishError("qualification_gate_failed")
        if any(row.get(key) != rows[row["id"]].get(key) for key in ("exclusivity", "nintendo_url", "playstation_url", "platform_urls")):
            raise PublishError("inconsistent_exclusivity_evidence")
    for key in ("candidate_count", "public_count", "popularity_counts", "content_review_count", "content_excluded_count", "pending_count"):
        actual = status.get(key)
        numeric = isinstance(actual, dict) and all(type(value) is int and value >= 0 for value in actual.values()) if key == "popularity_counts" else type(actual) is int and actual >= 0
        if not numeric or actual != rebuilt_status.get(key):
            raise PublishError("public_count_mismatch")
    if "exclusivity_counts" in status:
        counts = {kind: sum(row["exclusivity"]["status"] == kind for row in games)
                  for kind in ("confirmed", "listed_only", "multi_platform", "unknown")}
        actual = status["exclusivity_counts"]
        if not isinstance(actual, dict) or any(type(value) is not int for value in actual.values()) or actual != counts:
            raise PublishError("exclusivity_count_mismatch")


def _serialize(bundle: dict[str, dict]) -> dict[str, str]:
    from nintendo_backend.persistence import encode_master
    from nintendo_backend.igdb import CollectionError
    try:
        # Preserve every raw record and history. Large ledgers use a checked,
        # deterministic compressed envelope; consumers decode before resuming.
        return {"data/" + name: json.dumps(
            encode_master(bundle[name]) if name == "nintendo_master.json" else bundle[name],
            ensure_ascii=False, allow_nan=False,
            **({"separators": (",", ":")} if name == "nintendo_master.json" else {"indent": 2})) + "\n"
                for name in FILES}
    except (ValueError, TypeError, CollectionError):
        raise PublishError("invalid_output_file") from None


def publish(bundle: dict[str, dict], client: GitHub, *, now: datetime, slot: str,
            trigger_source: str, run_id: str) -> dict:
    from scripts.guard import parse_utc, target_slot, utc_text

    validate_bundle(bundle, now=now)
    normalized_slot = target_slot(now, slot, trigger_source)
    if parse_utc(normalized_slot) > parse_utc(bundle["nintendo_master.json"]["generated_at"]):
        raise PublishError("slot_after_collection")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9]+", run_id):
        raise PublishError("invalid_run_id")
    # Frontend is the durable source of master state and must be writable first.
    client.preflight(FRONTEND)
    backend_mirror = "published"
    backend_error = None
    backend_sha = None
    prepared = deepcopy(bundle)
    prepared["nintendo_refresh_status.json"].update({"status": "prepared", "complete": False,
                                                   "target_slot": normalized_slot, "trigger_source": trigger_source,
                                                   "run_id": run_id, "backend_mirror": "published"})
    try:
        client.preflight(BACKEND)
        backend_sha = client.commit_files(BACKEND, _serialize(prepared), "data: preserve complete Nintendo collection")
    except PublishError as error:
        backend_mirror = "blocked"
        backend_error = {"reason": error.reason, "http_status": error.status}
        if error.detail:
            backend_error["detail"] = error.detail
        code = f"HTTP {error.status}" if type(error.status) is int else "GitHub request failed"
        print(f"::warning::Nintendo backend mirror unavailable ({code}); the frontend master remains the durable collection state.")
    published = deepcopy(bundle)
    receipt = published["nintendo_refresh_status.json"]
    receipt.update({"status": "published", "complete": True, "published_at": utc_text(now),
                    "target_slot": normalized_slot, "trigger_source": trigger_source, "run_id": run_id,
                    "published_run_id": run_id, "backend_mirror": backend_mirror,
                    "backend_error": backend_error, "backend_commit": backend_sha})
    frontend_sha = client.commit_files(FRONTEND, _serialize(published), "data: publish validated Nintendo release calendar")
    return {"status": "published", "complete": True, "public_count": receipt["public_count"],
            "candidate_count": receipt["candidate_count"], "frontend_commit": frontend_sha,
            "backend_mirror": backend_mirror, "backend_error": backend_error,
            "target_slot": normalized_slot, "published_run_id": run_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--target-slot", default="")
    parser.add_argument("--trigger-source", choices=("manual", "cloudflare", "schedule"), default="manual")
    args = parser.parse_args(argv)
    token = os.environ.get("FRONTEND_REPO_TOKEN", "").strip()
    if not token:
        print(json.dumps({"status": "error", "reason": "missing_publish_credentials"}))
        return 1
    try:
        bundle = {name: _read_json(args.output_dir / name) for name in FILES}
        now = datetime.now(timezone.utc)
        # A direct local manual publication defaults to collection start, never a future slot.
        slot = args.target_slot or bundle["nintendo_master.json"]["generated_at"]
        result = publish(bundle, GitHub(token), now=now, slot=slot,
                         trigger_source=args.trigger_source, run_id=os.environ.get("GITHUB_RUN_ID", ""))
        (args.output_dir / "publication_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("### IGDB 主機月曆資料發布\n\n")
                handle.write(f"- 候選：{result['candidate_count']} 款\n- 月曆：{result['public_count']} 款\n")
                handle.write(f"- IGDB 後端鏡像：{result['backend_mirror']}\n")
                handle.write("- 續查狀態：前端 nintendo_master.json（已持久化）\n")
        return 0
    except PublishError as error:
        failure = {"status": "error", "reason": error.reason, "http_status": error.status}
        if error.detail:
            failure["detail"] = error.detail
        print(json.dumps(failure))
        return 1
    except Exception:
        # Network bodies, URLs, exception text and credential material are never logged.
        print(json.dumps({"status": "error", "reason": "publication_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
