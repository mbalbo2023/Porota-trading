"""Positive and adversarial guards for lossless exact source-page storage."""
import gzip
import hashlib
import random
from unittest.mock import patch
import sqlite3

import pytest

from rc6_shadow_runtime import exact_page_storage as storage

def sha(value):
    return hashlib.sha256(value).hexdigest()


def fixed_page(seed=1):
    return random.Random(seed).randbytes(storage.PAGE_SIZE)


def target_pair():
    previous = fixed_page(1) + fixed_page(2)
    current = bytearray(previous)
    current[80:88] = b"newclock"
    current[5000:5008] = b"newvalue"
    return previous, bytes(current)


def decode_pack(pack, diagnostic, previous=None, depth=None):
    kwargs = {} if diagnostic["dependency_depth"] == 0 else {
        "previous_bytes": previous, "previous_depth": depth}
    return storage.decode_page_pack(pack, expected_pack_sha256=diagnostic["pack_sha256"],
                                    expected_target_sha256=diagnostic["target_sha256"], **kwargs)


@pytest.mark.parametrize("seed", (1, 2, 3, 13, 17))
def test_page_full_roundtrip_exact(seed):
    target = fixed_page(seed)
    record = storage.encode_page(target)
    assert record["codec"] == "RAW_GZIP_V1" and record["base_sha256"] is None
    assert storage.decode_page(record) == target


def test_dictionary_delta_is_smaller_and_binds_original_base():
    previous, target = target_pair()
    record = storage.encode_page(target[:4096], previous[:4096])
    full = storage.encode_page(target[:4096])
    assert record["codec"] == "ZLIB_EXACT_PAGE_DELTA_V1"
    assert record["base_sha256"] == sha(previous[:4096])
    assert len(record["encoded"]) < len(full["encoded"])
    assert storage.decode_page(record, previous[:4096]) == target[:4096]


@pytest.mark.parametrize("target", (None, "x", bytearray(4096), b"x" * 4095, b"x" * 4097))
def test_page_encoder_rejects_nonexact_bytes(target):
    with pytest.raises(ValueError):
        storage.encode_page(target)


@pytest.mark.parametrize("mutation", ("codec", "bytes", "empty", "length_bool", "length", "target_hash", "extra", "base"))
def test_page_closed_record_and_typed_fields(mutation):
    record = storage.encode_page(fixed_page())
    if mutation == "codec": record["codec"] = "UNKNOWN"
    elif mutation == "bytes": record["encoded"] = bytearray(record["encoded"])
    elif mutation == "empty": record["encoded"] = b""
    elif mutation == "length_bool": record["target_length"] = True
    elif mutation == "length": record["target_length"] = 4097
    elif mutation == "target_hash": record["target_sha256"] = "F" * 64
    elif mutation == "extra": record["authority"] = "invented"
    elif mutation == "base": record["base_sha256"] = "0" * 64
    with pytest.raises(ValueError): storage.decode_page(record)


@pytest.mark.parametrize("attack", ("truncated", "crc", "trailing", "second_gzip", "expansion", "target"))
def test_page_stream_and_original_target_are_verified(attack):
    target = fixed_page()
    record = storage.encode_page(target)
    wire = record["encoded"]
    if attack == "truncated": record["encoded"] = wire[:-1]
    elif attack == "crc": record["encoded"] = wire[:-8] + bytes([wire[-8] ^ 1]) + wire[-7:]
    elif attack == "trailing": record["encoded"] = wire + b"x"
    elif attack == "second_gzip": record["encoded"] = wire + gzip.compress(b"", mtime=0)
    elif attack == "expansion": record["encoded"] = gzip.compress(b"x" * 4097, mtime=0)
    elif attack == "target": record["target_sha256"] = sha(fixed_page(2))
    with pytest.raises(ValueError): storage.decode_page(record)


@pytest.mark.parametrize("attack", ("wrong_base", "missing_base", "base_hash", "adler", "trailing"))
def test_delta_verifies_base_and_complete_zlib_stream(attack):
    previous, target = target_pair()
    record = storage.encode_page(target[:4096], previous[:4096])
    base = previous[:4096]
    if attack == "wrong_base": base = fixed_page(3)
    elif attack == "missing_base": base = None
    elif attack == "base_hash": record["base_sha256"] = sha(fixed_page(3))
    elif attack == "adler": record["encoded"] = record["encoded"][:-1] + bytes([record["encoded"][-1] ^ 1])
    elif attack == "trailing": record["encoded"] += b"x"
    with pytest.raises(ValueError): storage.decode_page(record, base)


def test_one_packed_file_restores_all_original_pages_without_encoder():
    previous, target = target_pair()
    pack, diagnostic = storage.encode_page_pack(target, previous, previous_depth=4)
    assert diagnostic["dependency_depth"] == 5 and diagnostic["delta_pages"] == 2
    assert diagnostic["pack_bytes"] == len(pack) and diagnostic["index_bytes"] == 144
    with patch.object(storage.gzip, "compress", side_effect=AssertionError("ENCODER_FORBIDDEN")), \
         patch.object(storage.zlib, "compressobj", side_effect=AssertionError("ENCODER_FORBIDDEN")), \
         patch.object(storage, "encode_page", side_effect=AssertionError("ENCODER_FORBIDDEN")):
        assert decode_pack(pack, diagnostic, previous, 4) == target


@pytest.mark.parametrize("transition", ("grow", "shrink", "unrelated", "identical"))
def test_pack_handles_exact_original_file_length_transitions(transition):
    previous, target = target_pair()
    if transition == "grow": target += fixed_page(3)
    elif transition == "shrink": target = target[:4096]
    elif transition == "unrelated": target = fixed_page(4) + fixed_page(5)
    elif transition == "identical": target = previous
    pack, diagnostic = storage.encode_page_pack(target, previous)
    assert decode_pack(pack, diagnostic, previous, 0) == target
    assert diagnostic["page_count"] == len(target) // 4096
    if transition == "identical":
        assert diagnostic["dependency_depth"] == 0
        assert diagnostic["previous_sha256"] is None
        assert diagnostic["anchor_reason"] == "IDENTICAL_FILE_INDEPENDENT_BASE"


def test_hard_depth_limit_creates_full_anchors_and_never_uses_previous_for_restore():
    previous, depth, observed = None, 0, []
    for number in range(70):
        target = bytearray(fixed_page(9))
        target[80:88] = number.to_bytes(8, "big")
        pack, diagnostic = storage.encode_page_pack(bytes(target), previous, previous_depth=depth)
        restored = decode_pack(pack, diagnostic, previous, depth)
        assert restored == bytes(target)
        observed.append(diagnostic["dependency_depth"])
        if diagnostic["anchor_reason"] == "MAXIMUM_DEPENDENCY_DEPTH":
            assert diagnostic["dependency_depth"] == 0 and diagnostic["delta_pages"] == 0
        previous, depth = restored, diagnostic["dependency_depth"]
    assert max(observed) == 32 and observed[33] == observed[66] == 0


def test_owner_can_anchor_every_32_generations():
    previous, depth, observed = None, 0, []
    for number in range(65):
        target = bytearray(fixed_page(9)); target[80:88] = number.to_bytes(8, "big")
        pack, diagnostic = storage.encode_page_pack(bytes(target), previous, previous_depth=depth,
                                                    force_full=number % 32 == 0)
        restored = decode_pack(pack, diagnostic, previous, depth)
        assert restored == bytes(target)
        observed.append(diagnostic["dependency_depth"])
        previous, depth = restored, diagnostic["dependency_depth"]
    assert max(observed) == 31 and observed[0] == observed[32] == observed[64] == 0


@pytest.mark.parametrize("argument", ("depth_bool", "depth_negative", "depth_33", "force_string", "depth_without_previous"))
def test_encoder_rejects_invalid_chain_inputs(argument):
    previous, target = target_pair()
    kwargs = {"previous_depth": 0}
    if argument == "depth_bool": kwargs["previous_depth"] = True
    elif argument == "depth_negative": kwargs["previous_depth"] = -1
    elif argument == "depth_33": kwargs["previous_depth"] = 33
    elif argument == "force_string": kwargs["force_full"] = "yes"
    elif argument == "depth_without_previous": kwargs["previous_depth"] = 1; previous = None
    with pytest.raises(ValueError): storage.encode_page_pack(target, previous, **kwargs)


@pytest.mark.parametrize("attack", ("old_pack_pin", "old_target_pin", "wire_crc", "trailing", "truncated", "index_reorder",
                                  "reserved", "codec", "page_count", "page_size", "length_capacity", "depth_33", "base_header"))
def test_pack_rejects_wire_and_rehashed_structure_attacks(attack):
    previous, target = target_pair()
    pack, diagnostic = storage.encode_page_pack(target, previous)
    wire = bytearray(pack)
    expected_target, expected_pack = diagnostic["target_sha256"], diagnostic["pack_sha256"]
    if attack == "old_pack_pin": wire[-1] ^= 1
    elif attack == "old_target_pin": expected_target = sha(previous)
    elif attack == "wire_crc": wire[-1] ^= 1
    elif attack == "trailing": wire += b"x"
    elif attack == "truncated": wire = wire[:-1]
    elif attack == "index_reorder":
        a = storage.HEADER.size; n = storage.INDEX.size
        wire[a:a+2*n] = wire[a+n:a+2*n] + wire[a:a+n]
    elif attack in {"reserved", "codec"}:
        offset = storage.HEADER.size + (5 if attack == "reserved" else 4)
        wire[offset] = 99
    else:
        fields = list(storage.HEADER.unpack_from(wire))
        if attack == "page_count": fields[3] += 1
        elif attack == "page_size": fields[1] = 2048
        elif attack == "length_capacity": fields[2] = storage.MAX_SOURCE_BYTES + 4096
        elif attack == "depth_33": fields[4] = 33
        elif attack == "base_header": fields[6] = fields[5]
        wire[:storage.HEADER.size] = storage.HEADER.pack(*fields)
    wire = bytes(wire)
    if attack != "old_pack_pin": expected_pack = sha(wire)
    with pytest.raises(ValueError):
        storage.decode_page_pack(wire, expected_pack_sha256=expected_pack, expected_target_sha256=expected_target,
                                 previous_bytes=previous, previous_depth=0)


@pytest.mark.parametrize("attack", ("previous_bytes", "previous_depth", "depth_bool", "missing_previous"))
def test_pack_original_previous_file_and_verified_depth_are_required(attack):
    previous, target = target_pair()
    pack, diagnostic = storage.encode_page_pack(target, previous, previous_depth=3)
    depth = 3
    if attack == "previous_bytes": previous = fixed_page(4) + fixed_page(5)
    elif attack == "previous_depth": depth = 2
    elif attack == "depth_bool": depth = True
    elif attack == "missing_previous": previous = None
    with pytest.raises(ValueError): decode_pack(pack, diagnostic, previous, depth)


def test_source_member_hash_still_rejects_coordinated_pack_and_index_rebinding():
    previous, target = target_pair()
    changed = bytearray(target); changed[50] ^= 1
    pack, diagnostic = storage.encode_page_pack(bytes(changed), previous)
    with pytest.raises(ValueError, match="PACK_TARGET_BINDING_MISMATCH"):
        storage.decode_page_pack(pack, expected_pack_sha256=diagnostic["pack_sha256"],
                                 expected_target_sha256=sha(target), previous_bytes=previous, previous_depth=0)


def test_real_sqlite_images_preserve_exact_microsecond_revision_and_unicode():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA page_size=4096")
        connection.execute("CREATE TABLE source_events(id INTEGER PRIMARY KEY,identity TEXT,effective_at TEXT,known_at TEXT,price TEXT,detail TEXT)")
        identity = '["DLR/DIC26","FUTUROS","A3","ARS","A-24HS"]'
        rows = [(index, identity, "2026-10-05T13:35:00.000001+00:00", "2026-10-05T13:35:00.000002+00:00", "1579.5", "Córdoba · revisión íntegra")
                for index in range(128)]
        connection.executemany("INSERT INTO source_events VALUES(?,?,?,?,?,?)", rows)
        connection.commit()
        original = connection.serialize()
        connection.execute("UPDATE source_events SET known_at=?,price=? WHERE id=17", ("2026-10-05T13:35:00.000003+00:00", "1580.0"))
        connection.commit()
        revised = connection.serialize()
    finally:
        connection.close()
    base, base_diagnostic = storage.encode_page_pack(original)
    restored_base = decode_pack(base, base_diagnostic)
    delta, delta_diagnostic = storage.encode_page_pack(revised, restored_base)
    restored = decode_pack(delta, delta_diagnostic, restored_base, 0)
    assert restored_base == original and restored == revised
    assert delta_diagnostic["dependency_depth"] == 1
    readback = sqlite3.connect(":memory:")
    try:
        readback.deserialize(restored)
        readback.execute("PRAGMA query_only=ON")
        assert readback.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        actual = readback.execute("SELECT identity,effective_at,known_at,price,detail FROM source_events WHERE id=17").fetchone()
        assert actual == (identity, "2026-10-05T13:35:00.000001+00:00", "2026-10-05T13:35:00.000003+00:00", "1580.0", "Córdoba · revisión íntegra")
        assert readback.execute("SELECT COUNT(*) FROM source_events").fetchone()[0] == 128
    finally:
        readback.close()
