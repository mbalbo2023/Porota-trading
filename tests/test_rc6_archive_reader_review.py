"""Reader closure diagnostics on tiny owned archives, never Horizon/G5 approval."""
from datetime import timedelta
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import time

import pytest

from rc6_shadow_runtime import archive_components as components
from rc6_shadow_runtime.archive_namespace import inspect_archive
from rc6_shadow_runtime.persistence import EvidenceFiles
from scripts import rc6_archive_v3_image_smoke as smoke
from scripts import rc6_archive_reader_review as review
from tests.test_issue465_generations import publish
from tests.test_rc6_component_archive import bytes_at, members, policy, snapshot
from tests.test_rc6_shadow_runtime_wiring import PRE


@pytest.fixture(scope="module")
def mixed_gc_base(tmp_path_factory):
    """Fifty small source generations with ORIGINAL native quotas and retention.

    Clocks deliberately span expiry for a unit recovery test, not the original
    financial schedule. No generated data or metric is an all1202 model.
    """
    base = tmp_path_factory.mktemp("rc6-codec-image-smoke-mixed-gc-")
    root, archive = base / "shadow", base / "archive"
    first, originals, identities = None, {}, {}
    for number in range(1, 51):
        as_of = PRE + timedelta(seconds=30 * number, hours=11 if number >= 32 else 0)
        with EvidenceFiles(root, maximum_files=512, maximum_bytes=128 * 1024**2,
                           archive_root=archive, archive_format="COMPONENT_V3") as files:
            cut = publish(files, number, as_of=as_of)
        ident = cut["pointer"]["generation_id"]
        identities[number] = ident
        source = root / ("gen-" + ident)
        owner = policy(root, archive)
        owner.archive_generation(source)
        if number in (35, 50):
            originals[ident] = members(source)
            smoke._private_slice_recipe_fixture(owner, deadline=time.monotonic() + 5)
        if number == 1:
            first = ident
            owner._durable_control(root / "retention-pins.json", {
                "schema": "RC6_SHADOW_EVIDENCE_PINS_V1", "generation_ids": [ident]})
    (root / "retention-pins.json").unlink()
    # Projected entries force real ACK-verified rotation, preserving the
    # original 512 entry quota instead of shrinking it to manufacture GC.
    policy(root, archive).prepare(additional_files=400,
        pinned=(cut["manifest"]["previous_generation_id"],))
    assert not (root / ("gen-" + first)).exists()
    head = json.loads(bytes_at(archive / "CHECKPOINT.json"))
    assert head["compacted_receipt_count"] == 0
    assert head["contracted_horizon_seconds"] == 9 * 3600 and head["recovery_margin_seconds"] == 3600
    return base, identities, originals


def _copy_mixed(tmp_path, fixture):
    base, identities, originals = fixture
    for name in ("shadow", "shadow.authority", "archive"):
        shutil.copytree(base / name, tmp_path / name)
    return tmp_path / "shadow", tmp_path / "archive", identities, originals


def _assert_mixed_recovered(root, archive, identities, originals):
    owner = policy(root, archive)
    head = owner._archive_checkpoint()
    assert head["compacted_receipt_count"] > 0
    assert head["contracted_horizon_seconds"] == 9 * 3600 and head["recovery_margin_seconds"] == 3600
    receipts = owner._archive_receipts()
    context = components.ComponentArchive(owner)
    graph = context.dependency_graph(receipts)
    # The full sequence-32 anchor survives transitively; V4 at 35 is followed
    # by native V3 deltas and current V4 at 50. Whole parents remain reachable.
    assert identities[32] in graph and identities[35] in graph and identities[50] in graph
    schemas = {receipt["generation_id"]: context._recipe(receipt)["schema"] for receipt in receipts}
    assert schemas[identities[35]] == schemas[identities[50]] == components.SLICE_RECIPE_SCHEMA
    assert schemas[identities[36]] == components.RECIPE_SCHEMA
    with smoke.no_encoder():
        for ident, original in originals.items():
            assert owner.restore_generation(ident)["members"] == original
            assert owner.restore_generation(ident)["members"] == original
        for ident in owner._archive_pins():
            if ident in graph:
                assert all((archive / name).is_file() for name in graph[ident]["packs"])
    assert components.NATIVE_WRITE_RECIPE_SCHEMA == components.RECIPE_SCHEMA
    assert components.SLICE_WRITE_CAPABILITY_REVIEW["status"] == "BLOCKED"
    assert inspect_archive(archive, owner_uid=os.geteuid())["state"] == "WITHIN_QUOTA"


def test_mixed_v3_v4_gc_keeps_whole_parents_and_actual_native_pins(tmp_path, mixed_gc_base):
    root, archive, identities, originals = _copy_mixed(tmp_path, mixed_gc_base)
    source = snapshot(root)
    result = policy(root, archive).maintain_archive()
    assert result["expired_generations"] > 0
    assert snapshot(root) == source
    assert not (archive / "GC.json").exists()
    _assert_mixed_recovered(root, archive, identities, originals)


def _kill_mixed_gc(root, archive, stage, pipe):
    def stop(point):
        if point == stage:
            pipe.send(point)
            signal.pause()
    policy(Path(root), Path(archive), fault_inject=stop).maintain_archive()


@pytest.mark.parametrize("stage", ("archive_gc_after_intent", "archive_gc_before_checkpoint",
    "archive_gc_after_checkpoint", "archive_gc_before_unlink", "archive_gc_after_unlink",
    "archive_gc_before_directory_fsync", "archive_gc_after_directory_fsync"))
def test_mixed_v3_v4_sigkill_gc_recovers_exact_sources(tmp_path, mixed_gc_base, stage):
    root, archive, identities, originals = _copy_mixed(tmp_path, mixed_gc_base)
    source = snapshot(root)
    parent_pipe, child_pipe = multiprocessing.get_context("fork").Pipe(duplex=False)
    child = multiprocessing.get_context("fork").Process(target=_kill_mixed_gc,
        args=(root, archive, stage, child_pipe))
    child.start()
    try:
        assert parent_pipe.poll(10), "MIXED_GC_FAULT_NOT_ENTERED"
        assert parent_pipe.recv() == stage
        os.kill(child.pid, signal.SIGKILL); child.join(5)
        assert child.exitcode == -signal.SIGKILL
    finally:
        if child.is_alive():
            child.kill(); child.join(5)
        parent_pipe.close(); child_pipe.close()
    assert (archive / "GC.json").exists()
    with pytest.raises(ValueError, match="RECOVERY_REQUIRED"):
        policy(root, archive).restore_generation(identities[50])
    with smoke.no_encoder():
        policy(root, archive).maintain_archive()
    assert not (archive / "GC.json").exists()
    assert snapshot(root) == source
    _assert_mixed_recovered(root, archive, identities, originals)


def test_private_v4_fixture_rejects_nonprivate_namespace_before_writing(tmp_path):
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    root.mkdir(mode=0o700)
    before = snapshot(root)
    with pytest.raises(smoke.SmokeError, match="PRIVATE_V4_FIXTURE_ONLY"):
        smoke._private_slice_recipe_fixture(policy(root, archive), deadline=time.monotonic() + 2)
    assert not archive.exists() and snapshot(root) == before


def _execution_metadata():
    """Explicit unit metadata, never a native artifact or approval receipt."""
    nodes = sorted(review.REQUIRED_NODEIDS | {
        "tests/test_rc6_archive_slice_reuse.py::test_slice_hash_and_whole_parent_corruption_are_fatal_on_fresh_public_read"})
    return {"phase": "execution", "source_sha": "a" * 40, "source_tree": "b" * 40,
        "source_namespace_exact_before_after": True, "pytest_exit_code": 0,
        "native_execution_coverage_exact": True, "inet_socket_attempts": [], "unexpected_product_imports": [],
        "phase_validation_errors": [], "child_infrastructure_finalization": {
            "status": "GREEN", "finalization_thread_finished": True, "kernel_echild_before_phase_return": True,
            "signal_guard_installed_and_witnessed": True, "forced_termination_attempted": False,
            "signal_vetoed": False, "termination_signal_attempts": [], "errors": [], "wall_seconds": 0.01,
            "management_bound_seconds": 5},
        "items": [{"nodeid": node, "name": node.rsplit("::", 1)[-1]} for node in nodes],
        "actual_execution_started_nodeids": nodes, "actual_execution_finished_nodeids": nodes,
        "original_pytest_reports": [{"nodeid": node, "when": phase, "outcome": "passed"}
                                    for node in nodes for phase in ("setup", "call", "teardown")]}


def _source_metadata():
    return {"source_sha": "a" * 40, "source_tree": "b" * 40, **review.inventory_source_bytes({
        "rc6_shadow_runtime/archive_namespace.py": b"def inspect_archive(root): pass\n",
        "rc6_shadow_runtime/retention.py": b"from .archive_namespace import inspect_archive\n",
        "caller.py": b"from rc6_shadow_runtime import retention\n",
        "indirect.py": b"import caller\n",
        "loader.sh": b"python -I -S archive_namespace.py\n",
        "unrelated.py": b"answer = 42\n"})}


def test_reader_inventory_covers_transitive_callers_inline_loaders_and_metadata_scope():
    source = _source_metadata()
    rows = {row["path"]: row for row in source["consumers"]}
    assert set(rows) == {"rc6_shadow_runtime/archive_namespace.py", "rc6_shadow_runtime/retention.py",
                         "caller.py", "indirect.py", "loader.sh"}
    assert source["scanned_files"] == 6 and "unrelated.py" in source["scanned_source_sha256"]
    assert rows["rc6_shadow_runtime/archive_namespace.py"]["scope"] == "FILESYSTEM_CUSTODY_METADATA_ONLY_NOT_RECIPE_PROOF"
    assert rows["loader.sh"]["direct_token_lines"]
    assert source["semantic_review_is_not_inferred_from_imports"] is True


def test_reader_review_hash_binds_all_consumers_and_cannot_grant_native_v4_or_g5():
    source, execution = _source_metadata(), _execution_metadata()
    wire = components.canonical(execution)
    result = review.build_review(source, execution, wire, source_sha="a" * 40, source_tree="b" * 40)
    assert review.validate_review(result, source, execution, wire, source_sha="a" * 40, source_tree="b" * 40) == result
    assert result["native_v4_write_enabled"] is False and result["native_writer_contract_approved"] is False
    assert result["g5_equivalence_demonstrated"] is False and result["certified_physical_bound_bytes"] is None
    changed = dict(result, consumer_inventory={**source, "consumers": source["consumers"][:-1]})
    with pytest.raises(ValueError, match="ALL_CONSUMERS_OR_EXECUTION_REBOUND"):
        review.validate_review(changed, source, execution, wire, source_sha="a" * 40, source_tree="b" * 40)


@pytest.mark.parametrize("attack", ("missing_gc_stage", "skipped", "wrong_source", "pending_fin",
    "duplicate_node", "wrong_name", "wrong_finished", "call_only"))
def test_reader_review_rejects_incomplete_or_rebound_real_execution_metadata(attack):
    execution = _execution_metadata()
    if attack == "missing_gc_stage":
        missing = next(row["nodeid"] for row in execution["items"] if "sigkill" in row["nodeid"])
        execution["items"] = [row for row in execution["items"] if row["nodeid"] != missing]
        execution["actual_execution_started_nodeids"] = execution["actual_execution_finished_nodeids"] = [
            row["nodeid"] for row in execution["items"]]
    elif attack == "skipped": execution["original_pytest_reports"][0]["outcome"] = "skipped"
    elif attack == "wrong_source": execution["source_sha"] = "c" * 40
    elif attack == "pending_fin": execution["child_infrastructure_finalization"]["finalization_thread_finished"] = False
    elif attack == "duplicate_node": execution["items"].append(execution["items"][0])
    elif attack == "wrong_name": execution["items"][0]["name"] = "a different test"
    elif attack == "wrong_finished": execution["actual_execution_finished_nodeids"] = []
    elif attack == "call_only": execution["original_pytest_reports"] = [
        row for row in execution["original_pytest_reports"] if row["when"] == "call"]
    with pytest.raises(ValueError, match="ARCHIVE_REVIEW_"):
        review.executed_reader_nodes(execution, source_sha="a" * 40, source_tree="b" * 40)


def test_private_mixed_image_reader_body_verifies_v4_without_source_or_image_approval(tmp_path):
    work = tmp_path / "rc6-codec-image-smoke-reader-body"
    work.mkdir(mode=0o700)
    from rc6_shadow_runtime.retention import EvidenceRetention
    deadline = time.monotonic() + smoke.MAX_SECONDS
    live, archive, identities, original, _ = smoke.native_archive(work, EvidenceFiles, EvidenceRetention, deadline=deadline)
    result = smoke.private_mixed_archive(work, live, archive, identities, original,
        EvidenceFiles, EvidenceRetention, deadline=deadline)
    assert result["recipe_schemas"] == [components.RECIPE_SCHEMA, components.SLICE_RECIPE_SCHEMA, components.RECIPE_SCHEMA]
    assert result["native_v4_write_enabled"] is False and result["original_horizon_or_capacity_equivalence_claimed"] is False
    assert result["whole_parent_graph_verified"] is True and result["recovery_encoder_calls"] == 0


def test_image_restore_encoder_guard_covers_original_page_and_binary_aliases():
    from rc6_shadow_runtime import exact_binary_storage, exact_page_storage
    with smoke.no_encoder():
        for encoder in (components.encode_binary_pack, exact_binary_storage.encode_binary_pack,
                        components.encode_page_pack, exact_page_storage.encode_page_pack):
            with pytest.raises(smoke.SmokeError, match="RESTORE_ENCODER_CALLED"):
                encoder(b"no restored bytes may be reencoded")


def _pinned_source(tmp_path):
    from scripts import rc6_controlled_governed_runner as governed
    import hashlib
    raw = b"from rc6_shadow_runtime import archive_components\n"
    path = tmp_path / 'consumer.py'
    path.write_bytes(raw)
    captured, fields = governed.capture(path)
    pin = {'source_sha': 'a' * 40, 'source_tree': 'b' * 40,
        'physical_namespace_exact_to_literal_tree': True, 'overlay_count': 0,
        'files': {'consumer.py': {'bytes': len(raw), 'sha256': components.sha(raw),
            'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(),
            'stat_fields': fields}}}
    return pin, governed


def test_reader_inventory_reuses_original_pin_and_noatime_bytes_without_git_or_source_copy(tmp_path, monkeypatch):
    pin, governed = _pinned_source(tmp_path)
    monkeypatch.setattr(review.subprocess, 'check_output', lambda *a, **kw: pytest.fail('DUPLICATE_GIT_FORBIDDEN'))
    result = review.inventory_from_source_pin(tmp_path, pin, capture=governed.capture)
    assert result['scanned_files'] == 1 and result['source_sha'] == pin['source_sha']
    assert governed.capture(tmp_path / 'consumer.py')[1] == pin['files']['consumer.py']['stat_fields']


def test_reader_inventory_preserves_original_admitted_code_atime_delta_without_reset(tmp_path):
    import os
    pin, governed = _pinned_source(tmp_path)
    path = tmp_path / 'consumer.py'
    old = path.stat()
    os.utime(path, ns=(1, old.st_mtime_ns))
    pin['files']['consumer.py']['stat_fields'] = governed.capture(path)[1]
    path.read_bytes()  # Real ordinary code read changes only atime.
    current = governed.capture(path)[1]
    assert current['st_atime_ns'] != pin['files']['consumer.py']['stat_fields']['st_atime_ns']
    review.inventory_from_source_pin(tmp_path, pin, capture=governed.capture)
    assert governed.capture(path)[1] == current


@pytest.mark.parametrize('attack', ('bytes', 'stat', 'blocks', 'sha', 'blob', 'path', 'bound', 'overlay', 'partial_pin'))
def test_reader_inventory_rejects_changed_or_incomplete_original_pin_before_review(tmp_path, attack):
    pin, governed = _pinned_source(tmp_path)
    row = pin['files']['consumer.py']
    if attack == 'bytes': (tmp_path / 'consumer.py').write_bytes(b'changed\n')
    elif attack == 'stat': row['stat_fields'] = dict(row['stat_fields'], st_ino=0)
    elif attack == 'blocks': row['stat_fields'] = dict(row['stat_fields'], st_blocks=row['stat_fields']['st_blocks'] + 1)
    elif attack == 'sha': row['sha256'] = '0' * 64
    elif attack == 'blob': row['git_blob'] = '0' * 40
    elif attack == 'path': pin['files']['../escape.py'] = pin['files'].pop('consumer.py')
    elif attack == 'bound': row['bytes'] = review.MAX_SOURCE_FILE + 1
    elif attack == 'overlay': pin['overlay_count'] = 1
    elif attack == 'partial_pin': pin['physical_namespace_exact_to_literal_tree'] = False
    with pytest.raises(ValueError, match='ARCHIVE_REVIEW_'):
        review.inventory_from_source_pin(tmp_path, pin, capture=governed.capture)
