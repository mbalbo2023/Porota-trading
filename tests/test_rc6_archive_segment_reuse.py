"""Exact original-byte codec, admission and mixed archive closure guards."""
from datetime import timedelta
import gzip
import hashlib
import json
import os
import shutil
import struct
import sys

import pytest

from rc6_shadow_runtime import archive_components as components
from rc6_shadow_runtime import exact_binary_storage as binary
from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_dynamic_universe.common import digest
from tests.test_issue465_generations import publish
from tests.test_rc6_component_archive import bytes_at, members, policy, recipe, snapshot, tree_custody
from tests.test_rc6_shadow_runtime_wiring import PRE


def decode(pack, target, previous=None, depth=None):
    return binary.decode_binary_pack(pack, expected_pack_sha256=components.sha(pack),
        expected_target_sha256=components.sha(target), previous_bytes=previous, previous_depth=depth)


@pytest.mark.parametrize("length", [1, 23, 24, 25, 63, 64, 65, 255, 256, 257, 8192])
def test_copy_literal_last_unmatched_anchor_and_exact_eof(length):
    previous = bytes((index * 17 + 3) % 256 for index in range(length))
    target = bytes((index * 19 + 7) % 256 for index in range(length))
    packed, info = binary.encode_binary_pack(target, previous)
    assert decode(packed, target, previous if info["dependency_depth"] else None,
                  0 if info["dependency_depth"] else None) == target


@pytest.mark.parametrize("bad", [None, "wire", bytearray(b"x"), b""])
def test_binary_original_input_types_fail_closed(bad):
    with pytest.raises(ValueError, match="BOUNDED_ORIGINAL_BYTES_REQUIRED"):
        binary.encode_binary_pack(bad)


@pytest.mark.parametrize("bad", [True, -1, 33, 1.0, None])
def test_binary_previous_depth_types_are_explicit(bad):
    with pytest.raises(ValueError, match="DEPTH_INVALID"):
        binary.encode_binary_pack(b"target", b"base", previous_depth=bad)


def test_binary_original_gzip_wire_is_copied_not_reencoded_and_encoder_forbidden_on_restore(monkeypatch):
    original = gzip.compress(bytes(range(256)) * 64, mtime=42)
    target = original[:100] + b"changed" + original[107:]
    packed, info = binary.encode_binary_pack(target, original)
    monkeypatch.setattr(gzip, "compress", lambda *args, **kwargs: pytest.fail("RESTORE_ENCODER_CALLED"))
    monkeypatch.setattr(binary, "encode_binary_pack", lambda *args, **kwargs: pytest.fail("RESTORE_ENCODER_CALLED"))
    assert decode(packed, target, original if info["dependency_depth"] else None,
                  0 if info["dependency_depth"] else None) == target


def test_binary_depth32_bound_and_explicit_anchor_do_not_change_original_bytes():
    source = bytes(range(256)) * 32
    previous, depth = None, 0
    for number in range(34):
        target = source + struct.pack("!I", number)
        packed, info = binary.encode_binary_pack(target, previous, previous_depth=depth)
        assert info["dependency_depth"] == (number if number <= 32 else 0)
        assert decode(packed, target, previous if info["dependency_depth"] else None,
                      depth if info["dependency_depth"] else None) == target
        previous, depth = target, info["dependency_depth"]
    packed, info = binary.encode_binary_pack(source, previous, previous_depth=depth, force_full=True)
    assert info["dependency_depth"] == 0 and decode(packed, source) == source


def xor_originals():
    previous = b"".join(hashlib.sha256(str(number).encode()).digest() for number in range(4096))
    target = bytearray(previous)
    for cursor in range(0, len(target), 64):
        target[cursor] ^= cursor // 64 % 255 + 1
    return previous, bytes(target)


def test_binary_same_length_xor_preserves_original_bytes_and_decoder_never_encodes(monkeypatch):
    previous, target = xor_originals()
    packed, info = binary.encode_binary_pack(target, previous, allow_xor=True)
    assert info["xor_bytes"] == len(target) and info["dependency_depth"] == 1
    monkeypatch.setattr(gzip, "compress", lambda *args, **kwargs: pytest.fail("RESTORE_ENCODER_CALLED"))
    monkeypatch.setattr(binary, "encode_binary_pack", lambda *args, **kwargs: pytest.fail("RESTORE_ENCODER_CALLED"))
    assert decode(packed, target, previous, 0) == target


@pytest.mark.parametrize("length", [1, 65535, 65536, 65537, 131073])
def test_xor_block_boundaries_match_exact_inverse(length):
    previous = (bytes(range(256)) * ((length + 255) // 256))[:length]
    target = bytes(reversed(previous))
    delta = binary._xor_equal_bytes(target, previous)
    assert delta == bytes(a ^ b for a, b in zip(target, previous))
    assert binary._xor_equal_bytes(delta, previous) == target


@pytest.mark.parametrize("attack", ["zero_depth", "wrong_length", "multiple_operations",
                                    "wrong_base_hash", "wrong_base_length", "missing_base",
                                    "wrong_result_hash", "truncated_delta"])
def test_resealed_xor_shapes_bases_and_original_sha_fail_closed(attack):
    previous, target = xor_originals()
    delta = binary._xor_equal_bytes(target, previous)
    depth, count, length = 1, 1, len(target)
    operation = binary.LITERAL.pack(2, length) + delta
    if attack == "zero_depth":
        depth = 0
    elif attack == "wrong_length":
        operation = binary.LITERAL.pack(2, length - 1) + delta
    elif attack == "multiple_operations":
        count = 2
    elif attack == "wrong_base_hash":
        previous = bytes(reversed(previous))
    elif attack == "wrong_base_length":
        previous = previous[:-1]
    elif attack == "missing_base":
        previous = None
    elif attack == "wrong_result_hash":
        operation = binary.LITERAL.pack(2, length) + bytes(reversed(delta))
    elif attack == "truncated_delta":
        operation = operation[:-1]
    original, _ = xor_originals()
    wire = binary.HEADER.pack(binary.MAGIC, length, count, depth,
        bytes.fromhex(components.sha(target)), bytes.fromhex(components.sha(original)) if depth else binary.ZERO_HASH)
    packed = gzip.compress(wire + operation, mtime=0)
    with pytest.raises(ValueError, match="BINARY_"):
        decode(packed, target, previous, 0 if previous is not None else None)


def test_xor_is_opt_in_length_equal_and_original_depth32_anchor_remains_independent():
    previous, target = xor_originals()
    packed, legacy = binary.encode_binary_pack(target, previous)
    assert legacy["xor_bytes"] == 0 and legacy["dependency_depth"] == 0
    with pytest.raises(ValueError, match="BINARY_INDEPENDENT_BASE_UNEXPECTED"):
        decode(packed, target, previous, 0)
    assert decode(packed, target) == target
    for changed, options in ((target + b"x", {}), (target, {"force_full": True}),
                             (target, {"previous_depth": 32})):
        packed, info = binary.encode_binary_pack(changed, previous, allow_xor=True, **options)
        assert info["xor_bytes"] == 0
        if options:
            assert info["dependency_depth"] == 0
        assert decode(packed, changed, previous if info["dependency_depth"] else None,
                      0 if info["dependency_depth"] else None) == changed
    with pytest.raises(ValueError, match="ALLOW_XOR_BOOLEAN_REQUIRED"):
        binary.encode_binary_pack(target, previous, allow_xor=1)


def test_archive_xor_proposals_are_only_for_authenticated_original_projection(tmp_path, monkeypatch):
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    actual, allowances = components.encode_binary_pack, []
    def observed(raw, previous=None, **kwargs):
        allowances.append((raw.startswith(b"SQLite format 3\x00"), kwargs["allow_xor"]))
        return actual(raw, previous, **kwargs)
    monkeypatch.setattr(components, "encode_binary_pack", observed)
    for number in range(1, 3):
        with EvidenceFiles(root) as files:
            cut = publish(files, number, as_of=PRE + timedelta(seconds=30 * number))
        receipt = policy(root, archive).archive_generation(root / ("gen-" + cut["pointer"]["generation_id"]))
        assert policy(root, archive).restore_generation(receipt["generation_id"])["manifest"] == cut["manifest"]
    assert allowances.count((True, True)) == 2 and allowances.count((False, False)) == 2
    assert len(allowances) == 4


@pytest.mark.parametrize("attack", ["wrong_sha", "trailing_gzip", "unknown_opcode", "copy_bounds", "instruction_truncation"])
def test_resealed_binary_wire_and_typed_instruction_corruption_fail_closed(attack):
    target = bytes(range(256)) * 32
    packed, _ = binary.encode_binary_pack(target)
    expected = components.sha(packed)
    if attack == "wrong_sha":
        packed = packed[:-1] + bytes([packed[-1] ^ 1])
    elif attack == "trailing_gzip":
        packed += gzip.compress(b"trailing", mtime=0)
        expected = components.sha(packed)
    else:
        wire = bytearray(gzip.decompress(packed))
        if attack == "unknown_opcode":
            wire[binary.HEADER.size] = 255
        elif attack == "instruction_truncation":
            wire = wire[:-1]
        else:
            wire[binary.HEADER.size:binary.HEADER.size + binary.COPY.size] = binary.COPY.pack(1, binary.MAX_MEMBER_BYTES, 1)
        packed = gzip.compress(bytes(wire), mtime=0)
        expected = components.sha(packed)
    with pytest.raises(ValueError, match="BINARY_"):
        binary.decode_binary_pack(packed, expected_pack_sha256=expected,
                                  expected_target_sha256=components.sha(target))


@pytest.fixture(scope="module")
def deepest_binary_chain(tmp_path_factory):
    base = tmp_path_factory.mktemp("native-original-binary-depth32")
    root, archive = base / "shadow", base / "archive"
    for number in range(1, 35):
        with EvidenceFiles(root) as files:
            cut = publish(files, number, as_of=PRE + timedelta(seconds=30 * number))
        if number == 32:
            continue
        last = policy(root, archive).archive_generation(root / ("gen-" + cut["pointer"]["generation_id"]))
    final = recipe(archive, last)["members"]["projection.sqlite"]
    assert final["recovery"] == "EXACT_BINARY_PACK" and final["dependency_depth"] == 32
    return root, archive, cut, last


def test_native_binary_depth32_iterative_restore_has_one_pack_and_exact_originals(deepest_binary_chain, monkeypatch):
    root, archive, cut, receipt = deepest_binary_chain
    original = members(root / ("gen-" + receipt["generation_id"]))
    protected = (root, root.with_name(root.name + ".authority"), archive)
    before = {str(path): tree_custody(path) for path in protected}
    actual_class, actual_decode = components.ComponentArchive, components.decode_binary_pack
    instances, decoded, retained = [], [], []
    class ObservedArchive(actual_class):
        def __init__(self, *args):
            super().__init__(*args)
            instances.append(self)
    def observed(*args, **kwargs):
        verifier = instances[-1]
        frame, nesting = sys._getframe(), 0
        while frame is not None:
            nesting += frame.f_code is actual_class._original.__code__
            frame = frame.f_back
        assert nesting == 1 and not verifier.projections
        resident = sum(len(raw) for raw, _ in verifier.packs.values())
        assert resident <= components.MAX_PACK_BYTES
        retained.append(resident)
        result = actual_decode(*args, **kwargs)
        decoded.append(kwargs["previous_depth"])
        return result
    monkeypatch.setattr(components, "ComponentArchive", ObservedArchive)
    monkeypatch.setattr(components, "decode_binary_pack", observed)
    monkeypatch.setattr(gzip, "compress", lambda *args, **kwargs: pytest.fail("RESTORE_ENCODER_CALLED"))
    result = policy(root, archive).restore_generation(receipt["generation_id"])
    assert result["members"] == original and result["manifest"] == cut["manifest"]
    assert decoded == [None, *range(32)] and len(retained) == 33
    assert all(not instance.projections for instance in instances)
    assert before == {str(path): tree_custody(path) for path in protected}


def test_native_binary_restore_reopens_each_pack_and_fails_mid_call_tamper(deepest_binary_chain, tmp_path, monkeypatch):
    original_root, original_archive, _, receipt = deepest_binary_chain
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    shutil.copytree(original_root, root)
    shutil.copytree(original_archive, archive)
    shutil.copytree(original_root.with_name(original_root.name + ".authority"), root.with_name(root.name + ".authority"))
    protected = snapshot(root)
    target = archive / recipe(archive, receipt)["packs"][-1]
    actual, mutated = components.inspect_binary_pack, []
    def inspected(*args, **kwargs):
        metadata = actual(*args, **kwargs)
        if metadata["dependency_depth"] == 0 and not mutated:
            wire = bytes_at(target)
            target.write_bytes(wire[:-1] + bytes([wire[-1] ^ 1]))
            mutated.append(target.name)
        return metadata
    monkeypatch.setattr(components, "inspect_binary_pack", inspected)
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        policy(root, archive).restore_generation(receipt["generation_id"])
    assert mutated and snapshot(root) == protected


def test_next_archive_after_skipped_modulo_depth32_anchors_all_codecs(deepest_binary_chain, tmp_path):
    original_root, original_archive, _, previous = deepest_binary_chain
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    shutil.copytree(original_root, root)
    shutil.copytree(original_archive, archive)
    shutil.copytree(original_root.with_name(original_root.name + ".authority"), root.with_name(root.name + ".authority"))
    assert components.ComponentArchive(policy(root, archive))._common_dependency_depth(previous) == 32
    with EvidenceFiles(root) as files:
        cut = publish(files, 35, as_of=PRE + timedelta(seconds=35 * 30))
    receipt = policy(root, archive).archive_generation(root / ("gen-" + cut["pointer"]["generation_id"]))
    value = recipe(archive, receipt)
    assert all(record.get("dependency_depth", 0) == 0 for record in value["members"].values())
    assert policy(root, archive).restore_generation(receipt["generation_id"])["manifest"] == cut["manifest"]


def test_common_depth_counts_edges_with_unequal_member_depths(monkeypatch, tmp_path):
    (tmp_path / "shadow").mkdir(mode=0o700)
    owner = policy(tmp_path / "shadow", tmp_path / "archive")
    verifier = components.ComponentArchive(owner)
    receipts, recipes = {}, {}
    for number in range(33):
        ident = f"{number + 1:032x}"
        receipt = {"generation_id": ident, "receipt_sequence": number + 1}
        receipts[ident] = receipt
        if number:
            prior = receipts[f"{number:032x}"]
            # Alternating independent member resets leave each member chain
            # short, while the common archive graph still has32realedges.
            dependent = {"recovery": "EXACT_BINARY_PACK", "dependency_depth": number % 4 + 1,
                "base_generation_id": prior["generation_id"], "base_receipt_digest": digest(prior)}
            independent = {"recovery": "EXACT_BINARY_PACK", "dependency_depth": 0,
                "base_generation_id": None, "base_receipt_digest": None}
            records = {"projection.sqlite": independent if number % 2 else dependent,
                       "checkpoint.json.gz": dependent if number % 2 else independent}
        else:
            records = {"checkpoint.json.gz": {"recovery": "SOURCE_GZIP_FRAME_CONCAT"}}
        recipes[ident] = {"generation_id": ident, "members": records}
    monkeypatch.setattr(verifier, "_recipe", lambda receipt: recipes[receipt["generation_id"]])
    monkeypatch.setattr(owner, "_read_control", lambda path: (receipts[path.name.split('.')[0]], None))
    assert verifier._common_dependency_depth(receipts[f"{33:032x}"]) == 32


def legacy_unprojected_cut(root, number, as_of, blob):
    """Supported V1 four-member input; no new financial/runtime authority."""
    ident = f"{number:032x}"
    directory = root / ("gen-" + ident)
    directory.mkdir(mode=0o700, parents=True)
    base = {"mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED", "as_of": as_of.isoformat()}
    payloads = {"report": {**base, "number": number},
                "checkpoint": {**base, "blob": blob, "number": number},
                "status": {**base, "number": number}}
    names = {"report": "report.json.gz", "checkpoint": "checkpoint.json.gz", "status": "status.json"}
    records, originals = {}, {}
    for role, payload in payloads.items():
        raw = components.canonical({"digest": digest(payload), "payload": payload})
        if role != "status":
            raw = gzip.compress(raw, mtime=0, compresslevel=1)
        name = names[role]
        originals[name] = raw
        records[role] = {"name": name, "sha256": components.sha(raw), "payload_digest": digest(payload)}
    manifest = {"schema": "rc6.shadow-evidence-generation.v1", "generation_id": ident,
                "sequence": number, "as_of": as_of.isoformat(), "files": records}
    originals["manifest.json"] = components.canonical(manifest)
    for name, raw in originals.items():
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
    return directory, originals


def test_checkpoint_binary_dependency_without_projection_survives_gc_and_restores_exact_gzip(tmp_path):
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    root.mkdir(mode=0o700)
    blob = "".join(hashlib.sha256(str(number).encode()).hexdigest() for number in range(2048))
    owner, cuts = policy(root, archive), []
    for number in range(1, 4):
        directory, originals = legacy_unprojected_cut(root, number, PRE + timedelta(seconds=30 * number), blob)
        receipt = owner.archive_generation(directory)
        cuts.append((directory, originals, receipt))
    record = recipe(archive, cuts[-1][2])["members"]["checkpoint.json.gz"]
    assert record["recovery"] == "EXACT_BINARY_PACK" and record["dependency_depth"] == 2
    value = recipe(archive, cuts[-1][2])
    assert "projection.sqlite" not in value["members"]
    graph = components.ComponentArchive(owner).dependency_graph([cuts[-1][2]])
    assert set(graph) == {receipt["generation_id"] for _, _, receipt in cuts}
    assert graph[cuts[-1][2]["generation_id"]]["depth"] == 2
    future, _ = legacy_unprojected_cut(root, 32, PRE + timedelta(hours=11), blob)
    owner.archive_generation(future)
    # Only generated Source fixtures are removed. Once those source pins are
    # gone, a supplied recovery pin must keep every CP-onlytransitivebase.
    for directory, _, receipt in cuts:
        owner._remove_flat_directory(directory, complete=True)
        (root / ("archive-ack-" + receipt["generation_id"] + ".json")).unlink()
    result = owner.maintain_archive(pinned=[cuts[-1][2]["generation_id"]])
    assert result["expired_generations"] == 0
    for _, originals, receipt in cuts:
        assert owner.restore_generation(receipt["generation_id"])["members"] == originals
