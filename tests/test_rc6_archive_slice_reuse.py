"""Economic CAS layout proofs, NOT the original financial Horizon/G5 run."""
from datetime import date
import hashlib
import gzip
import os
import struct

import pytest

from rc6_shadow_runtime import archive_components as archive
from rc6_shadow_runtime import exact_page_storage as page
from rc6_shadow_runtime.archive_physical_model import horizon_schedule
from tests.test_rc6_archive_grouped_wire import (
    actual_new_allocation, manifest, options, persist_and_decode, verifier, writer,
)
from tests.test_rc6_component_archive import native, policy, members, reseal_recipe, tree_custody


def slice_options(target, packed):
    result = options(target, packed)
    record = result["projection.sqlite"][0][1]
    result["projection.sqlite"].append((archive.exact_wire_slices(packed), record))
    return result


def raw_source(pages=32):
    return hashlib.shake_256(b"original-private-byte-fixture").digest(pages * page.PAGE_SIZE)


def test_exact_bounded_slices_recover_shifted_and_boundary_free_wire():
    for raw in (b"x", b"x" * 65537, raw_source(), b"\0" * 524289):
        parts = archive.exact_wire_slices(raw)
        assert parts == archive.exact_wire_slices(raw)
        assert b"".join(parts) == raw
        assert 1 <= len(parts) <= archive.MAX_WIRE_SLICES == 128
    for raw in (None, b"", bytearray(b"x"), "wire"):
        with pytest.raises(ValueError, match="BOUNDED_ORIGINAL_WIRE"):
            archive.exact_wire_slices(raw)


def test_parent_slice_index_uses_immutable_views_without_copying_each_wire(tmp_path, monkeypatch):
    context = verifier(tmp_path / "archive")
    source = raw_source()
    packed, _ = page.encode_page_pack(source)
    plan = context._plan({"projection.sqlite": options(source, packed)["projection.sqlite"][0]},
                         {}, manifest(1), "a" * 64, {})
    recipe, _ = persist_and_decode(context, plan, source, number=1)
    ranges, seen = archive._exact_wire_ranges, []
    def observed(raw):
        assert type(raw) is memoryview and raw.readonly and raw.c_contiguous
        seen.append(len(raw))
        return ranges(raw)
    monkeypatch.setattr(archive, "_exact_wire_ranges", observed)
    monkeypatch.setattr(archive, "exact_wire_slices", lambda *args: pytest.fail("PARENT_WIRE_COPIED"))
    aliases = context._slice_catalog(context._catalog(), recipe)
    assert seen == [len(packed)]
    assert all(archive.sha(packed[start:end]) in aliases for start, end in ranges(packed))
    for mutable in (memoryview(bytearray(b"mutable-wire")),
                    memoryview(bytearray(b"mutable-wire")).toreadonly()):
        with pytest.raises(ValueError, match="BOUNDED_ORIGINAL_WIRE"):
            ranges(mutable)


def test_monolithic_v3_seed_reuses_v4_ranges_without_rewriting_parent_and_cost_improves(tmp_path):
    source = raw_source()
    context = verifier(tmp_path / "archive")
    first, _ = page.encode_page_pack(source)
    first_options = options(source, first)
    initial = context._plan({"projection.sqlite": first_options["projection.sqlite"][0]}, {}, manifest(1), "a" * 64, {})
    prior_recipe, _ = persist_and_decode(context, initial, source, number=1)
    assert prior_recipe["schema"] == archive.RECIPE_SCHEMA
    prior_path = context.root / initial[2]
    before = prior_path.stat()
    target = source[:100] + bytes(value ^ 1 for value in source[100:108]) + source[108:]
    second, _ = page.encode_page_pack(target, force_full=True)
    catalog = context._catalog()
    aliases = context._slice_catalog(catalog, prior_recipe)
    proposals = slice_options(target, second)
    baseline = context._plan({"projection.sqlite": proposals["projection.sqlite"][0]}, catalog, manifest(2), "b" * 64, {})
    selected = context._select_plan(proposals, catalog, manifest(2), "b" * 64, slice_catalog=aliases)
    assert selected != baseline
    assert actual_new_allocation(tmp_path / "baseline", baseline, "pack") > actual_new_allocation(tmp_path / "selected", selected, "pack")
    assert sum(map(len, selected[:2])) < sum(map(len, baseline[:2]))
    recipe, recovered = persist_and_decode(context, selected, target, number=2)
    assert recipe["schema"] == archive.SLICE_RECIPE_SCHEMA
    assert recovered == second
    assert initial[2] in recipe["packs"]  # Whole parent remains a physical obligation.
    after = prior_path.stat()
    assert (before.st_ino, before.st_size, before.st_blocks, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_ino, after.st_size, after.st_blocks, after.st_mtime_ns, after.st_ctime_ns)


@pytest.mark.parametrize("start,length", [(-1, 1), (True, 1), (0, 0), (0, True), (0, 100), (100, 1)])
def test_slice_bounds_and_types_fail_closed(tmp_path, start, length):
    context = verifier(tmp_path / "archive")
    raw = b"original-parent"
    packed = archive.PACK_HEADER.pack(archive.PACK_MAGIC, 1) + archive.PACK_RECORD.pack(
        bytes.fromhex(archive.sha(raw)), archive.PACK_HEADER.size + archive.PACK_RECORD.size, len(raw)) + raw
    name = archive.sha(packed) + ".cas.pack"
    writer(context.root / name, packed)
    with pytest.raises(ValueError):
        context._component(archive.sha(raw[:1]), (name, 0, start, length))


def test_slice_hash_and_whole_parent_corruption_are_fatal_on_fresh_public_read(tmp_path):
    context = verifier(tmp_path / "archive")
    raw = b"left-private-bytes:right-private-bytes"
    plan = context._plan({"status.json": [((raw,), {"sha256": archive.sha(raw), "bytes": len(raw), "recovery": "SOURCE_GZIP_FRAME_CONCAT"})][0]}, {}, manifest(1), "a" * 64, {})
    writer(context.root / plan[2], plan[1])
    location = (plan[2], 0, 0, 4)
    assert context._component(archive.sha(b"left"), location) == b"left"
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        context._component(archive.sha(b"wrong"), location)
    path = context.root / plan[2]
    path.write_bytes(path.read_bytes()[:-1] + b"!")
    with pytest.raises(ValueError, match="PACK_HASH_MISMATCH"):
        context._component(archive.sha(b"left"), location)


def test_v3_v4_array_dispatch_cannot_reinterpret_each_others_width(tmp_path):
    context = verifier(tmp_path / "archive")
    recipe = {"schema": archive.RECIPE_SCHEMA, "packs": ["a" * 64 + ".cas.pack"],
        "components": archive._array(archive.COMPONENT_RECORD.pack(0, 0, b"b" * 32), count=1)}
    assert len(context._indices(recipe)[0][1]) == 2
    recipe["schema"] = archive.SLICE_RECIPE_SCHEMA
    with pytest.raises(ValueError, match="ARRAY_INVALID"):
        context._indices(recipe)
    recipe["components"] = archive._array(archive.SLICE_COMPONENT_RECORD.pack(0, 0, 0, 4, b"b" * 32),
        count=1, codec=archive.SLICE_ARRAY_CODEC)
    assert len(context._indices(recipe)[0][1]) == 4
    recipe["schema"] = archive.RECIPE_SCHEMA
    with pytest.raises(ValueError, match="ARRAY_INVALID"):
        context._indices(recipe)


def test_overlapping_ranges_and_repeated_refs_fail_before_materialization(tmp_path, monkeypatch):
    context = verifier(tmp_path / "archive")
    cid = archive.sha(b"slice")
    components = [(cid, ("a" * 64 + ".cas.pack", 0, 0, 4096))]
    descriptor = {"bytes": 4096, "sha256": cid, "recovery": "SOURCE_GZIP_FRAME_CONCAT",
        "references": archive._array(struct.pack("!II", 0, 0), count=2)}
    monkeypatch.setattr(context, "_components", lambda *args, **kwargs: pytest.fail("UNBOUNDED_MATERIALIZATION"))
    with pytest.raises(ValueError, match="EXPANSION_INVALID"):
        context._member(descriptor, components, set())


def test_whole_physical_catalog_counts_duplicate_cids_and_unused_rows(tmp_path, monkeypatch):
    context = verifier(tmp_path / "archive")
    for raw_parts in ((b"shared",), (b"shared", b"unique")):
        cursor = archive.PACK_HEADER.size + len(raw_parts) * archive.PACK_RECORD.size
        rows = []
        for raw in raw_parts:
            rows.append(archive.PACK_RECORD.pack(bytes.fromhex(archive.sha(raw)), cursor, len(raw)))
            cursor += len(raw)
        wire = archive.PACK_HEADER.pack(archive.PACK_MAGIC, len(raw_parts)) + b"".join(rows) + b"".join(raw_parts)
        writer(context.root / (archive.sha(wire) + ".cas.pack"), wire)
    monkeypatch.setattr(archive, "MAX_COMPONENTS", 3)
    catalog = context._catalog()
    assert len(catalog) == 2 and catalog.physical_records == 3
    with pytest.raises(ValueError, match="INDEX_CAPACITY_REACHED"):
        context._plan({"status.json": ((b"new",), {"recovery": "SOURCE_GZIP_FRAME_CONCAT"})},
                      catalog, manifest(3), "c" * 64, {})


def test_virtual_slice_recipe_count_is_bounded_separately_from_physical_catalog(tmp_path, monkeypatch):
    context = verifier(tmp_path / "archive")
    values = (b"a", b"b", b"c", b"new")
    catalog = archive.ComponentCatalog({archive.sha(raw): ("a" * 64 + ".cas.pack", 0, index, 1)
        for index, raw in enumerate(values[:3])}, physical_records=1)
    monkeypatch.setattr(archive, "MAX_COMPONENTS", 3)
    monkeypatch.setattr(context, "_components", lambda entries, **kwargs: {cid: values[index]
        for index, (cid, _) in enumerate(entries)})
    with pytest.raises(ValueError, match="INDEX_CAPACITY_REACHED"):
        context._plan({"status.json": (values, {"recovery": "SOURCE_GZIP_FRAME_CONCAT"})},
                      catalog, manifest(1), "a" * 64, {})


def test_native_v4_exact_five_members_mixed_v3_chain_and_reachability_without_encoder(tmp_path, monkeypatch):
    root, path, _, directories, receipts = native(tmp_path)
    original = members(directories[-1])
    context = archive.ComponentArchive(policy(root, path))
    def range_layout(recipe):
        prior = context._indices(recipe)
        rows, refs, seen = [], {}, {}
        indivisible = set()
        for descriptor in recipe["members"].values():
            if descriptor["recovery"] == "STORED_GZIP_RAW":
                raw, _ = archive._decode_array(descriptor["references"], width=4, maximum=archive.MAX_REFS)
                indivisible.update(index for index, in struct.iter_unpack("!I", raw))
        for number, (cid, location) in enumerate(prior):
            raw = context._component(cid, location)
            parts = (raw,) if number in indivisible or len(raw) < 2 else (raw[:len(raw)//2], raw[len(raw)//2:])
            cursor, indices = 0, []
            for part in parts:
                part_cid = archive.sha(part)
                if part_cid not in seen:
                    seen[part_cid] = len(rows)
                    rows.append(archive.SLICE_COMPONENT_RECORD.pack(recipe["packs"].index(location[0]),
                        location[1], cursor, len(part), bytes.fromhex(part_cid)))
                indices.append(seen[part_cid])
                cursor += len(part)
            refs[number] = indices
        for descriptor in recipe["members"].values():
            raw, _ = archive._decode_array(descriptor["references"], width=4, maximum=archive.MAX_REFS)
            indices = [new for old, in struct.iter_unpack("!I", raw) for new in refs[old]]
            descriptor["references"] = archive._array(b"".join(struct.pack("!I", index) for index in indices), count=len(indices))
        recipe["schema"] = archive.SLICE_RECIPE_SCHEMA
        recipe["components"] = archive._array(b"".join(rows), count=len(rows), codec=archive.SLICE_ARRAY_CODEC)
    current = reseal_recipe(path, receipts[-1], range_layout)
    custody = {str(protected): tree_custody(protected)
               for protected in (root, root.with_name(root.name + ".authority"), path)}
    monkeypatch.setattr(gzip, "compress", lambda *args, **kwargs: pytest.fail("RESTORE_ENCODER_CALLED"))
    monkeypatch.setattr(archive, "encode_page_pack", lambda *args, **kwargs: pytest.fail("RESTORE_ENCODER_CALLED"))
    restored, _ = context.restore(current)
    assert restored == original
    assert policy(root, path).restore_generation(current["generation_id"])["members"] == original
    graph = context.dependency_graph([current])
    assert graph[current["generation_id"]]["base_generation_id"] == receipts[0]["generation_id"]
    parent_packs = {name for node in graph.values() for name in node["packs"]}
    assert parent_packs and all((path / name).is_file() for name in parent_packs)
    assert custody == {str(protected): tree_custody(protected)
                       for protected in (root, root.with_name(root.name + ".authority"), path)}


def test_interleaved_member_references_authenticate_each_parent_pack_once(tmp_path, monkeypatch):
    context = verifier(tmp_path / "archive")
    locations = []
    for value in (b"left", b"right"):
        plan = context._plan({"status.json": ((value,), {"recovery": "SOURCE_GZIP_FRAME_CONCAT"})}, {}, manifest(1), "a" * 64, {})
        writer(context.root / plan[2], plan[1])
        locations.append((archive.sha(value), (plan[2], 0)))
    wire = b"leftright" * 20
    descriptor = {"bytes": len(wire), "sha256": archive.sha(wire), "recovery": "SOURCE_GZIP_FRAME_CONCAT",
        "references": archive._array(b"".join(struct.pack("!I", index) for index in (0, 1) * 20), count=40)}
    seen, actual = [], archive.read
    def observed(path, **kwargs):
        if path.name.endswith(".cas.pack"):
            seen.append(path.name)
        return actual(path, **kwargs)
    monkeypatch.setattr(archive, "read", observed)
    used = set()
    assert context._member(descriptor, locations, used) == wire
    assert used == {0, 1} and len(seen) == len(set(seen)) == 2


def test_all1202_economic_layout_prefixes_keep_original_clock_and_exact_wire(tmp_path, record_property):
    """32 KiB fixture only: all prefixes, never financial ticks/G5 certification."""
    source = raw_source(pages=8)
    context = verifier(tmp_path / "archive")
    catalog = archive.ComponentCatalog()
    prior = None
    baseline_bytes = selected_bytes = 0
    clocks = horizon_schedule(date(2026, 10, 10))
    for number, clock in enumerate(clocks, 1):
        target = struct.pack("!I", number) + source[4:]
        packed, _ = page.encode_page_pack(target, force_full=True)
        proposals = slice_options(target, packed)
        baseline = context._plan({"projection.sqlite": proposals["projection.sqlite"][0]}, catalog, manifest(number), "a" * 64, {})
        aliases = context._slice_catalog(catalog, prior) if prior is not None else None
        selected = context._select_plan(proposals, catalog, manifest(number), "a" * 64, slice_catalog=aliases)
        unit = os.statvfs(context.root).f_frsize
        cost = lambda plan: sum((len(raw) + unit - 1) // unit * unit for raw in plan[:2] if raw is not None)
        baseline_bytes += cost(baseline)
        selected_bytes += cost(selected)
        assert cost(selected) <= cost(baseline)
        assert sum(len(raw) for raw in selected[:2] if raw is not None) <= sum(len(raw) for raw in baseline[:2] if raw is not None)
        prior, wire = persist_and_decode(context, selected, target, number=number)
        assert wire == packed
        if selected[1] is not None:
            rows = archive._pack_index(selected[1], len(selected[1]))
            catalog.physical_records += len(rows)
            for ordinal, (cid, _, _) in enumerate(rows):
                catalog.setdefault(cid, (selected[2], ordinal))
        assert clock == clocks[number - 1]
    assert len(clocks) == 1202 and selected_bytes < baseline_bytes
    physical = context.root.stat().st_blocks * 512 + sum(path.stat().st_blocks * 512 for path in context.root.iterdir())
    assert physical >= selected_bytes  # Directory allocation is still counted.
    record_property("scope", "32KIB_SYNTHETIC_LAYOUT_NOT_FINANCIAL_HORIZON_OR_G5")
    record_property("original_schedule_prefixes", len(clocks))
    record_property("baseline_new_file_allocation_sum", baseline_bytes)
    record_property("selected_new_file_allocation_sum", selected_bytes)
    record_property("selected_total_allocated_including_directory", physical)
