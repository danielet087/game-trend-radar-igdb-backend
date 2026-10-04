from copy import deepcopy
import base64
import gzip
import hashlib
import json

import pytest

from nintendo_backend.igdb import CollectionError
from nintendo_backend import persistence as P


def master():
    return {"schema_version": 1, "source": {"provider": "IGDB", "complete": True},
            "generated_at": "2026-10-04T15:00:00Z", "games": {
                "igdb:123": {"id": "igdb:123", "igdb_id": 123, "name_en": "A complete game",
                    "display_name": "完整遊戲 🎮", "raw": {"id": 123, "name": "A complete game"},
                    "platform_history": [{"checked_at": "2026-10-04T14:00:00Z", "known_platforms": [{"id": 167}],
                                          "release_records": [{"platform": "PS5", "date": "2027-03-01"}]}],
                    "platform_language_support": {"PS5": {"languages": {"tchinese": None, "english": True}}}}}}


def raw_bytes(document):
    return json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def envelope(raw, compressed=None):
    compressed = gzip.compress(raw, mtime=0) if compressed is None else compressed
    return {"schema_version": 1, "storage_format": "gzip-base64", "sha256": hashlib.sha256(raw).hexdigest(),
            "uncompressed_bytes": len(raw), "payload": base64.b64encode(compressed).decode()}


def test_small_and_legacy_master_pass_through_without_changing_complete_history():
    document = master()
    original = deepcopy(document)
    assert P.encode_master(document) is document
    assert P.decode_master(document) is document
    assert document == original


def test_encoding_threshold_round_trip_is_deterministic_and_preserves_unicode_and_history(monkeypatch):
    document = master()
    original = deepcopy(document)
    raw = raw_bytes(document)
    monkeypatch.setattr(P, "ENCODE_THRESHOLD_BYTES", len(raw))
    encoded = P.encode_master(document)
    assert encoded == P.encode_master(document) and set(encoded) == P.ENVELOPE_FIELDS
    assert encoded["sha256"] == hashlib.sha256(raw).hexdigest()
    assert encoded["uncompressed_bytes"] == len(raw)
    compressed = base64.b64decode(encoded["payload"])
    assert compressed[4:8] == b"\x00\x00\x00\x00" and compressed[9] == 255
    assert gzip.decompress(compressed) == raw
    assert P.decode_master(encoded) == document == original


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("schema_version", True), ("storage_format", "gzip"),
    ("sha256", "f" * 64), ("sha256", "A" * 64), ("sha256", "wrong"),
    ("uncompressed_bytes", 0), ("uncompressed_bytes", -1), ("uncompressed_bytes", True),
    ("uncompressed_bytes", 128 * 1024 * 1024 + 1), ("uncompressed_bytes", 1),
    ("payload", None), ("payload", ""), ("payload", "not base64!"),
])
def test_invalid_envelope_metadata_or_integrity_is_rejected(field, value):
    document = envelope(raw_bytes(master()))
    document[field] = value
    with pytest.raises(CollectionError, match="^invalid_previous_master$"):
        P.decode_master(document)


def test_extra_envelope_fields_and_noncanonical_base64_are_rejected():
    document = envelope(raw_bytes(master()))
    document["games"] = {}
    with pytest.raises(CollectionError):
        P.decode_master(document)
    document.pop("games")
    document["payload"] += "\n"
    with pytest.raises(CollectionError):
        P.decode_master(document)


@pytest.mark.parametrize("alter", [
    lambda value: value[:-1], lambda value: value + b"trailing junk",
    lambda value: value + gzip.compress(b"{}", mtime=0),
    lambda value: value[:-8] + b"\x00" * 8,
])
def test_truncated_corrupt_trailing_or_concatenated_gzip_is_rejected(alter):
    raw = raw_bytes(master())
    document = envelope(raw, alter(gzip.compress(raw, mtime=0)))
    with pytest.raises(CollectionError, match="invalid_previous_master"):
        P.decode_master(document)


def test_decoder_enforces_compressed_and_decompressed_limits_before_parsing(monkeypatch):
    raw = raw_bytes(master())
    document = envelope(raw)
    compressed = base64.b64decode(document["payload"])
    monkeypatch.setattr(P, "MAX_COMPRESSED_BYTES", len(compressed) - 1)
    with pytest.raises(CollectionError):
        P.decode_master(document)
    monkeypatch.setattr(P, "MAX_COMPRESSED_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(P, "MAX_UNCOMPRESSED_BYTES", len(raw) - 1)
    with pytest.raises(CollectionError):
        P.decode_master(document)


def test_claimed_small_length_cannot_expand_a_compression_bomb():
    document = envelope(b" " * 100_000)
    document["uncompressed_bytes"] = 32
    with pytest.raises(CollectionError):
        P.decode_master(document)


@pytest.mark.parametrize("raw", [b"[]", b"null", b"\xff", b'{"schema_version":1,"games":{},"value":NaN}',
    b'{"schema_version":1,"schema_version":1,"games":{}}',
    b'{"schema_version":1,"games":{},"storage_format":"gzip-base64"}'])
def test_decoded_payload_requires_valid_finite_json_object_without_duplicate_or_nested_envelope(raw):
    with pytest.raises(CollectionError):
        P.decode_master(envelope(raw))


@pytest.mark.parametrize("change", [
    lambda doc: doc.update(schema_version=True), lambda doc: doc.update(games=[]),
    lambda doc: doc["games"]["igdb:123"].update(igdb_id=True),
    lambda doc: doc["games"]["igdb:123"].update(igdb_id=-1),
    lambda doc: doc["games"]["igdb:123"].update(id="igdb:456"),
    lambda doc: doc["games"]["igdb:123"]["raw"].update(id=456),
    lambda doc: doc["games"].update({"igdb:456": {"igdb_id": 123}}),
])
def test_legacy_and_enveloped_master_share_strict_game_identity_and_schema_checks(change):
    document = master()
    change(document)
    for candidate in (document, envelope(raw_bytes(document))):
        with pytest.raises(CollectionError):
            P.decode_master(candidate)
    with pytest.raises(CollectionError):
        P.encode_master(document)


def test_encoder_rejects_nonfinite_and_oversized_raw_or_compressed_data(monkeypatch):
    document = master()
    document["bad"] = float("nan")
    with pytest.raises(CollectionError):
        P.encode_master(document)
    del document["bad"]
    monkeypatch.setattr(P, "MAX_UNCOMPRESSED_BYTES", len(raw_bytes(document)) - 1)
    with pytest.raises(CollectionError):
        P.encode_master(document)
    with pytest.raises(CollectionError):
        P.decode_master(document)
    monkeypatch.setattr(P, "MAX_UNCOMPRESSED_BYTES", 128 * 1024 * 1024)
    monkeypatch.setattr(P, "ENCODE_THRESHOLD_BYTES", 1)
    monkeypatch.setattr(P, "MAX_COMPRESSED_BYTES", 1)
    with pytest.raises(CollectionError):
        P.encode_master(document)
