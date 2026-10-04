"""Lossless, bounded transport for the complete native-game candidate ledger.

The collector still works with the expanded master. Only large persisted
snapshots use this envelope; public catalogs and receipts remain ordinary JSON.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import io
import json
import re
import zlib

from .igdb import CollectionError

ENCODE_THRESHOLD_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
STORAGE_FORMAT = "gzip-base64"
ENVELOPE_FIELDS = {"schema_version", "storage_format", "sha256", "uncompressed_bytes", "payload"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _invalid():
    raise CollectionError("invalid_previous_master")


def _validate_master(document):
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or "storage_format" in document
            or not isinstance(document.get("games"), dict)):
        _invalid()
    for key, row in document["games"].items():
        if (not isinstance(row, dict) or type(row.get("igdb_id")) is not int
                or row["igdb_id"] <= 0 or key != "igdb:" + str(row["igdb_id"])):
            _invalid()
        if "id" in row and row["id"] != key:
            _invalid()
        if "raw" in row:
            raw = row["raw"]
            if (not isinstance(raw, dict) or ("id" in raw
                    and (type(raw["id"]) is not int or raw["id"] != row["igdb_id"]))):
                _invalid()
    return document


def _compact_bytes(document):
    try:
        result = json.dumps(document, ensure_ascii=False, allow_nan=False,
                            separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _invalid()
    if len(result) > MAX_UNCOMPRESSED_BYTES:
        _invalid()
    return result


def encode_master(document) -> dict:
    """Keep small masters unchanged; encode large complete ledgers deterministically."""
    _validate_master(document)
    raw = _compact_bytes(document)
    if len(raw) < ENCODE_THRESHOLD_BYTES:
        return document
    output = io.BytesIO()
    # GzipFile fixes both mtime and the filename/OS header across Python versions.
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as handle:
        handle.write(raw)
    compressed = output.getvalue()
    if len(compressed) > MAX_COMPRESSED_BYTES:
        _invalid()
    return {"schema_version": 1, "storage_format": STORAGE_FORMAT,
            "sha256": hashlib.sha256(raw).hexdigest(), "uncompressed_bytes": len(raw),
            "payload": base64.b64encode(compressed).decode("ascii")}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("nonfinite_json")


def decode_master(document) -> dict:
    """Decode one bounded gzip member, validating its bytes and all game identities."""
    if not isinstance(document, dict):
        _invalid()
    if "storage_format" not in document:
        _validate_master(document)
        _compact_bytes(document)  # The legacy path obeys the same raw-size/JSON bound.
        return document
    if (set(document) != ENVELOPE_FIELDS or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or document.get("storage_format") != STORAGE_FORMAT
            or not isinstance(document.get("sha256"), str) or not _SHA256.fullmatch(document["sha256"])
            or type(document.get("uncompressed_bytes")) is not int
            or not 0 < document["uncompressed_bytes"] <= MAX_UNCOMPRESSED_BYTES
            or not isinstance(document.get("payload"), str)):
        _invalid()
    payload, expected = document["payload"], document["uncompressed_bytes"]
    if not payload or len(payload) > 4 * ((MAX_COMPRESSED_BYTES + 2) // 3):
        _invalid()
    try:
        compressed = base64.b64decode(payload, validate=True)
        if (len(compressed) > MAX_COMPRESSED_BYTES
                or base64.b64encode(compressed).decode("ascii") != payload):
            _invalid()
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        # Never call flush: its length argument is not an allocation/output bound.
        raw = inflater.decompress(compressed, expected + 1)
        if (len(raw) != expected or not inflater.eof or inflater.unconsumed_tail
                or inflater.unused_data or hashlib.sha256(raw).hexdigest() != document["sha256"]):
            _invalid()
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
    except (binascii.Error, zlib.error, TypeError, ValueError, UnicodeError, RecursionError):
        _invalid()
    return _validate_master(decoded)
