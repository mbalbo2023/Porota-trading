"""Native private codec fixtures and receipt guards; never CI image approval."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import time

import pytest

from scripts import rc6_archive_v3_image_smoke as smoke
from scripts.porota_artifact_provenance import canonical_bytes, create_source_manifest


ROOT = Path(__file__).resolve().parents[1]


def synthetic_receipt(source, frozen):
    """Explicit metadata-only fixture for binders, not executed native evidence."""
    rows = {row["path"]: row for row in source["files"]}
    hashes = {name: rows[name]["sha256"] for name in smoke.SOURCE_PATHS}
    legacy = json.loads((ROOT / smoke.CODEC_FIXTURE).read_bytes())
    archived = json.loads((ROOT / smoke.ARCHIVE_FIXTURE).read_bytes())
    member_hashes = {name: row["sha256"] for name, row in archived["members"].items()}
    cases = {name: {"status": "GREEN_EXPECTED_RED" if i in {2, 3, 5, 8, 9} else "GREEN"}
             for i, name in enumerate(smoke.CASE_IDS)}
    cases[smoke.CASE_IDS[0]].update(wire_sha256=legacy["wire_sha256"],
        logical_sha256=legacy["logical_sha256"], logical_bytes=legacy["logical_bytes"])
    cases[smoke.CASE_IDS[1]].update(logical_sha256=legacy["logical_sha256"],
        logical_bytes=legacy["logical_bytes"], storage_schema="rc6.lossless-json-storage.v2")
    floating = smoke.canonical(smoke.float_boundary_payload(), ascii=True)
    cases[smoke.CASE_IDS[1]].update(signed_zero_and_finite_float_types_exact=True,
        finite_float_control_sha256=smoke.sha(floating), finite_float_control_bytes=len(floating))
    cases[smoke.CASE_IDS[2]].update(signature="SHADOW_STORAGE_GZIP_INVALID", public_hashes_resealed=True)
    cases[smoke.CASE_IDS[3]]["signature"] = "SHADOW_STORAGE_SCHEMA_UNSUPPORTED"
    cases[smoke.CASE_IDS[4]].update(dependency_depth=1, delta_pages=2, recovery_encoder_calls=0,
        full_sha256="1" * 64, target_sha256="2" * 64)
    cases[smoke.CASE_IDS[5]]["signature"] = "PACK_PREVIOUS_SHA256_MISMATCH"
    cases[smoke.CASE_IDS[6]].update(verification_level=smoke.V2_LEVEL,
        source_bytes_and_all_stats_unchanged=True, member_sha256=member_hashes)
    cases[smoke.CASE_IDS[7]].update(verification_level=smoke.V3_LEVEL, generations=2,
        original_members_per_generation=5, source_bytes_and_all_stats_unchanged=True,
        repeated_restore_fresh_verification=True, member_sha256=[member_hashes, member_hashes])
    for index, signature in ((8, "RETENTION_COMPONENT_PACK_HASH_MISMATCH"),
                             (9, "FileNotFoundError:EXACT_DEPENDENCY")):
        cases[smoke.CASE_IDS[index]].update(restore_signature=signature, archive_signature=signature,
            new_ack_written=False, origin_deleted=False, source_bytes_and_all_stats_unchanged=True)
        cases[smoke.CASE_IDS[index]]["same_reader_success_before_mutation"] = True
    cases[smoke.CASE_IDS[10]].update(verification_level="BOUNDED_ARCHIVE_NAMESPACE_AND_CUSTODY_METADATA",
        isolated_python_flags=["-I", "-S"], repo_package_imports=0, source_bytes_and_all_stats_unchanged=True,
        source_sha256=hashes["rc6_shadow_runtime/archive_namespace.py"])
    return {"schema": smoke.SCHEMA, "status": "GREEN", "scope": smoke.SCOPE,
        "unit_fixture_scope": "EXPLICIT_SYNTHETIC_METADATA_ONLY_NOT_IMAGE_EXECUTION",
        "candidate_sha": frozen["candidate_sha"], "candidate_tree_sha": frozen["candidate_tree_sha"],
        "image_id_argument": frozen["image_id"],
        "image_id_authority": "CALLER_MUST_VERIFY_DOCKER_AND_EXTERNAL_GITHUB_TUPLE",
        "source_manifest_sha256": frozen["source_manifest_sha256"], "source_components": hashes,
        "imported_source_modules": {name[:-3].replace("/", "."): {"path": name, "sha256": hashes[name]}
                                    for name in smoke.SOURCE_PATHS[3:]},
        "legacy_baseline": deepcopy(smoke.BASELINE), "execution_uid": 1000, "execution_gid": 1000,
        "network_attempts": 0, "provider_requests": 0, "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "elapsed_seconds": 1.0, "deadline_seconds": 30, "rss_peak_bytes": 32 * 1024**2,
        "scratch_allocated_bytes": 1024**2, "output_limit_bytes": 65536,
        "cases": cases, "case_count": 11, "runtime_approval": False,
        "nine_hour_archive_capacity": "PENDING_SEPARATE_NATIVE_GATE",
        "large_producer_health_browser": "PENDING_SEPARATE_NATIVE_GATES"}


@pytest.fixture(scope="module")
def native_private_source(tmp_path_factory):
    """A fresh native Git checkout plus owned new files, without module overlays."""
    temporary = tmp_path_factory.mktemp("native-codec-source")
    repo = temporary / "source"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    previous = os.umask(0o022)
    try:
        subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", str(ROOT), str(repo)],
                       check=True, capture_output=True, env=env)
        head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", "--detach", head], check=True,
                       capture_output=True, env=env)
        for name in smoke.SOURCE_PATHS[:3]:
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
            target.chmod(0o644)
        for args in (("config", "user.email", "native-fixture@example.invalid"),
                     ("config", "user.name", "Private Native Codec Fixture"),
                     ("add", "scripts/rc6_archive_v3_image_smoke.py", "scripts/fixtures"),
                     ("commit", "--quiet", "--allow-empty", "-m", "private native source; no CI/image authority")):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)
    finally:
        os.umask(previous)
    manifest = create_source_manifest(repo)
    path = temporary / "source-manifest.json"
    path.write_bytes(canonical_bytes(manifest)); path.chmod(0o644)
    return repo, path, manifest


def test_native_tiny_cli_executes_all_legacy_v3_corruption_and_missing_dependency_controls(native_private_source):
    repo, path, manifest = native_private_source
    image_argument = "sha256:" + "a" * 64  # Caller argument only; no Docker was executed.
    completed = subprocess.run([sys.executable, "-B", "-m", "scripts.rc6_archive_v3_image_smoke",
        "--source-manifest", str(path), "--candidate-sha", manifest["candidate_sha"],
        "--tree-sha", manifest["candidate_tree_sha"], "--image-id", image_argument], cwd=repo,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stdout.decode() + completed.stderr.decode()
    assert len(completed.stdout) + len(completed.stderr) <= smoke.MAX_OUTPUT
    report = smoke.loads(completed.stdout)
    smoke.validate_report(report, candidate_sha=manifest["candidate_sha"], tree_sha=manifest["candidate_tree_sha"],
        image_id=image_argument, source_manifest_sha256=smoke.sha(path.read_bytes()),
        source_sha256={row["path"]: row["sha256"] for row in manifest["files"]},
        expected_uid=os.geteuid(), expected_gid=os.getegid())
    assert set(report["cases"]) == set(smoke.CASE_IDS) and report["runtime_approval"] is False
    assert report["cases"][smoke.CASE_IDS[8]]["new_ack_written"] is False
    assert report["cases"][smoke.CASE_IDS[9]]["origin_deleted"] is False
    receipt = path.parent / "native-cli.json"
    receipt.write_bytes(completed.stdout); receipt.chmod(0o644)


@pytest.mark.parametrize("mutation,signature", [("drift", "COMPONENT_MISMATCH"),
    ("alias", None), ("missing", None), ("mode", "COMPONENT_MISMATCH"),
    ("duplicate", "SOURCE_DUPLICATE"), ("metadata_resealed_wrong_head", "SOURCE_BINDING")])
def test_source_guard_rejects_native_git_source_drift_before_runtime_imports(native_private_source, tmp_path, mutation, signature):
    repo, path, manifest = native_private_source
    private = tmp_path / "source"
    private.mkdir()
    for name in smoke.SOURCE_PATHS:
        target = private / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target); target.chmod(0o644)
    altered = deepcopy(manifest)
    victim = private / smoke.SOURCE_PATHS[0]
    if mutation == "drift": victim.write_bytes(victim.read_bytes() + b"\n# changed source\n")
    elif mutation == "alias": victim.unlink(); victim.symlink_to(repo / smoke.SOURCE_PATHS[0])
    elif mutation == "missing": victim.unlink()
    elif mutation == "mode": victim.chmod(0o600)
    elif mutation == "duplicate": altered["files"].append(deepcopy(altered["files"][0])); altered["file_count"] += 1
    else: altered["candidate_sha"] = "f" * 40
    unsigned = {key: value for key, value in altered.items() if key != "manifest_sha256"}
    altered["manifest_sha256"] = smoke.sha(canonical_bytes(unsigned))
    source = tmp_path / "manifest.json"; source.write_bytes(canonical_bytes(altered))
    with pytest.raises((smoke.SmokeError, OSError), match=signature):
        smoke.source_binding(private, source, candidate_sha=manifest["candidate_sha"],
            tree_sha=manifest["candidate_tree_sha"], deadline=time.monotonic() + 2)


def test_unknown_negative_error_and_encoder_failures_cannot_count_as_expected_red():
    for error in (ValueError("foreign error"), smoke.SmokeError("SMOKE_RESTORE_ENCODER_CALLED")):
        with pytest.raises(smoke.SmokeError):
            smoke.expected_failure(lambda: (_ for _ in ()).throw(error), signatures=("exact reason",))


def test_caught_network_attempt_cannot_leave_a_green_smoke_receipt(monkeypatch):
    def attempted(*_args, **_kwargs):
        try: socket.create_connection(("must-never-resolve.invalid", 443))
        except smoke.SmokeError: pass
        return {"status": "GREEN"}
    monkeypatch.setattr(smoke, "_run_smoke", attempted)
    with pytest.raises(smoke.SmokeError, match="NETWORK_ATTEMPTED"):
        smoke.run_smoke("unused", "unused", candidate_sha="a" * 40, tree_sha="b" * 40,
                        image_id="sha256:" + "c" * 64)


def test_bounded_noatime_reader_rejects_alias_hardlink_and_oversized_member(tmp_path):
    target = tmp_path / "source"; target.write_bytes(b"x" * 100)
    before = smoke.identity(target.stat())
    assert smoke.read(target, limit=100, deadline=time.monotonic() + 1)[0] == b"x" * 100
    assert smoke.identity(target.stat()) == before
    with pytest.raises(smoke.SmokeError, match="INPUT_LIMIT"):
        smoke.read(target, limit=99, deadline=time.monotonic() + 1)
    alias = tmp_path / "alias"; alias.symlink_to(target)
    with pytest.raises(OSError): smoke.read(alias, limit=100, deadline=time.monotonic() + 1)
    link = tmp_path / "link"; os.link(target, link)
    with pytest.raises(smoke.SmokeError, match="INPUT_CUSTODY"):
        smoke.read(link, limit=100, deadline=time.monotonic() + 1)


def test_native_legacy_fixtures_are_pinned_to_original_git_blobs_and_above_codec_threshold():
    subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor",
                    smoke.LEGACY_REACHABLE_ANCHOR, "HEAD"], check=True, capture_output=True)
    entries = subprocess.check_output(["git", "-C", str(ROOT), "ls-tree",
                                      smoke.LEGACY_REACHABLE_ANCHOR, "--", *smoke.LEGACY_GIT_BLOBS], text=True)
    assert {row.split("\t")[1]: (row.split()[0], row.split()[2]) for row in entries.splitlines()} == {
        name: ("100644", oid) for name, oid in smoke.LEGACY_GIT_BLOBS.items()}
    for name, expected in smoke.BASELINE["source_sha256"].items():
        raw = subprocess.check_output(["git", "-C", str(ROOT), "show", smoke.LEGACY_REACHABLE_ANCHOR + ":" + name])
        assert smoke.sha(raw) == expected
    codec = json.loads((ROOT / smoke.CODEC_FIXTURE).read_bytes())
    archive = json.loads((ROOT / smoke.ARCHIVE_FIXTURE).read_bytes())
    assert codec["baseline"] == archive["baseline"] == smoke.BASELINE
    assert codec["logical_bytes"] > 256 * 1024 and codec["wire"]["schema"] == "rc6.lossless-json-storage.v1"
    assert set(archive["members"]) == smoke.MEMBERS
    assert all(smoke.decode_record(row) for row in archive["members"].values())
    assert all(smoke.sha((ROOT / name).read_bytes()) == digest for name, digest in smoke.FIXTURE_SHA256.items())
    assert codec["wire_sha256"] == smoke.LEGACY_WIRE_SHA256
    assert {name: row["sha256"] for name, row in archive["members"].items()} == smoke.LEGACY_MEMBER_SHA256


def test_resealed_legacy_metadata_does_not_replace_original_fixture_bytes(tmp_path):
    value = json.loads((ROOT / smoke.CODEC_FIXTURE).read_bytes())
    value["logical_sha256"] = "f" * 64
    target = tmp_path / smoke.CODEC_FIXTURE; target.parent.mkdir(parents=True)
    target.write_bytes(canonical_bytes(value))
    with pytest.raises(smoke.SmokeError, match="LEGACY_FIXTURE_BYTES_CHANGED"):
        smoke.fixture(tmp_path, smoke.CODEC_FIXTURE, value["schema"], deadline=time.monotonic()+1)
