#!/usr/bin/env python3
"""Original-byte V3/V4 comparison; never a G5 receipt or native write flag.

The native Horizon producer calls observe() with its already captured original
five members. There is one original fixture and one set of worker ticks. Each
private branch maintains its OWN cumulative CAS catalog, receipt chain, whole
packs and native retention/GC state. Development tests may use the private
branch directly; a material producer must authenticate admission first.

The V2 admission additionally requires an exact-artifact review of the private
producer ACK contract: the candidate supplies the archive that permits native
live rotation, while a failed 512 MiB V3 baseline stays RED and cannot truncate
the input schedule. This reviewed development sink never qualifies native G5
or enables the product writer to emit V4.

The audit CLI reads an existing lossless Actions ZIP without extracting it.
Missing historical member bytes remain missing; hashes are not substituted for
payloads and a partial artifact cannot establish the original Horizon bound.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import resource
import stat
import time
from types import MappingProxyType
import zipfile

from rc6_shadow_runtime import archive_components as components
from rc6_shadow_runtime.archive_physical_model import horizon_schedule, original_prefix_obligations
from rc6_shadow_runtime.retention import EvidenceRetention, RetentionPressure


SCHEMA = "rc6.original-cas-comparison.v1"
REVIEW_SCHEMA = "rc6.original-cas-reader-review.v2"
ACK_REVIEW_SCHEMA = "rc6.original-cas-private-producer-ack-review.v1"
ACK_SCOPE = "PRIVATE_ORIGINAL_PRODUCER_CANDIDATE_ACK_ONLY_NOT_G5"
MAX_AUDIT_BYTES = 256 * 1024**2
MAX_INDEX_BYTES = 4 * 1024**2
MAX_RESULT_BYTES = 16 * 1024**2
CUSTODY_POLICY_SHA256 = "22b14344abc0429e27b893e9ef3851f5029c8ffb882b99746b95c695b7e682b1"
CONTRACT = MappingProxyType({
    "cuts": 1202, "catalog_count": 1200, "observations_per_identity": 5,
    "observation_rows": 6000, "tick_seconds": 30,
    "preopen": "2026-10-05T13:20:00+00:00",
    "first_operational": "2026-10-05T13:35:00+00:00",
    "restart_indices": (361, 841),
    "restart_kind": "SAME_PROCESS_CANONICAL_FACTORY_RECONSTRUCTION_WITH_REAL_CHECKPOINT_REUSE_NOT_SIGKILL",
    "archive_limit_bytes": 512 * 1024**2, "archive_limit_entries": 32768,
    "maximum_dependency_depth": 32, "contracted_retention_seconds": 32400,
    "recovery_margin_seconds": 3600, "full_cycle_deadline_seconds": 30,
    "provider_session": "FIXED_SYNTHETIC_SOURCE_NO_REFRESH",
    "fresh_contract_until": "2026-10-05T13:37:00+00:00",
    "first_operational_cut_retained": 1,
    "anchor_rule": "ORIGINAL_SEQUENCE_MOD32_OR_COMMON_DEPTH32",
    "gc_unit": "WHOLE_IMMUTABLE_PACK_NO_RANGE_DELETION",
    "pins": "ACTUAL_NATIVE_SOURCE_CURRENT_PREVIOUS_FALLBACK_AND_TRANSITIVE_BASES",
})
READER_CASES = frozenset({
    "test_native_v4_exact_five_members_mixed_v3_chain_and_reachability_without_encoder",
    "test_existing_v4_remains_readable_and_next_native_write_is_v3",
    "test_v4_gc_recovery_rejects_reachable_whole_parent_pack_before_any_unlink",
    "test_slice_hash_and_whole_parent_corruption_are_fatal_on_fresh_public_read",
    "test_v3_v4_array_dispatch_cannot_reinterpret_each_others_width",
})
ACK_CASES = frozenset({
    "test_existing_candidate_ack_restores_same_five_bytes_and_preserves_native_v3_writer",
    "test_private_candidate_red_stops_before_any_native_build_or_ack",
    "test_private_baseline_quota_red_keeps_candidate_and_original_sequence_running",
    "test_private_candidate_ack_rotation_and_recovery_keep_native_pins_and_original_limits",
    "test_private_comparison_completion_never_satisfies_g5",
    "test_private_candidate_ack_rejects_changed_marker_source_or_receipt",
})


def require(value, reason):
    if not value:
        raise ValueError(reason)


def _executed_case_names(items, prefix):
    """Bind reviewed function names to authentic pytest node IDs, including params."""
    names = set()
    for item in items:
        nodeid = item["nodeid"]
        if nodeid.startswith(prefix):
            name = item["name"].split("[", 1)[0]
            require(name == nodeid.rsplit("::", 1)[-1].split("[", 1)[0],
                    "CAS_COMPARISON_ACTUAL_EXECUTION_CASE_ID_CHANGED")
            names.add(name)
    return names


def _read(path, *, maximum):
    """Bounded NOATIME read with the same complete all11 custody comparison."""
    from scripts.rc6_material_horizon import attributes
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "CAS_COMPARISON_ALIAS_FORBIDDEN")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 <= info.st_size <= maximum,
                "CAS_COMPARISON_BOUNDED_REGULAR_CONTROL_REQUIRED")
        before = attributes(info)
        chunks, size = [], 0
        while raw := os.read(fd, min(65536, maximum + 1 - size)):
            chunks.append(raw); size += len(raw)
            require(size <= maximum, "CAS_COMPARISON_CONTROL_CAPACITY_REACHED")
        require(before == attributes(os.fstat(fd)) == attributes(path.lstat()) and size == info.st_size,
                "CAS_COMPARISON_CONTROL_CHANGED")
        return b"".join(chunks)
    finally:
        os.close(fd)


def canonical_fixture_identity(source_root):
    """Hash the actual original generator; do not create a reduced substitute."""
    wire = _read(Path(source_root) / "scripts/rc6_issue465_stress.py", maximum=MAX_INDEX_BYTES)
    source = wire.decode("utf-8")
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "fixture_database")
    lines = source.splitlines(keepends=True)
    body = "".join(lines[function.lineno - 1:function.end_lineno]).encode()
    return {"path": "scripts/rc6_issue465_stress.py", "source_sha256": components.sha(wire),
            "fixture_function_sha256": components.sha(body),
            "catalog_count": 1200, "observations_per_identity": 5, "observation_rows": 6000,
            "source_rows_materialized_by_this_model": 0, "contract": dict(CONTRACT)}


def audit_original_artifact(policy_path, member_index, artifact_path, artifact_id):
    """Verify every original ZIP member against its hash-pinned Git CSV."""
    policy_wire = _read(policy_path, maximum=MAX_INDEX_BYTES)
    require(components.sha(policy_wire) == CUSTODY_POLICY_SHA256, "CAS_ORIGINAL_SOURCE_PINNED_POLICY_CHANGED")
    policy = components.loads(policy_wire)
    require(policy.get("schema") == "porota.rc6.actions-custody-plan.v1"
            and policy.get("repository") == "mbalbo2023/Porota-trading", "CAS_ORIGINAL_CUSTODY_POLICY_REQUIRED")
    index = _read(member_index, maximum=MAX_INDEX_BYTES)
    pin = policy["index"]
    require(len(index) == pin["bytes"] and components.sha(index) == pin["sha256"]
            and hashlib.sha1(b"blob " + str(len(index)).encode() + b"\0" + index).hexdigest() == pin["git_blob"],
            "CAS_ORIGINAL_GIT_MEMBER_INDEX_CHANGED")
    records = [row for row in csv.DictReader(io.StringIO(index.decode()))
               if row["artifact_id"] == str(artifact_id)]
    expected = {row["path"]: row for row in records}
    require(expected and len(expected) == len(records), "CAS_ORIGINAL_DUPLICATE_OR_MISSING_INDEX_MEMBER")
    artifact = next((row for row in policy["artifacts"] if row["artifact_id"] == artifact_id), None)
    require(artifact is not None, "CAS_ORIGINAL_ARTIFACT_NOT_IN_CUSTODY")
    wire = _read(artifact_path, maximum=MAX_AUDIT_BYTES)
    require(len(wire) == artifact["bytes"] and components.sha(wire) == artifact["sha256"],
            "CAS_ORIGINAL_ZIP_DIGEST_CHANGED")
    verified, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(wire)) as archive:
        rows = archive.infolist()
        require(len(rows) == len(expected) == artifact["member_count"], "CAS_ORIGINAL_MEMBER_SET_CHANGED")
        for item in rows:
            name = item.filename
            parsed = PurePosixPath(name)
            mode = item.external_attr >> 16
            require(name in expected and name not in verified and not item.is_dir() and not item.flag_bits & 1
                    and parsed.as_posix() == name and not parsed.is_absolute() and ".." not in parsed.parts
                    and "\\" not in name and not any(ord(c) < 32 for c in name)
                    and stat.S_IFMT(mode) in (0, stat.S_IFREG), "CAS_ORIGINAL_UNSAFE_OR_DUPLICATE_MEMBER")
            record, size, hasher = expected[name], 0, hashlib.sha256()
            with archive.open(item) as source:
                while raw := source.read(65536):
                    size += len(raw); total += len(raw); hasher.update(raw)
                    require(total <= MAX_AUDIT_BYTES, "CAS_ORIGINAL_UNPACKED_CAPACITY_REACHED")
            require(size == item.file_size == int(record["uncompressed_bytes"])
                    and hasher.hexdigest() == record["sha256"], "CAS_ORIGINAL_MEMBER_DIGEST_CHANGED")
            verified[name] = record["sha256"]  # archive.open consumed and checked CRC.
        results = [name for name in verified if name.endswith("native-horizon-result.json")]
        require(len(results) == 1, "CAS_ORIGINAL_HORIZON_RESULT_REQUIRED")
        result = components.loads(archive.read(results[0]))
        manifest = components.loads(archive.read("raw-lossless.manifest.json"))
    cuts = result.get("cuts", [])
    require(result.get("catalog_count") == 1200 and result.get("input_observation_rows") == 6000
            and result.get("ticks_requested") == 1201 and result.get("tick_seconds") == 30,
            "CAS_ORIGINAL_WORKLOAD_CONTRACT_CHANGED")
    clocks = horizon_schedule(datetime.fromisoformat(CONTRACT["preopen"]).date())
    require(type(cuts) is list and 0 <= len(cuts) <= 1202, "CAS_ORIGINAL_PREFIX_INVALID")
    for index, cut in enumerate(cuts):
        require(cut.get("tick_index") == index and cut.get("as_of") == clocks[index].isoformat()
                and set(cut.get("original_member_sha256", {})) == components.MEMBERS,
                "CAS_ORIGINAL_CLOCK_OR_MEMBER_BINDING_CHANGED")
    payload_hashes = set(verified.values())
    available = sum(set(cut["original_member_sha256"].values()) <= payload_hashes for cut in cuts)
    return {"schema": SCHEMA, "status": "BLOCKED_ORIGINAL_MEMBER_BYTES_OR_COMPLETE_HORIZON_MISSING",
        "classification": "AUTHENTIC_HISTORICAL_CUSTODY_AUDIT_NOT_G5",
        "artifact_id": artifact_id, "artifact_sha256": artifact["sha256"],
        "index_sha256": pin["sha256"], "all_member_hashes_and_crc_verified": len(verified),
        "actual_original_cuts": len(cuts), "missing_original_cuts": 1202 - len(cuts),
        "cuts_with_all_five_payloads_available": available,
        "source_db_archive_copied": manifest.get("Source_tar_DB_archive_venv_fixture_content_copied"),
        "historical_error": result.get("error", {}).get("reason"), "original_contract": dict(CONTRACT),
        "original_model_obligations": dict(original_prefix_obligations(cuts, available_payload_sha256=payload_hashes)),
        "storage_reduction_percent": None, "certified_physical_bound_bytes": None,
        "runtime_validated": False, "real_orders_sent": 0}


class _ObservedRetention(EvidenceRetention):
    """Native GC/recovery with actual producer pins; no fake source mirror."""
    def __init__(self, *args, sample, **kwargs):
        self.sample, self.native_pins, self.current_originals = sample, frozenset(), None
        super().__init__(*args, **kwargs)

    def _archive_pins(self, supplied=()):
        result = set(self.native_pins) | set(supplied)
        require(all(isinstance(value, str) and components.ID.fullmatch(value) for value in result),
                "CAS_COMPARISON_ACTUAL_NATIVE_PINS_REQUIRED")
        return result

    def _fault(self, stage):
        self.sample(stage)
        super()._fault(stage)

    def _sync_directory(self, path):
        super()._sync_directory(path)
        self.sample("native_directory_fsync")

    def _recover_archive_build(self):
        pending = self._build_intent()
        if pending is not None and not (self.archive_root / (pending["generation_id"] + ".receipt.json")).exists():
            originals = self.current_originals
            require(type(originals) is dict and components.sha(originals["manifest.json"]) == pending["manifest_sha256"]
                    and components.loads(originals["manifest.json"])["generation_id"] == pending["generation_id"],
                    "CAS_COMPARISON_INTERRUPTED_BUILD_SAME_ORIGINAL_BYTES_REQUIRED")
            # The explicit private writer resumes below with the same bytes;
            # it cannot use a missing/fabricated gen-* source directory.
            return False
        return super()._recover_archive_build()


def _inventory(root):
    """Actual allocation of whole files and directories, including temporaries."""
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid == os.getuid(), "CAS_COMPARISON_NAMESPACE_CUSTODY_CHANGED")
    fd = os.open(root, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_NOATIME)
    try:
        names = os.listdir(fd)
        require(len(names) <= 32768, "CAS_COMPARISON_NAMESPACE_ENTRY_CAPACITY_REACHED")
    finally:
        os.close(fd)
    logical, allocated = info.st_size, info.st_blocks * 512
    temporaries, packs = 0, 0
    for name in names:
        info = (root / name).lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600, "CAS_COMPARISON_NAMESPACE_MEMBER_CUSTODY_CHANGED")
        logical += info.st_size; allocated += info.st_blocks * 512
        temporaries += int(name.endswith(".tmp") or name in {"BUILD.json", "GC.json"})
        packs += int(name.endswith(".cas.pack"))
    return {"logical_bytes_including_directories": logical,
            "allocated_bytes_including_directories": allocated,
            "entries_excluding_root": len(names), "whole_packs": packs,
            "pending_temporaries_or_intents": temporaries}


class _PrivateBranch:
    """Economic-test API; PRIVATE bytes here grant no material admission."""
    def __init__(self, root, *, slices, fault_inject=None, archive_root=None):
        self.root, self.slices = Path(root), slices
        require(type(slices) is bool and not os.path.lexists(self.root)
                and self.root.parent.resolve(strict=True) == self.root.parent
                and not any(p.is_symlink() for p in self.root.parents), "CAS_COMPARISON_FRESH_PRIVATE_BRANCH_REQUIRED")
        self.root.mkdir(mode=0o700)
        self.live = self.root / "controls"
        self.archive = self.root / "archive" if archive_root is None else Path(archive_root).absolute()
        require(not os.path.lexists(self.archive) and not any(p.is_symlink() for p in self.archive.parents)
                and self.archive.parent.resolve(strict=True) == self.archive.parent,
                "CAS_COMPARISON_FRESH_PRIVATE_ARCHIVE_REQUIRED")
        self.live.mkdir(mode=0o700); self.archive.mkdir(mode=0o700)
        fd = os.open(self.live / "writer.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.observations, self.observed_high_water, self.current_index = Counter(), 0, 0
        self.owner = _ObservedRetention(self.live, archive_root=self.archive,
            archive_format="COMPONENT_V3", sample=self._sample, fault_inject=fault_inject)

    def _sample(self, stage):
        archive = _inventory(self.archive)
        controls = _inventory(self.live)
        self.observed_high_water = max(self.observed_high_water,
            archive["allocated_bytes_including_directories"] + controls["allocated_bytes_including_directories"]
            + self.root.stat().st_blocks * 512)
        self.observations[stage] += 1
        require(max(archive["logical_bytes_including_directories"], archive["allocated_bytes_including_directories"])
                <= CONTRACT["archive_limit_bytes"], "CAS_COMPARISON_ORIGINAL_ARCHIVE_QUOTA_EXCEEDED")

    def archive_originals(self, originals, *, native_pins=()):
        require(type(originals) is dict and set(originals) == components.MEMBERS
                and all(type(raw) is bytes for raw in originals.values()), "CAS_COMPARISON_EXACT_FIVE_ORIGINAL_MEMBERS_REQUIRED")
        manifest_sha = components.sha(originals["manifest.json"])
        manifest = components.validate_materialized(originals, expected_manifest_sha256=manifest_sha)
        self.owner.native_pins, self.owner.current_originals = frozenset(native_pins), originals
        archive_lock = None
        try:
            archive_lock = self.owner._lock_archive()
            context = components.ComponentArchive(self.owner)
            self.owner._archive_inventory()
            head = self.owner._archive_checkpoint()
            ident, path = manifest["generation_id"], self.archive / (manifest["generation_id"] + ".recipe.gz")
            external = self.archive / (ident + ".receipt.json")
            if external.exists():
                receipt, _ = self.owner._read_control(external)
                require(context.restore(receipt)[0] == originals, "CAS_COMPARISON_ORIGINAL_BYTES_CHANGED")
                self.owner._verify_archive(receipt)
                self.owner._advance_archive_checkpoint(receipt)
                self.owner._clear_archive_build(receipt)
                return self._result(context, receipt, new_recipe_bytes=0, new_pack_bytes=0)
            if path.exists():
                recipe_wire, _ = components.read(path, maximum=components.MAX_RECIPE_BYTES)
                packed, pack_name = None, None
            elif self.slices:
                recipe_wire, packed, pack_name = context._comparison_slice_plan(originals, manifest, manifest_sha)
            else:
                options, catalog, _ = context._original_proposals(originals, manifest)
                recipe_wire, packed, pack_name = context._select_plan(options, catalog, manifest, manifest_sha)
                require(components.loads(components.inflate(recipe_wire, maximum=components.MAX_RECIPE_BYTES))["schema"]
                        == components.RECIPE_SCHEMA, "CAS_COMPARISON_V3_BASELINE_FORMAT_CHANGED")
            if not path.exists():
                additions = len(recipe_wire) + (len(packed) if packed is not None else 0)
                self.owner._archive_inventory(additional_bytes=additions + 4 * 65536, additional_files=6)
                self.owner._write_archive_build_intent(ident, manifest_sha,
                    recipe_sha256=components.sha(recipe_wire), pack_name=pack_name)
                if packed is not None:
                    context._publish(self.archive / pack_name, packed, prefix=".cas-")
                    self.owner._fault("archive_after_publish_components")
                context._publish(path, recipe_wire, prefix=".recipe-")
                self.owner._fault("archive_after_publish_recipe")
            receipt = {"schema": components.ACK_SCHEMA, "archiver_id": components.ARCHIVER_ID,
                "generation_id": ident, "sequence": manifest["sequence"], "manifest_sha256": manifest_sha,
                "archive_sha256": components.sha(recipe_wire), "archive_uri": "local-private://" + path.name,
                "receipt_sequence": head["receipt_count"] + 1, "previous_receipt_digest": head["receipt_digest"],
                "source_as_of": manifest["as_of"], "archive_verified": True, "durable": True,
                "acknowledged_at": datetime.now(timezone.utc).isoformat()}
            restored, _ = context.restore(receipt)
            require(restored == originals, "CAS_COMPARISON_ORIGINAL_BYTES_CHANGED")
            self.owner._durable_control(self.archive / (ident + ".receipt.json"), receipt)
            self.owner._verify_archive(receipt)
            self.owner._advance_archive_checkpoint(receipt)
            self.owner._durable_control(self.live / ("archive-ack-" + ident + ".json"), receipt)
            self.owner._clear_archive_build(receipt)
            maintenance = self.owner._maintain_archive()
            return self._result(context, receipt, new_recipe_bytes=len(recipe_wire),
                new_pack_bytes=len(packed) if packed is not None else 0, maintenance=maintenance)
        finally:
            self.owner.current_originals = None
            if archive_lock is not None:
                os.close(archive_lock)

    def _result(self, context, receipt, *, new_recipe_bytes, new_pack_bytes, maintenance=None):
        recipe = context._recipe(receipt)
        graph = context.dependency_graph([receipt])
        self._sample("committed")
        reachable = sorted({name for node in graph.values() for name in node["packs"]})
        return {"receipt": receipt, "recipe_schema": recipe["schema"],
            "archive": _inventory(self.archive), "controls": _inventory(self.live),
            "observed_allocated_high_water_bytes": self.observed_high_water,
            "measurement_scope": "NATIVE_FAULT_AND_FSYNC_OBSERVATIONS_NOT_CONTINUOUS_PEAK",
            "native_pins": sorted(self.owner.native_pins), "native_maintenance": maintenance,
            "reachable_whole_pack_count": len(reachable),
            "reachable_whole_pack_names_sha256": components.sha(components.canonical(reachable)),
            "physical_catalog_records": context._catalog().physical_records,
            "new_recipe_bytes": new_recipe_bytes, "new_pack_bytes": new_pack_bytes,
            "source_payload_copies": 0}


class _ExistingCandidateArchive(EvidenceRetention):
    """Private producer ACK sink. Never encode or fall back to native V3.

    This is deliberately outside the product retention/writer API. The
    candidate has already published its receipt using the captured five bytes;
    source rotation still calls the unchanged native ACK verifier and GC.
    """
    def __init__(self, *args, comparison, **kwargs):
        self.comparison = comparison
        super().__init__(*args, **kwargs)

    def _recover_archive_build(self):
        pending = self._build_intent()
        if pending is not None:
            require((self.archive_root / (pending["generation_id"] + ".receipt.json")).exists(),
                    "CAS_COMPARISON_CANDIDATE_RECOVERY_REQUIRED_BEFORE_ACK")
        return super()._recover_archive_build()

    def _archive_generation(self, path):
        attempt = self.comparison
        attempt._verify_marker()
        require(self.archive_root == attempt.branches["V4"].archive and bool(attempt.cuts),
                "CAS_COMPARISON_CANDIDATE_ACK_CONTEXT_REQUIRED")
        cut = attempt.cuts[-1]
        candidate = cut["branches"]["V4"]
        require(candidate["status"] == "EXACT_ORIGINAL_BYTES_RESTORED",
                "CAS_COMPARISON_CANDIDATE_RED_NO_NATIVE_FALLBACK")
        require(attempt.pending_originals is not None, "CAS_COMPARISON_CANDIDATE_ACK_CONTEXT_REQUIRED")
        expected = candidate["receipt"]
        require(path.parent == self.root and path.name == "gen-" + expected["generation_id"],
                "CAS_COMPARISON_CANDIDATE_SOURCE_GENERATION_CHANGED")
        self._archive_inventory()
        receipt, _ = self._read_control(self.archive_root / (expected["generation_id"] + ".receipt.json"))
        require(receipt == expected, "CAS_COMPARISON_CANDIDATE_RECEIPT_CHANGED")
        manifest, manifest_hash = self._read_control(path / "manifest.json")
        self._pins(())  # CURRENT digest, real directory and fallback/pin registry.
        current, _ = self._read_control(self.root / "CURRENT.json")
        require(manifest_hash == receipt["manifest_sha256"] == cut["original_member_sha256"]["manifest.json"]
                and manifest["generation_id"] == receipt["generation_id"]
                and manifest["sequence"] == receipt["sequence"]
                and all(current[key] == receipt[key] for key in ("generation_id", "sequence", "manifest_sha256")),
                "CAS_COMPARISON_CANDIDATE_SOURCE_CHANGED")
        require({name: self._file_hash(path / name) for name in components.MEMBERS}
                == cut["original_member_sha256"], "CAS_COMPARISON_CANDIDATE_SOURCE_CHANGED")
        restored, archived_manifest = components.ComponentArchive(self).restore(receipt)
        require(restored == attempt.pending_originals and archived_manifest == manifest,
                "CAS_COMPARISON_CANDIDATE_ORIGINAL_RESTORE_CHANGED")
        del restored, archived_manifest
        self._advance_archive_checkpoint(receipt)
        self._fault("private_candidate_before_native_ack")
        self._durable_control(self.root / ("archive-ack-" + receipt["generation_id"] + ".json"), receipt)
        self._fault("private_candidate_after_native_ack")
        self._clear_archive_build(receipt)
        self._maintain_archive()
        attempt.native_ack_count += 1
        attempt.pending_originals = None
        return receipt


class OriginalComparison:
    """Per-cut observer created only by authenticate_comparison(), below."""
    def __init__(self, root, authority, source_root, *, _ack_review=None, _producer_root=None):
        self.root, self.authority = Path(root), authority
        require(not os.path.lexists(self.root), "CAS_COMPARISON_EXCLUSIVE_ATTEMPT_REQUIRED")
        self.root.mkdir(mode=0o700)
        marker = components.canonical({"schema": "rc6.original-cas-private-attempt.v1",
            "nonce": os.urandom(16).hex(), "authority": authority,
            "runtime_validated": False, "native_v4_write_enabled": False, "real_orders_sent": 0})
        self.marker = self.root / "SESSION.json"
        fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(marker); stream.flush(); os.fsync(stream.fileno())
        self.marker_sha256 = components.sha(marker)
        self.fixture_identity = canonical_fixture_identity(source_root)
        self.ack_review = _ack_review
        self.producer_root = Path(_producer_root).absolute() if _producer_root is not None else None
        self.native_live = None
        self.pending_originals, self.native_ack_count = None, 0
        self.branches = {"V3": _PrivateBranch(self.root / "v3", slices=False)}
        if _ack_review is None:
            self.branches["V4"] = _PrivateBranch(self.root / "v4", slices=True)
        self.cuts, self.failures, self.first_operational_hashes = [], {}, None
        self.clocks = horizon_schedule(datetime.fromisoformat(CONTRACT["preopen"]).date())

    def _verify_marker(self):
        require(components.sha(_read(self.marker, maximum=MAX_INDEX_BYTES)) == self.marker_sha256,
                "CAS_COMPARISON_PRIVATE_ATTEMPT_MARKER_CHANGED")

    def bind_native_archive(self, live_root, archive_root):
        """Bind one reviewed private candidate to the original producer's sink."""
        self._verify_marker()
        require(self.ack_review is not None and self.producer_root is not None
                and self.native_live is None and "V4" not in self.branches,
                "CAS_COMPARISON_AUTHENTICATED_PRIVATE_ACK_REVIEW_REQUIRED")
        from rc6_shadow_runtime.persistence import shadow_evidence_root, shadow_archive_root
        database = self.producer_root / "data/paper_v17/observer_v17.db"
        live, archive = Path(live_root).absolute(), Path(archive_root).absolute()
        require(live == shadow_evidence_root(database, {}) and archive == shadow_archive_root(database, {})
                and live.is_relative_to(self.producer_root) and archive.is_relative_to(self.producer_root)
                and not live.exists() and not archive.exists(), "CAS_COMPARISON_EXACT_FRESH_NATIVE_NAMESPACE_REQUIRED")
        self.branches["V4"] = _PrivateBranch(self.root / "v4", slices=True, archive_root=archive)
        self.native_live = live

    def native_ack_owner(self, live_root, archive_root, **policy):
        self._verify_marker()
        require(self.native_live is not None and Path(live_root).absolute() == self.native_live
                and Path(archive_root).absolute() == self.branches["V4"].archive,
                "CAS_COMPARISON_REVIEWED_PRIVATE_ACK_SINK_REQUIRED")
        require(policy.get("maximum_bytes", 128 * 1024**2) == 128 * 1024**2
                and policy.get("maximum_files", 512) == 512
                and policy.get("archive_maximum_bytes", 512 * 1024**2) == 512 * 1024**2
                and policy.get("archive_maximum_files", 32768) == 32768
                and policy.get("archive_format") == "COMPONENT_V3",
                "CAS_COMPARISON_ORIGINAL_NATIVE_ACK_POLICY_REQUIRED")
        return _ExistingCandidateArchive(live_root, archive_root=archive_root, comparison=self, **policy)

    def _archive_pair(self, originals, *, native_pins):
        """One captured dictionary, two catalogs; baseline quota RED is sticky."""
        index, results = len(self.cuts), {}
        for name, branch in self.branches.items():
            if name in self.failures:
                results[name] = {"status": "BLOCKED_AFTER_ORIGINAL_QUOTA_RED", "error": self.failures[name]}
                continue
            try:
                results[name] = {"status": "EXACT_ORIGINAL_BYTES_RESTORED", **branch.archive_originals(originals, native_pins=native_pins)}
            except RetentionPressure as error:
                self.failures[name] = {"reason": str(error), "cut_index": index}
                results[name] = {"status": "RED_ORIGINAL_QUOTA_PRESERVED", "error": self.failures[name]}
        return results

    def observe(self, originals, *, cut, native_pins):
        self._verify_marker()
        require(self.ack_review is None or self.native_live is not None,
                "CAS_COMPARISON_REVIEWED_PRIVATE_ACK_SINK_REQUIRED")
        require(self.pending_originals is None, "CAS_COMPARISON_PREVIOUS_CANDIDATE_ACK_REQUIRED")
        index = len(self.cuts)
        require(index < 1202 and cut["tick_index"] == index and cut["as_of"] == self.clocks[index].isoformat()
                and cut["catalog_ready_count"] == 1200 and type(cut["real_orders_sent"]) is int
                and cut["real_orders_sent"] == cut["provider_requests"] == 0 and cut["real_routes"] == "NOT_CALLED",
                "CAS_COMPARISON_ORIGINAL_SCHEDULE_OR_SAFETY_CHANGED")
        hashes = {name: components.sha(raw) for name, raw in originals.items()}
        require(hashes == cut["original_member_sha256"], "CAS_COMPARISON_NATIVE_MEMBER_BINDING_CHANGED")
        if index == 1:
            self.first_operational_hashes = hashes
        start = time.monotonic()
        results = self._archive_pair(originals, native_pins=native_pins)
        record = {"cut_index": index, "as_of": cut["as_of"], "phase": cut["phase"],
            "checkpoint_reused": cut["checkpoint_reused"], "original_member_sha256": hashes,
            "member_bytes": {name: len(raw) for name, raw in originals.items()}, "branches": results,
            "comparison_wall_seconds": time.monotonic() - start,
            "process_lifetime_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}
        self.cuts.append(record)
        if self.native_live is not None:
            require(results["V4"]["status"] == "EXACT_ORIGINAL_BYTES_RESTORED",
                    "CAS_COMPARISON_CANDIDATE_RED_NO_NATIVE_FALLBACK")
            self.pending_originals = originals
        return record

    def finish(self, *, original_complete):
        complete = bool(original_complete and len(self.cuts) == 1202)
        restored = {}
        if complete:
            for name, branch in self.branches.items():
                if name in self.failures:
                    continue
                ident = self.cuts[1]["branches"][name]["receipt"]["generation_id"]
                members = branch.owner.restore_generation(ident)["members"]
                require({member: components.sha(raw) for member, raw in members.items()} == self.first_operational_hashes,
                        "CAS_COMPARISON_FIRST_OPERATIONAL_ORIGINAL_RESTORE_FAILED")
                restored[name] = True
        return {"schema": SCHEMA, "classification": "ORIGINAL_BYTE_DEVELOPMENT_COMPARISON_NOT_G5",
            "status": "COMPLETE_DEVELOPMENT_COMPARISON" if complete else "INCOMPLETE_ORIGINAL_HORIZON",
            "original_contract": dict(CONTRACT), "fixture_identity": self.fixture_identity,
            "authority": self.authority, "actual_original_cuts": len(self.cuts), "cuts": self.cuts,
            "branch_failures": self.failures, "first_operational_cut_restored": restored,
            "private_producer_ack_review": self.ack_review, "native_candidate_ack_count": self.native_ack_count,
            "source_rotation_archive": "REVIEWED_PRIVATE_CANDIDATE" if self.native_live is not None else None,
            "certified_physical_bound_bytes": None, "contractual_equivalence_demonstrated": False,
            "unclosed_physical_obligations": ["CONTINUOUS_OR_VERIFIED_ALL_STATE_HIGH_WATER",
                "DIRECTORY_CONTROL_TEMPORARY_AND_INTERRUPTED_RECOVERY_ENVELOPES",
                "AUTHENTIC_ALL1202_GLOBAL_MODEL_AND_EXACT_DEPLOY_ARTIFACT_REVIEW"],
            "native_v4_write_enabled": False, "real_orders_sent": 0, "runtime_validated": False}


def publish_result(path, result):
    """Lossless bounded RAW, separate from the 256 KiB owned-FIN control."""
    path = Path(path)
    wire = components.canonical(result)
    require(len(wire) <= MAX_RESULT_BYTES and path.parent.resolve(strict=True) == path.parent
            and not any(p.is_symlink() for p in path.parents), "CAS_COMPARISON_RESULT_BOUND_OR_CUSTODY_INVALID")
    wire = gzip.compress(wire, mtime=0, compresslevel=1)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(wire); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"path": str(path), "sha256": components.sha(wire), "bytes": len(wire)}


def authenticate_comparison(manifest_path, *, source_sha, source_tree, source_root, output_root, producer_root=None):
    """Authenticate exact predecessor artifacts; honor the current G5 hold."""
    from scripts import rc6_architectural_gates as gates
    from scripts import rc6_archive_reader_review as readers
    gates.require_horizon_model_before_material("G5")
    wire = _read(manifest_path, maximum=MAX_INDEX_BYTES)
    manifest = components.loads(wire)
    require(manifest.get("schema") == "rc6.original-cas-comparison-admission.v2"
            and manifest.get("source_sha") == source_sha and manifest.get("source_tree") == source_tree
            and producer_root is not None and manifest.get("producer_namespace_root") == str(Path(producer_root).absolute())
            and not os.path.lexists(producer_root)
            and manifest.get("original_contract") == components.loads(components.canonical(dict(CONTRACT))),
            "CAS_COMPARISON_EXACT_SOURCE_AND_ORIGINAL_CONTRACT_REQUIRED")
    output = Path(output_root).absolute()
    require(not os.path.lexists(output) and not any(p.is_symlink() for p in output.parents)
            and output.parent.resolve(strict=True) == output.parent
            and stat.S_IMODE(output.parent.lstat().st_mode) == 0o700,
            "CAS_COMPARISON_FRESH_OWNED_ADMISSION_NAMESPACE_REQUIRED")
    output.mkdir(mode=0o700)
    verified = gates.verify_manifest(manifest["prerequisites"], source_sha=source_sha, source_tree=source_tree,
        target_gate="G5", evidence_root=output / "authenticated-prerequisites")
    review = manifest.get("reader_review")
    require(type(review) is dict and set(review) == {"gate", "path", "sha256"}
            and review["gate"] in {"G1.311", "G1.312"}, "CAS_COMPARISON_HASH_BOUND_READER_REVIEW_REQUIRED")
    evidence = verified["evidence_files"][review["gate"]]
    raw = gates.capture_member(evidence["archive"], review["path"], maximum=MAX_INDEX_BYTES)
    require(components.sha(raw) == review["sha256"], "CAS_COMPARISON_READER_REVIEW_DIGEST_CHANGED")
    record = components.loads(raw)
    require(record.get("schema") == REVIEW_SCHEMA and record.get("source_sha") == source_sha
            and record.get("source_tree") == source_tree and record.get("recipe_schemas") == [components.RECIPE_SCHEMA, components.SLICE_RECIPE_SCHEMA]
            and record.get("native_write_recipe_schema") == components.RECIPE_SCHEMA
            and set(record.get("executed_reader_case_names", [])) >= READER_CASES
            and record.get("scope") == "PRIVATE_DEVELOPMENT_COMPARISON_NOT_NATIVE_V4_ENABLEMENT",
            "CAS_COMPARISON_READER_CAPABILITY_REVIEW_INCOMPLETE")
    ack_review = manifest.get("private_producer_ack_review")
    require(type(ack_review) is dict and set(ack_review) == {"gate", "path", "sha256"}
            and ack_review["gate"] in {"G1.311", "G1.312"}, "CAS_COMPARISON_HASH_BOUND_PRIVATE_ACK_REVIEW_REQUIRED")
    ack_wire = gates.capture_member(verified["evidence_files"][ack_review["gate"]]["archive"],
        ack_review["path"], maximum=MAX_INDEX_BYTES)
    require(components.sha(ack_wire) == ack_review["sha256"], "CAS_COMPARISON_PRIVATE_ACK_REVIEW_DIGEST_CHANGED")
    ack_record = components.loads(ack_wire)
    require(ack_record.get("schema") == ACK_REVIEW_SCHEMA and ack_record.get("scope") == ACK_SCOPE
            and ack_record.get("source_sha") == source_sha and ack_record.get("source_tree") == source_tree
            and ack_record.get("native_write_recipe_schema") == components.RECIPE_SCHEMA
            and ack_record.get("original_contract") == components.loads(components.canonical(dict(CONTRACT)))
            and ack_record.get("live_limit_bytes") == 128 * 1024**2 and ack_record.get("live_limit_entries") == 512
            and ack_record.get("native_fallback_allowed") is False and ack_record.get("g5_qualification_allowed") is False
            and set(ack_record.get("executed_ack_case_names", [])) >= ACK_CASES,
            "CAS_COMPARISON_PRIVATE_ACK_CONTRACT_REVIEW_INCOMPLETE")
    inventory = readers.source_inventory(source_root, source_sha=source_sha, source_tree=source_tree)
    for epoch in ("G1.311", "G1.312"):
        row = next(row for row in verified["authenticated_receipts"] if row["gate"] == epoch)
        reference = row["native_evidence"]["execution"]
        execution = gates.capture_member(verified["evidence_files"][epoch]["archive"], reference["path"])
        require(components.sha(execution) == reference["sha256"], "CAS_COMPARISON_ACTUAL_EXECUTION_CHANGED")
        observed_execution = components.loads(execution)
        readers.executed_reader_nodes(observed_execution, source_sha=source_sha, source_tree=source_tree)
        if epoch == review["gate"]:
            readers.validate_review(record, inventory, observed_execution, execution,
                                    source_sha=source_sha, source_tree=source_tree)
        items = observed_execution["items"]
        names = _executed_case_names(items, "tests/test_rc6_archive_slice_reuse.py::")
        require(READER_CASES <= names, "CAS_COMPARISON_ACTUAL_DUAL_READER_CASES_MISSING")
        ack_names = _executed_case_names(items, "tests/test_rc6_cas_original_comparison.py::")
        require(ACK_CASES <= ack_names, "CAS_COMPARISON_ACTUAL_DUAL_PRIVATE_ACK_CASES_MISSING")
    authority = {"admission_manifest_sha256": components.sha(wire), "source_sha": source_sha,
        "source_tree": source_tree, "reader_review_sha256": review["sha256"],
        "all_consumers_inventory_sha256": inventory["inventory_sha256"],
        "private_producer_ack_review_sha256": ack_review["sha256"], "producer_namespace_root": str(Path(producer_root).absolute()),
        "artifact_origins": verified["artifact_origins"], "native_v4_write_capability": "BLOCKED"}
    return OriginalComparison(output / "private-comparison", authority, source_root,
        _ack_review={**ack_review, "scope": ACK_SCOPE}, _producer_root=producer_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-policy", type=Path, required=True)
    parser.add_argument("--member-index", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    args = parser.parse_args(argv)
    print(components.canonical(audit_original_artifact(args.audit_policy, args.member_index, args.artifact, args.artifact_id)).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
