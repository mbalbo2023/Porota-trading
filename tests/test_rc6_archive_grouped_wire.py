"""Cheap exact-wire factoring and native physical-cost differential guards.

Private synthetic byte images only: no financial ticks or Horizon executions.
Local wins never authenticate the originals' complete retention envelope.
"""
import gzip
import hashlib
import os
import struct
from types import SimpleNamespace
import zlib

import pytest

from rc6_shadow_runtime import archive_components as archive
from rc6_shadow_runtime import exact_binary_storage as binary
from rc6_shadow_runtime import exact_page_storage as page


def parts(packed, target, recovery="EXACT_PAGE_PACK"):
    return archive.grouped_wire_parts(packed, recovery,
        expected_pack_sha256=archive.sha(packed), expected_target_sha256=archive.sha(target))


def writer(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def verifier(root):
    root.mkdir(mode=0o700)
    owner = SimpleNamespace(archive_root=root, _read_only_paths=lambda directory: tuple(directory.iterdir()))
    return archive.ComponentArchive(owner)


def options(target, packed):
    record = {"sha256": archive.sha(target), "bytes": len(target), "recovery": "EXACT_PAGE_PACK",
              "pack_sha256": archive.sha(packed), "dependency_depth": 0,
              "base_generation_id": None, "base_receipt_digest": None}
    return {"projection.sqlite": [((packed,), record), (parts(packed, target), record)]}


def manifest(number):
    return {"generation_id": f"{number:032x}", "sequence": number}


def persist_and_decode(context, plan, target, *, number):
    recipe_wire, packed, name = plan
    if packed is not None:
        writer(context.root / name, packed)
    writer(context.root / (f"{number:032x}" + ".recipe.gz"), recipe_wire)
    recipe = archive.loads(archive.inflate(recipe_wire, maximum=archive.MAX_RECIPE_BYTES))
    components = context._indices(recipe)
    used = set()
    record = recipe["members"]["projection.sqlite"]
    wire = context._member(record, components, used, decode=False)
    assert used == set(range(len(components)))
    assert archive.sha(wire) == record["pack_sha256"]
    assert page.decode_page_pack(wire, expected_pack_sha256=record["pack_sha256"],
        expected_target_sha256=record["sha256"]) == target
    return recipe, wire


def actual_new_allocation(root, plan, name):
    root.mkdir(mode=0o700)
    recipe, packed, _ = plan
    writer(root / "recipe", recipe)
    if packed is not None:
        writer(root / name, packed)
    return sum(path.stat().st_blocks * 512 for path in root.iterdir())


@pytest.mark.parametrize("count", [1, 64, 65, 128, 129, 1024, page.MAX_PAGES])
def test_grouped_page_wire_is_exact_deterministic_and_at_most33_parts_at_all_format_bounds(count):
    target = b"\0" * (page.PAGE_SIZE * count)
    packed, _ = page.encode_page_pack(target)
    result = parts(packed, target)
    assert result == parts(packed, target)
    assert b"".join(result) == packed
    assert archive.sha(b"".join(result)) == archive.sha(packed)
    assert len(result) <= archive.MAX_GROUPED_WIRE_COMPONENTS == 33
    assert all(result)
    assert page.decode_page_pack(b"".join(result), expected_pack_sha256=archive.sha(packed),
                                expected_target_sha256=archive.sha(target)) == target
    assert 1202 * archive.MAX_GROUPED_WIRE_COMPONENTS == 39666 < archive.MAX_COMPONENTS


def test_binary_gzip_wire_stays_one_original_component_and_decodes_without_encoder(monkeypatch):
    original = bytes(range(256)) * 32
    packed, _ = binary.encode_binary_pack(original)
    result = parts(packed, original, "EXACT_BINARY_PACK")
    assert result == (packed,)
    monkeypatch.setattr(gzip, "compress", lambda *args, **kwargs: pytest.fail("RECOVERY_ENCODER_CALLED"))
    assert binary.decode_binary_pack(result[0], expected_pack_sha256=archive.sha(packed),
                                    expected_target_sha256=archive.sha(original)) == original
    with pytest.raises(ValueError, match="RECOVERY_UNSUPPORTED"):
        parts(packed, original, "NEW_CODEC")


def test_first_unique_layout_cannot_seed_factoring_by_silently_increasing_logical_cost(tmp_path):
    target = b"".join(hashlib.shake_256(str(number).encode()).digest(page.PAGE_SIZE) for number in range(129))
    packed, _ = page.encode_page_pack(target)
    context = verifier(tmp_path / "archive")
    alternatives = options(target, packed)
    baseline = context._plan({"projection.sqlite": alternatives["projection.sqlite"][0]}, {}, manifest(1), "a" * 64, {})
    grouped = context._plan({"projection.sqlite": alternatives["projection.sqlite"][1]}, {}, manifest(1), "a" * 64, {})
    assert sum(len(value) for value in grouped[:2] if value is not None) > sum(len(value) for value in baseline[:2] if value is not None)
    assert context._select_plan(alternatives, {}, manifest(1), "a" * 64) == baseline


def test_real_group_dedup_seeds_layout_then_eight_byte_change_reuses_exact_regions_and_never_worsens_cost(tmp_path, monkeypatch):
    group = b"".join(hashlib.shake_256(str(number).encode()).digest(page.PAGE_SIZE) for number in range(64))
    source = group * 3
    context = verifier(tmp_path / "archive")
    first, _ = page.encode_page_pack(source)
    first_options = options(source, first)
    baseline = context._plan({"projection.sqlite": first_options["projection.sqlite"][0]}, {}, manifest(1), "a" * 64, {})
    selected = context._select_plan(first_options, {}, manifest(1), "a" * 64)
    assert selected != baseline
    assert actual_new_allocation(tmp_path / "first-selected", selected, "pack") < actual_new_allocation(tmp_path / "first-baseline", baseline, "pack")
    assert sum(map(len, selected[:2])) < sum(map(len, baseline[:2]))
    first_recipe, wire = persist_and_decode(context, selected, source, number=1)
    assert wire == first
    target = source[:100] + bytes(value ^ 1 for value in source[100:108]) + source[108:]
    assert sum(a != b for a, b in zip(source, target)) == 8
    second, _ = page.encode_page_pack(target, force_full=True)
    second_options = options(target, second)
    catalog = context._catalog()
    baseline = context._plan({"projection.sqlite": second_options["projection.sqlite"][0]}, catalog, manifest(2), "b" * 64, {})
    selected = context._select_plan(second_options, catalog, manifest(2), "b" * 64)
    assert actual_new_allocation(tmp_path / "second-selected", selected, "pack") < actual_new_allocation(tmp_path / "second-baseline", baseline, "pack")
    assert sum(map(len, selected[:2])) < sum(map(len, baseline[:2]))
    second_recipe, wire = persist_and_decode(context, selected, target, number=2)
    assert wire == second
    assert set(first_recipe["packs"]) & set(second_recipe["packs"])
    monkeypatch.setattr(gzip, "compress", lambda *args, **kwargs: pytest.fail("RECOVERY_ENCODER_CALLED"))
    assert page.decode_page_pack(wire, expected_pack_sha256=archive.sha(second),
                                expected_target_sha256=archive.sha(target)) == target


def test_original_dictid_churn_across_every_page_does_not_create_stable_group_authority(tmp_path):
    dictionaries = (b"clock=a" + b"row-data" * 200, b"clock=b" + b"row-data" * 200)
    row = b'{"instrument":"ORIGINAL","payload":"row-data-row-data"}'
    streams = []
    for dictionary in dictionaries:
        encoder = zlib.compressobj(level=1, zdict=dictionary)
        stream = encoder.compress(row) + encoder.flush()
        decoder = zlib.decompressobj(zdict=dictionary)
        assert decoder.decompress(stream) == row and decoder.eof
        streams.append(stream)
    assert streams[0][2:6] != streams[1][2:6]  # exact original DICTID, untouched
    assert streams[0][6:] == streams[1][6:]
    sources = []
    for stream in streams:
        # Two4-byte DICTID fields per original page vary. All decoded rows
        # stay byte-identical; no archive operation normalizes either field.
        prefix = stream * 2
        sources.append(b"".join(prefix + hashlib.shake_256(str(number).encode()).digest(page.PAGE_SIZE-len(prefix))
                                for number in range(192)))
    packed = [page.encode_page_pack(source)[0] for source in sources]
    grouped = [parts(wire, source) for wire, source in zip(packed, sources)]
    assert not {archive.sha(value) for value in grouped[0]} & {archive.sha(value) for value in grouped[1]}
    context = verifier(tmp_path / "archive")
    alternatives = options(sources[1], packed[1])
    baseline = context._plan({"projection.sqlite": alternatives["projection.sqlite"][0]}, {}, manifest(1), "a" * 64, {})
    assert context._select_plan(alternatives, {}, manifest(1), "a" * 64) == baseline


@pytest.mark.parametrize("attack", ["pack_hash", "reserved_index", "trailing", "gzip_crc", "payload_length"])
def test_factored_wire_never_bypasses_existing_sha_typed_index_crc_or_exact_eof(attack):
    target = bytes(range(256)) * 32
    packed, _ = page.encode_page_pack(target)
    value = bytearray(packed)
    expected = archive.sha(packed)
    if attack == "reserved_index":
        value[page.HEADER.size + 5] = 1
    elif attack == "trailing":
        value.extend(b"trailing")
    elif attack == "payload_length":
        value[page.HEADER.size + 6:page.HEADER.size + 8] = struct.pack(">H", 65535)
    else:
        value[-8] ^= 1
    damaged = bytes(value)
    if attack != "pack_hash":
        expected = archive.sha(damaged)
    with pytest.raises(ValueError, match="PACK_|PAGE_"):
        result = archive.grouped_wire_parts(damaged, "EXACT_PAGE_PACK",
            expected_pack_sha256=expected, expected_target_sha256=archive.sha(target))
        page.decode_page_pack(b"".join(result), expected_pack_sha256=expected,
                              expected_target_sha256=archive.sha(target))


@pytest.mark.parametrize("reason", ["RETENTION_COMPONENT_INDEX_CAPACITY_REACHED", "RETENTION_COMPONENT_PACK_CAPACITY_REACHED", "RETENTION_RECIPE_CAPACITY_REACHED"])
def test_optional_factor_capacity_overflow_preserves_admissible_original_plan(tmp_path, monkeypatch, reason):
    context = verifier(tmp_path / "archive")
    baseline = (b"original recipe", b"original pack", "original")
    def planned(options, *args):
        if options["projection.sqlite"][1] == "original":
            return baseline
        raise ValueError(reason)
    monkeypatch.setattr(context, "_plan", planned)
    assert context._select_plan({"projection.sqlite": [((b"x",), "original"), ((b"y",), "proposal")]}, {}, manifest(1), "a" * 64) == baseline


def test_optional_layout_custody_failure_is_fatal_even_with_valid_baseline(tmp_path, monkeypatch):
    context = verifier(tmp_path / "archive")
    def planned(options, *args):
        if options["projection.sqlite"][1] == "original":
            return b"recipe", b"pack", "name"
        raise ValueError("RETENTION_COMPONENT_HASH_COLLISION")
    monkeypatch.setattr(context, "_plan", planned)
    with pytest.raises(ValueError, match="HASH_COLLISION"):
        context._select_plan({"projection.sqlite": [((b"x",), "original"), ((b"y",), "proposal")]}, {}, manifest(1), "a" * 64)


@pytest.mark.parametrize("tradeoff", ["logical_worse", "physical_worse"])
def test_planner_requires_both_actual_logical_and_rounded_whole_file_costs_not_to_worsen(tmp_path, monkeypatch, tradeoff):
    context = verifier(tmp_path / "archive")
    unit = os.statvfs(context.root).f_frsize
    if tradeoff == "logical_worse":
        baseline = (b"r" * (unit + 1), b"p" * (unit + 1), "original")
        proposal = (b"r" * (unit // 4), b"p" * (2 * unit - 1), "proposal")
    else:
        baseline = (b"r" * unit, b"p" * unit, "original")
        proposal = (b"r" * (unit + 1), b"p", "proposal")
    monkeypatch.setattr(context, "_plan", lambda selected, *args: baseline if selected["projection.sqlite"][1] == "original" else proposal)
    assert context._select_plan({"projection.sqlite": [((b"x",), "original"), ((b"y",), "proposal")]}, {}, manifest(1), "a" * 64) == baseline
