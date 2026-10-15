"""Commit a validated console bundle and project the acknowledged database state."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile

from scripts.publish import FILES, FRONTEND, PublishError, _serialize


@contextmanager
def projection_repository(token):
    """Short-lived credentials are supplied only to Git's askpass environment."""
    from radar_core.publication import SubprocessGitRepository
    with tempfile.TemporaryDirectory(prefix="radar-igdb-projection-") as folder:
        base = Path(folder)
        root = base / "frontend"
        try:
            subprocess.run(["git", "clone", "--depth", "1", "--no-tags",
                            f"https://github.com/{FRONTEND}.git", str(root)],
                           check=True, capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            raise PublishError("frontend_projection_checkout_failed") from None
        askpass = base / "askpass.py"
        askpass.write_text("#!/usr/bin/env python3\nimport os,sys\n"
                           "print('x-access-token' if 'username' in sys.argv[1].lower() "
                           "else os.environ['RADAR_PROJECTION_TOKEN'])\n", encoding="utf-8")
        askpass.chmod(0o700)
        environment = {"GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0",
                       "RADAR_PROJECTION_TOKEN": token}
        yield SubprocessGitRepository(root, disposable_checkout=True, environment=environment)


def _publish(files, *, generated_at, repository):
    from radar_core.publication import publish_with_retry, snapshot_revision
    from scripts.guard import parse_utc
    payload_revision = snapshot_revision({path: raw.decode("utf-8") for path, raw in files.items()})

    def apply(root):
        # Every CAS retry starts from the latest primary generation. Replaying
        # this frozen bundle does not repeat any collection request.
        path = root / "data/nintendo_refresh_status.json"
        previous = json.loads(path.read_bytes())
        if parse_utc(previous["generated_at"]) > parse_utc(generated_at):
            raise PublishError("newer_database_collection_exists")
        for relative, raw in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

    return publish_with_retry(repository, apply, paths=tuple("data/" + name for name in FILES),
        message="data: project committed IGDB console catalog",
        input_revision=payload_revision, payload_revision=payload_revision, max_attempts=8)


def publish_primary(bundle, client, *, now, slot, trigger_source, run_id, repository=None):
    from scripts.guard import utc_text
    committed = deepcopy(bundle)
    status = committed["nintendo_refresh_status.json"]
    # Delivery is acknowledged separately by the shared operation/outbox, not
    # by putting a claimed Git SHA into a pre-publication data document.
    status.update(status="persisted", complete=True, storage_backend="turso",
                  state_persisted=True, committed_at=utc_text(now), target_slot=slot,
                  trigger_source=trigger_source, run_id=run_id, backend_mirror="not_required",
                  authoritative_source=FRONTEND + "@main")
    for key in ("published_at", "published_run_id", "frontend_commit", "backend_commit", "backend_error"):
        status.pop(key, None)
    files = {path: raw.encode("utf-8") for path, raw in _serialize(committed).items()}
    if repository is None:
        with projection_repository(client.token) as target:
            receipt = _publish(files, generated_at=status["generated_at"], repository=target)
    else:
        receipt = _publish(files, generated_at=status["generated_at"], repository=repository)
    return {"status": "published", "complete": True, "state_persisted": True,
            "public_count": status["public_count"], "candidate_count": status["candidate_count"],
            "frontend_commit": receipt.published_revision, "backend_mirror": "not_required", "backend_error": None,
            "authoritative_source": FRONTEND + "@main",
            "publication_receipt": receipt.to_dict(), "target_slot": slot, "published_run_id": run_id}
