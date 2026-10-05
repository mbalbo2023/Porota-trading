"""Native original-byte archive/recovery guards; private offline fixtures only."""
from copy import deepcopy
from datetime import timedelta
import gzip
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import stat

import pytest

from rc6_dynamic_universe.common import digest
from rc6_shadow_runtime import archive_components as components
from rc6_shadow_runtime import exact_page_storage
from rc6_shadow_runtime.archive_namespace import inspect_archive
from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation
from rc6_shadow_runtime.retention import EvidenceRetention, RetentionPressure
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.test_issue465_generations import publish
from tests.test_rc6_shadow_runtime_wiring import PRE, make_store


def bytes_at(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        chunks = []
        while part := os.read(fd, 65536):
            chunks.append(part)
        return b"".join(chunks)
    finally:
        os.close(fd)


def members(path):
    return {name: bytes_at(path / name) for name in components.MEMBERS}


def snapshot(root):
    result = {}
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        names = os.listdir(fd)
    finally:
        os.close(fd)
    for name in names:
        path = root / name
        info = path.lstat()
        value = (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns)
        if stat.S_ISDIR(info.st_mode):
            result[name] = (value, snapshot(path))
        else:
            result[name] = (value, hashlib.sha256(bytes_at(path)).hexdigest())
    return result


def tree_custody(root):
    info = root.lstat()
    return ((info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_nlink,
        info.st_size, info.st_blocks, info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns), snapshot(root))


def policy(root, archive, **kwargs):
    return EvidenceRetention(root, archive_root=archive, archive_format="COMPONENT_V3", **kwargs)


def native(tmp_path, count=2):
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    cuts = []
    with EvidenceFiles(root) as files:
        for number in range(1, count + 1):
            cuts.append(publish(files, number))
    directories = [root / ("gen-" + cut["pointer"]["generation_id"]) for cut in cuts]
    receipts = [policy(root, archive).archive_generation(path) for path in directories]
    return root, archive, cuts, directories, receipts


def recipe(archive, receipt):
    return json.loads(gzip.decompress(bytes_at(archive / (receipt["generation_id"] + ".recipe.gz"))))


def test_native_two_cut_archive_exact_five_members_legacy_dispatch_no_encoder_and_readonly_custody(tmp_path, monkeypatch):
    root, archive, cuts, directories, receipts = native(tmp_path)
    originals = [members(path) for path in directories]
    before, custody = snapshot(root), snapshot(archive)
    assert receipts[0]["schema"] == components.ACK_SCHEMA
    assert all(receipt["archiver_id"] == components.ARCHIVER_ID for receipt in receipts)
    assert recipe(archive, receipts[1])["members"]["projection.sqlite"]["dependency_depth"] == 1
    monkeypatch.setattr(gzip, "compress", lambda *a, **k: pytest.fail("RECOVERY_ENCODER_CALLED"))
    monkeypatch.setattr(exact_page_storage, "encode_page", lambda *a, **k: pytest.fail("RECOVERY_ENCODER_CALLED"))
    # The constructor's emission preference must never reinterpret an existing
    # receipt. Public restoration creates its proof context per invocation.
    reader = EvidenceRetention(root, archive_root=archive, archive_format="TAR_V2")
    for index, receipt in enumerate(receipts):
        result = reader.restore_generation(receipt["generation_id"])
        assert result["members"] == originals[index]
        assert result["manifest"] == cuts[index]["manifest"]
        assert result["verification_level"] == components.LEVEL
        assert reader.restore_generation("gen-" + receipt["generation_id"])["members"] == originals[index]
    assert before == snapshot(root) and custody == snapshot(archive)


def test_repeat_component_verifier_rechecks_bytes_and_never_reuses_a_prior_public_proof(tmp_path):
    root, archive, _, _, receipts = native(tmp_path)
    verifier = components.ComponentArchive(policy(root, archive))
    assert verifier.restore(receipts[1])[0] == verifier.restore(receipts[1])[0]
    packed = archive / recipe(archive, receipts[1])["packs"][0]
    raw = bytes_at(packed); packed.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        verifier.restore(receipts[1])


@pytest.mark.parametrize("attack", ("pack_bytes", "missing_pack", "missing_base_receipt"))
def test_failed_restore_and_archive_preserve_root_directory_atime_and_all_protected_custody(tmp_path, attack):
    root, archive, _, directories, receipts = native(tmp_path)
    value = recipe(archive, receipts[-1])
    if attack == "missing_base_receipt":
        (archive / (receipts[0]["generation_id"] + ".receipt.json")).unlink()
    else:
        target = archive / value["packs"][0]
        if attack == "pack_bytes":
            raw = bytes_at(target)
            target.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        else:
            target.unlink()
    (root / ("archive-ack-" + receipts[-1]["generation_id"] + ".json")).unlink()
    protected = (root, root.with_name(root.name + ".authority"), archive)
    before = {str(path): tree_custody(path) for path in protected}
    reader = policy(root, archive)
    with pytest.raises((ValueError, OSError)):
        reader.restore_generation(receipts[-1]["generation_id"])
    assert before == {str(path): tree_custody(path) for path in protected}
    with pytest.raises((ValueError, OSError)):
        reader.archive_generation(directories[-1])
    assert before == {str(path): tree_custody(path) for path in protected}


@pytest.mark.parametrize("attack", ("pack_bytes", "missing_pack", "missing_base_receipt", "missing_base_recipe"))
def test_real_published_dependency_corruption_prevents_ack_and_source_deletion(tmp_path, attack):
    root, archive, cuts, directories, receipts = native(tmp_path)
    original = snapshot(root)
    value = recipe(archive, receipts[1])
    if attack in {"pack_bytes", "missing_pack"}:
        target = archive / value["packs"][0]
        if attack == "pack_bytes":
            raw = bytes_at(target); target.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        else:
            target.unlink()
    else:
        base = value["members"]["projection.sqlite"]["base_generation_id"]
        assert base == receipts[0]["generation_id"]
        (archive / (base + (".receipt.json" if attack == "missing_base_receipt" else ".recipe.gz"))).unlink()
    reader = policy(root, archive)
    with pytest.raises((ValueError, OSError)):
        reader.restore_generation(receipts[1]["generation_id"])
    (root / ("archive-ack-" + receipts[1]["generation_id"] + ".json")).unlink()
    source_before = snapshot(root)
    with pytest.raises((ValueError, OSError)):
        reader.archive_generation(directories[1])
    assert source_before == snapshot(root)
    assert not list(root.glob(".deleting-*")) and not list(root.glob("delete-intent-*"))
    assert not (root / ("archive-ack-" + receipts[1]["generation_id"] + ".json")).exists()
    assert original["CURRENT.json"] == snapshot(root)["CURRENT.json"]


def reseal_recipe(archive, receipt, change):
    value = recipe(archive, receipt); change(value)
    raw = gzip.compress(components.canonical(value), mtime=0, compresslevel=1)
    (archive / (receipt["generation_id"] + ".recipe.gz")).write_bytes(raw)
    modified = dict(receipt, archive_sha256=hashlib.sha256(raw).hexdigest())
    (archive / (receipt["generation_id"] + ".receipt.json")).write_bytes(components.canonical(modified))
    head = json.loads(bytes_at(archive / "CHECKPOINT.json"))
    head["receipt_digest"] = digest(modified)
    head["digest"] = digest({key: val for key, val in head.items() if key != "digest"})
    (archive / "CHECKPOINT.json").write_bytes(components.canonical(head))
    return modified


@pytest.mark.parametrize("attack", ("member_none", "array_none", "bool_depth", "unknown_schema", "unknown_key",
    "extra_pack", "bad_manifest_link", "cycle", "base_depth", "member_hash", "source_clock"))
def test_resealed_native_recipe_types_indices_links_and_dependency_graph_fail_closed(tmp_path, attack):
    root, archive, _, _, receipts = native(tmp_path)
    original = snapshot(root)
    def change(value):
        record = value["members"]["projection.sqlite"]
        if attack == "member_none": value["members"]["report.json.gz"] = None
        elif attack == "array_none": value["components"] = None
        elif attack == "bool_depth": record["dependency_depth"] = True
        elif attack == "unknown_schema": value["schema"] = "UNKNOWN"
        elif attack == "unknown_key": value["trading_authority"] = True
        elif attack == "extra_pack": value["packs"].append(value["packs"][0])
        elif attack == "bad_manifest_link": value["manifest_links"]["source_reports_digest"] = "0" * 64
        elif attack == "cycle": record["base_generation_id"] = value["generation_id"]
        elif attack == "base_depth": record["dependency_depth"] = 32
        elif attack == "member_hash": record["sha256"] = "0" * 64
        elif attack == "source_clock": value["manifest_links"]["as_of"] = (PRE - timedelta(hours=50)).isoformat()
    modified = reseal_recipe(archive, receipts[-1], change)
    with pytest.raises((ValueError, OSError)):
        policy(root, archive).restore_generation(modified["generation_id"])
    assert original == snapshot(root)


def test_native_legacy_tar_receipt_bytes_remain_readable_after_new_component_publication(tmp_path):
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    with EvidenceFiles(root) as files:
        old, new = publish(files, 1), publish(files, 2)
    first = root / ("gen-" + old["pointer"]["generation_id"])
    prior = EvidenceRetention(root, archive_root=archive).archive_generation(first)
    old_tar = bytes_at(archive / (prior["generation_id"] + ".tar.gz"))
    old_receipt = bytes_at(archive / (prior["generation_id"] + ".receipt.json"))
    second = root / ("gen-" + new["pointer"]["generation_id"])
    result = policy(root, archive).archive_generation(second)
    assert result["previous_receipt_digest"] == digest(prior)
    restored = policy(root, archive).restore_generation(prior["generation_id"])
    assert restored["members"] == members(first)
    assert restored["verification_level"] == "LEGACY_V2_ALL_ORIGINAL_MEMBER_HASHES_AND_MANIFEST"
    assert old_tar == bytes_at(archive / (prior["generation_id"] + ".tar.gz"))
    assert old_receipt == bytes_at(archive / (prior["generation_id"] + ".receipt.json"))


def test_legacy_restore_cannot_borrow_hash_proof_from_an_object_replaced_between_reads(tmp_path, monkeypatch):
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    with EvidenceFiles(root) as files:
        cuts = [publish(files, number) for number in (1, 2)]
    writer = EvidenceRetention(root, archive_root=archive)
    receipts = [writer.archive_generation(root / ("gen-" + cut["pointer"]["generation_id"])) for cut in cuts]
    replacement = bytes_at(archive / (receipts[1]["generation_id"] + ".tar.gz"))
    reader = policy(root, archive)
    original = reader._verify_archive
    def substitute(receipt):
        value = original(receipt)
        (archive / (receipt["generation_id"] + ".tar.gz")).write_bytes(replacement)
        return value
    monkeypatch.setattr(reader, "_verify_archive", substitute)
    before = snapshot(root)
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        reader.restore_generation(receipts[0]["generation_id"])
    assert before == snapshot(root)


BUILD_FAULTS = ("archive_after_build_intent", "archive_before_publish_object", "archive_after_publish_components",
    "archive_after_publish_recipe", "archive_before_publish_ack", "archive_before_clear_build_intent")


@pytest.mark.parametrize("stage", BUILD_FAULTS)
def test_native_archive_build_intent_recovers_exact_original_bytes_after_each_interruption(tmp_path, stage):
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    with EvidenceFiles(root) as files:
        cut = publish(files, 1)
    source = root / ("gen-" + cut["pointer"]["generation_id"])
    before = members(source)
    def stop(point):
        if point == stage:
            raise OSError("SYNTHETIC_INTERRUPTION")
    with pytest.raises(OSError, match="SYNTHETIC_INTERRUPTION"):
        policy(root, archive, fault_inject=stop).archive_generation(source)
    assert (archive / "BUILD.json").exists()
    receipt = policy(root, archive).archive_generation(source)
    assert not (archive / "BUILD.json").exists()
    assert policy(root, archive).restore_generation(receipt["generation_id"])["members"] == before
    assert read_committed_generation(root)["pointer"] == cut["pointer"]
    assert not list(archive.glob(".*.tmp"))


def test_component_ack_retry_after_another_archive_does_not_rewind_receipt_high_water(tmp_path):
    root, archive, _, directories, receipts = native(tmp_path)
    checkpoint = bytes_at(archive / "CHECKPOINT.json")
    (root / ("archive-ack-" + receipts[0]["generation_id"] + ".json")).unlink()
    assert policy(root, archive).archive_generation(directories[0]) == receipts[0]
    assert checkpoint == bytes_at(archive / "CHECKPOINT.json")
    assert json.loads(bytes_at(root / ("archive-ack-" + receipts[0]["generation_id"] + ".json"))) == receipts[0]


def test_canonical_factory_selects_component_format_binds_fingerprint_and_keeps_explicit_legacy(tmp_path):
    store, _ = make_store(tmp_path, count=2)
    current = ShadowRuntime.from_environment(store.path, evidence_root=tmp_path / "native", source_roots=[])
    assert current.files.archive_format == "COMPONENT_V3"
    legacy = ShadowRuntime.from_environment(store.path, evidence_root=current.root, source_roots=[], archive_format="TAR_V2")
    assert legacy.files.archive_format == "TAR_V2" and legacy.configuration_fingerprint(PRE) != current.configuration_fingerprint(PRE)
    assert ShadowRuntime(store.path, evidence_root=tmp_path / "offline", source_roots=[]).files.archive_format == "TAR_V2"


def test_archive_cannot_share_or_contain_the_independent_generation_authority(tmp_path):
    root = tmp_path / "shadow"
    for archive in (tmp_path / "shadow.authority", tmp_path / "shadow.authority" / "nested"):
        with pytest.raises(ValueError, match="MUST_BE_SEPARATE"):
            EvidenceFiles(root, archive_root=archive)
        assert not root.exists() and not archive.exists()


def test_archive_physical_and_logical_peak_admission_denies_before_ack_or_source_rotation(tmp_path):
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    with EvidenceFiles(root) as files:
        cut = publish(files, 1)
    source = root / ("gen-" + cut["pointer"]["generation_id"])
    before = snapshot(root)
    with pytest.raises(RetentionPressure, match="ARCHIVE_CAPACITY_REACHED"):
        policy(root, archive, archive_maximum_bytes=262144).archive_generation(source)
    assert before == snapshot(root)
    assert not list(archive.glob("*.receipt.json")) and not list(root.glob("archive-ack-*"))
    assert not list(archive.glob("*.cas.pack")) and not list(archive.glob("*.recipe.gz"))


@pytest.fixture(scope="module")
def gc_base(tmp_path_factory):
    base = tmp_path_factory.mktemp("native-component-gc")
    root, archive = base / "shadow", base / "archive"
    first_id = None
    for number in range(1, 51):
        as_of = PRE + timedelta(seconds=30 * number)
        if number >= 32:
            as_of += timedelta(hours=11)
        with EvidenceFiles(root, maximum_files=64, archive_root=archive, archive_format="COMPONENT_V3") as files:
            cut = publish(files, number, as_of=as_of)
        ident = cut["pointer"]["generation_id"]
        source = root / ("gen-" + ident)
        policy(root, archive).archive_generation(source)
        if number == 1:
            first_id = ident
            # Hold a real public pin until the fault-injection window. The
            # first fixture let automatic GC finish before the test started.
            policy(root, archive)._durable_control(root / "retention-pins.json", {
                "schema": "RC6_SHADOW_EVIDENCE_PINS_V1", "generation_ids": [ident]})
    (root / "retention-pins.json").unlink()
    policy(root, archive, maximum_files=64).prepare(additional_files=46,
        pinned=(cut["manifest"]["previous_generation_id"],))
    assert not (root / ("gen-" + first_id)).exists()
    assert json.loads(bytes_at(archive / "CHECKPOINT.json"))["compacted_receipt_count"] == 0
    return base, cut


def copy_gc_base(tmp_path, gc_base):
    base, cut = gc_base
    for name in ("shadow", "shadow.authority", "archive"):
        shutil.copytree(base / name, tmp_path / name)
    return tmp_path / "shadow", tmp_path / "archive", cut


def test_native_rolling_gc_preserves_full_wheel_recovery_transitive_bases_and_receipt_boundary(tmp_path, gc_base):
    root, archive, cut = copy_gc_base(tmp_path, gc_base)
    before = snapshot(root)
    previous = json.loads(bytes_at(archive / "CHECKPOINT.json"))
    old_receipts = [json.loads(bytes_at(path)) for path in archive.glob("*.receipt.json")]
    result = policy(root, archive).maintain_archive()
    head = json.loads(bytes_at(archive / "CHECKPOINT.json"))
    assert result["expired_generations"] > 0 and head["compacted_receipt_count"] > 0
    assert head["receipt_count"] == previous["receipt_count"] and head["receipt_digest"] == previous["receipt_digest"]
    assert head["contracted_horizon_seconds"] == 9 * 3600 and head["recovery_margin_seconds"] == 3600
    assert before == snapshot(root)
    restored = policy(root, archive).restore_generation(cut["pointer"]["generation_id"])
    assert restored["members"] == members(root / ("gen-" + cut["pointer"]["generation_id"]))
    expired = next(receipt for receipt in old_receipts if receipt["receipt_sequence"] <= head["compacted_receipt_count"])
    with pytest.raises(ValueError, match="EXPIRED"):
        policy(root, archive).restore_generation(expired["generation_id"])
    with pytest.raises(ValueError, match="EXPIRED"):
        policy(root, archive)._advance_archive_checkpoint(expired)
    assert not (archive / "GC.json").exists()
    assert inspect_archive(archive)["state"] == "WITHIN_QUOTA"


def _kill_gc(root, archive, stage, pipe):
    def stop(point):
        if point == stage:
            pipe.send(point)
            signal.pause()
    policy(Path(root), Path(archive), fault_inject=stop).maintain_archive()


@pytest.mark.parametrize("stage", ("archive_gc_after_intent", "archive_gc_before_checkpoint", "archive_gc_after_checkpoint",
    "archive_gc_before_unlink", "archive_gc_after_unlink", "archive_gc_before_directory_fsync", "archive_gc_after_directory_fsync"))
def test_real_sigkill_recovers_gc_checkpoint_then_owned_deletes_without_losing_retained_sources(tmp_path, gc_base, stage):
    root, archive, cut = copy_gc_base(tmp_path, gc_base)
    source = snapshot(root)
    read_pipe, child_pipe = multiprocessing.get_context("fork").Pipe(duplex=False)
    child = multiprocessing.get_context("fork").Process(target=_kill_gc, args=(root, archive, stage, child_pipe))
    child.start()
    try:
        assert read_pipe.poll(15), "NATIVE_GC_FAULT_NOT_ENTERED"
        assert read_pipe.recv() == stage
        os.kill(child.pid, signal.SIGKILL); child.join(10)
        assert child.exitcode == -signal.SIGKILL
    finally:
        if child.is_alive(): child.kill(); child.join()
        read_pipe.close(); child_pipe.close()
    assert (archive / "GC.json").exists()
    with pytest.raises(ValueError, match="RECOVERY_REQUIRED"):
        policy(root, archive).restore_generation(cut["pointer"]["generation_id"])
    policy(root, archive).maintain_archive()
    assert not (archive / "GC.json").exists()
    assert source == snapshot(root)
    assert policy(root, archive).restore_generation(cut["pointer"]["generation_id"])["members"] == members(
        root / ("gen-" + cut["pointer"]["generation_id"]))


def test_resealed_gc_cannot_delete_a_recent_live_generation_or_move_expiry_clock(tmp_path, gc_base):
    root, archive, cut = copy_gc_base(tmp_path, gc_base)
    def stop(point):
        if point == "archive_gc_after_intent":
            raise OSError("SYNTHETIC_GC_INTERRUPTION")
    with pytest.raises(OSError): policy(root, archive, fault_inject=stop).maintain_archive()
    value = json.loads(bytes_at(archive / "GC.json"))
    ident = cut["pointer"]["generation_id"]
    target = archive / (ident + ".receipt.json")
    value["targets"][0] = {"name": target.name, "sha256": hashlib.sha256(bytes_at(target)).hexdigest()}
    value["digest"] = digest({key: val for key, val in value.items() if key != "digest"})
    (archive / "GC.json").write_bytes(components.canonical(value))
    before = snapshot(root); head = bytes_at(archive / "CHECKPOINT.json")
    with pytest.raises(ValueError, match="PIN_CONFLICT"):
        policy(root, archive).maintain_archive()
    assert before == snapshot(root) and head == bytes_at(archive / "CHECKPOINT.json")
