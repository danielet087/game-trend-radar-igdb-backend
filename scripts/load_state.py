"""Load the complete candidate ledger from the configured authoritative store."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import requests

from nintendo_backend.persistence import decode_master
from scripts.publish import FRONTEND, GitHub, PublishError


def load(output: Path, *, storage=None, client=None, get=requests.get):
    if os.environ.get("RADAR_DATABASE_PRIMARY") == "1":
        from scripts.state_store import digest, read_ledger
        if storage is None:
            from radar_core.storage import primary as storage
        # An activated database must contain the imported ledger: the frontend
        # copy while it exists, then the database-only source. Missing data is
        # not a first run and must never fall back to Git or an empty master.
        stage, raw = read_ledger(storage)
        print(f"NINTENDO_STATE load stage={stage} bytes={len(raw)} sha256={digest(raw)}")
    else:
        client = client or GitHub(os.environ.get("FRONTEND_REPO_TOKEN", ""))
        head, _ = client.head(FRONTEND)
        try:
            metadata = client.request("GET", f"/repos/{FRONTEND}/contents/data/nintendo_master.json?ref={head}")
        except PublishError as error:
            # The ledger lives in the database-only source; a missing Git copy
            # is never a first run that would start an empty ledger.
            if error.status == 404:
                raise ValueError("The candidate ledger is not in Git; restore it before a Git-mode run") from None
            raise
        response = get(f"https://raw.githubusercontent.com/{FRONTEND}/{head}/data/nintendo_master.json",
                       timeout=30, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Previous candidate ledger is unavailable")
        raw = response.content
        if hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() != metadata.get("sha"):
            raise ValueError("Candidate ledger does not match its immutable source")
    decode_master(json.loads(raw.decode("utf-8")))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("input/nintendo_master.json"))
    args = parser.parse_args(argv)
    try:
        load(args.output)
    except Exception:
        print("Candidate state could not be verified; no fallback or collection was performed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
