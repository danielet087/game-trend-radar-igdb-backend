"""Where the IGDB candidate ledger lives in the database.

``data/nintendo_master.json`` moves from the frontend source to the
database-only source ``danielet087/game-trend-radar-igdb-backend@nintendo-state``
(never projected to Git or the site). The backend tool
``scripts/move_nintendo_state.py`` creates that source and later retires the
frontend copy after checking that every copy is identical. Each publication
reads the stage once:

* frontend stage (the frontend copy exists): the frontend copy stays
  authoritative and is published with the public files as before; afterwards
  the same bytes are mirrored to the database-only source. A mirror failure
  only warns, and the retire step refuses while the copies differ.
* database stage (no frontend copy): the database-only source is
  authoritative. The ledger is written there first, with a compare-and-swap on
  the generation that was read, after checking that it is not older than the
  stored ledger and that retained candidates are unchanged; the public files
  are published afterwards, without it.

A run whose stage changes before its frontend publication stops without
publishing; the next run follows the new stage. Only names, digests and byte
counts are printed.
"""
from __future__ import annotations

import hashlib

from scripts.publish import FRONTEND as FRONTEND_REPOSITORY

STATE_REPOSITORY = "danielet087/game-trend-radar-igdb-backend"
STATE_BRANCH = "nintendo-state"
STATE_SOURCE = STATE_REPOSITORY + "@" + STATE_BRANCH
STATE_PATH = "data/nintendo_master.json"
FRONTEND_STAGE, DATABASE_STAGE = "frontend", "database"
CAS_ATTEMPTS = 3


def _storage(storage=None):
    if storage is None:
        from radar_core.storage import primary as storage
    return storage


def digest(raw: bytes | None) -> str | None:
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def stage(storage=None) -> str:
    """frontend while the frontend source still holds the ledger, else database."""
    head, _ = _storage(storage).read_files(FRONTEND_REPOSITORY, ())
    return FRONTEND_STAGE if STATE_PATH in head["manifest"] else DATABASE_STAGE


def read_ledger(storage=None) -> tuple[str, bytes]:
    """The authoritative ledger bytes and their stage; a missing ledger is an error, never empty."""
    storage = _storage(storage)
    _, files = storage.read_files(FRONTEND_REPOSITORY, [STATE_PATH])
    if STATE_PATH in files:
        return FRONTEND_STAGE, files[STATE_PATH]
    _, files = storage.read_files(STATE_REPOSITORY, [STATE_PATH], branch=STATE_BRANCH)
    return DATABASE_STAGE, files[STATE_PATH]


def write_ledger(raw: bytes, *, check=None, storage=None) -> dict:
    """Compare-and-swap the ledger into the database-only source.

    ``check(prior_bytes)`` runs against the generation about to be replaced, on
    every attempt, and may raise to stop before anything is written.
    """
    from radar_core.storage.sql import StorageError
    storage = _storage(storage)
    revision = digest(raw)
    for attempt in range(1, CAS_ATTEMPTS + 1):
        head, files = storage.read_files(STATE_REPOSITORY, [STATE_PATH], branch=STATE_BRANCH)
        prior = files.get(STATE_PATH)
        if prior == raw:
            return {"status": "unchanged", "generation": head["generation"], "sha256": revision}
        if check is not None:
            check(prior)
        try:
            storage.write_files(STATE_REPOSITORY, {STATE_PATH: raw}, branch=STATE_BRANCH,
                                base_generation=head["generation"],
                                operation_id=f"igdb-ledger-{revision}-from-{head['generation']}",
                                payload_revision=revision, projection_required=False)
        except StorageError as error:
            if error.code == "STALE_GENERATION" and attempt < CAS_ATTEMPTS:
                continue
            raise
        written, files = storage.read_files(STATE_REPOSITORY, [STATE_PATH], branch=STATE_BRANCH)
        if files.get(STATE_PATH) != raw:
            raise StorageError("LEDGER_READBACK_MISMATCH", "The database-only ledger does not read back the written bytes.")
        return {"status": "written", "generation": written["generation"], "sha256": revision}
    raise StorageError("STALE_GENERATION", "The database-only ledger kept changing.")

