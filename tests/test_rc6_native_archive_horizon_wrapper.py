"""Wrapper contracts only: no physical1201-tick horizon is executed here.

Controlled clocks and small metadata negatives test the measurement boundary;
their PASS can never replace the separate native wheel/GC/storage execution.
"""
from pathlib import Path
import hashlib
import json
import os
import runpy
import socket
import subprocess

import pytest


@pytest.fixture
def probe():
    return runpy.run_path(str(Path(__file__).resolve().parents[1]/
        "docs/audits/rc6-convergence-persistence-evidence/native_archive_v3_profile_probe_v2.py"))


def test_restart_factory_recovery_is_charged_to_the_original_full_cycle_clock(probe, monkeypatch, record_property):
    record_property("evidence_scope", "CONTROLLED_WRAPPER_CLOCK_NOT_NATIVE_HORIZON")
    clock = [100.]
    monkeypatch.setattr(probe["time"], "monotonic", lambda: clock[0])
    monkeypatch.setattr(probe["time"], "process_time", lambda: 10.)
    worker, rebuilt, restarts = object(), object(), []
    def factory():
        clock[0] += 31.
        return rebuilt
    actual, started, cpu = probe["start_cycle"](worker, index=361, ticks=1201, factory=factory, restarts=restarts)
    assert actual is rebuilt and restarts == [361] and cpu == 10.
    # Constructor/recovery alone has already exhausted the existing30s.
    assert started == 100. and clock[0]-started == 31.


def test_non_restart_cycle_does_not_construct_a_new_worker_or_change_scope(probe, monkeypatch, record_property):
    record_property("evidence_scope", "CONTROLLED_WRAPPER_CLOCK_NOT_NATIVE_HORIZON")
    monkeypatch.setattr(probe["time"], "monotonic", lambda: 100.)
    worker, restarts = object(), []
    actual, started, _ = probe["start_cycle"](worker, index=1, ticks=1201,
        factory=lambda: pytest.fail("Non-restart constructed worker"), restarts=restarts)
    assert actual is worker and started == 100. and restarts == []


@pytest.mark.parametrize("ticks,executed", ((4,4),(1201,4),(1201,1201)))
def test_four_cuts_or_an_incomplete_wheel_cannot_claim_horizon_complete(probe, ticks, executed, record_property):
    record_property("evidence_scope", "METADATA_ONLY_NO1201_NATIVE_WHEEL_EXECUTION")
    flags = probe["completion_flags"]({"cuts":[{}]*executed,"ticks_requested":ticks,
        "execution_complete":True,"source_database_unchanged":True,"code_source_unchanged":True,"provider_requests":0})
    assert flags["execution_complete"] is True
    assert flags["horizon_complete"] is flags["complete"] is flags["acceptance_complete"] is False


@pytest.mark.parametrize("missing", ("projection.sqlite","manifest.json"))
def test_equal_legacy_or_incomplete_member_snapshots_are_rejected(probe, missing, record_property):
    record_property("evidence_scope", "MEMBER_SET_CONTRACT_NOT_NATIVE_ARCHIVE_RECOVERY")
    members = {name:b"original" for name in probe["ORIGINAL_MEMBERS"] if name != missing}
    # Snapshot==restore equality would have accepted the old four-member shape.
    assert members == dict(members)
    with pytest.raises(AssertionError, match="EXACT_FIVE_ORIGINAL_MEMBERS_REQUIRED"):
        probe["exact_original_members"](members)


def test_current_five_member_shape_is_preserved_as_metadata_only_control(probe, record_property):
    record_property("evidence_scope", "MEMBER_SET_CONTRACT_NOT_NATIVE_ARCHIVE_RECOVERY")
    probe["exact_original_members"]({name:b"original" for name in probe["ORIGINAL_MEMBERS"]})


def test_even1202_placeholder_cuts_cannot_replace_a_measured_native_contract(probe, record_property):
    record_property("evidence_scope", "METADATA_REJECTION_NOT_NATIVE_HORIZON")
    flags = probe["completion_flags"]({"cuts":[{}]*1202,"ticks_requested":1201,
        "execution_complete":True,"source_database_unchanged":True,"code_source_unchanged":True,"provider_requests":0})
    assert flags["horizon_complete"] is flags["complete"] is flags["acceptance_complete"] is False


def test_a_fifo_index_is_rejected_without_opening_a_blocking_reader(probe, tmp_path, record_property):
    record_property("evidence_scope", "NATIVE_PRIVATE_FIFO_REJECTION_NOT_NATIVE_HORIZON")
    index = tmp_path/"source.index.json"
    os.mkfifo(index,0o600)
    with pytest.raises(ValueError,match="SOURCE_ALIAS_INVALID"):
        probe["raw"](index)


@pytest.mark.parametrize("method", ("send","sendall"))
def test_internet_socket_send_methods_are_blocked_before_transport_without_connecting(probe, tmp_path, method, record_property):
    record_property("evidence_scope", "REAL_UNCONNECTED_INET_SOCKET_INTERPOSITION_NO_NETWORK_OR_NATIVE_HORIZON")
    original = getattr(socket.socket,method)
    guard = probe["PrivateExecutionGuard"](tmp_path,tmp_path/"fixture.sqlite")
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            with pytest.raises(AssertionError,match="NATIVE_PROFILE_NETWORK_FORBIDDEN"):
                getattr(sock,method)(b"blocked")
        assert guard.network_attempt_count == 1
    finally:
        guard.close()
    assert getattr(socket.socket,method) is original


def test_local_unix_socket_ipc_remains_allowed_and_guards_are_restored(probe, tmp_path, record_property):
    record_property("evidence_scope", "NATIVE_LOCAL_UNIX_SOCKET_CONTROL_NOT_PROVIDER_TRANSPORT_OR_HORIZON")
    original = socket.socket.sendall
    guard = probe["PrivateExecutionGuard"](tmp_path,tmp_path/"fixture.sqlite")
    try:
        left,right = socket.socketpair()
        with left,right:
            left.sendall(b"local")
            assert right.recv(5) == b"local"
        assert guard.network_attempt_count == 0
    finally:
        guard.close()
    assert socket.socket.sendall is original


@pytest.fixture
def miniature_git_source(probe, tmp_path):
    repository, source = tmp_path/"git-object-authority", tmp_path/"source-archive"
    repository.mkdir(); source.mkdir()
    def git(*arguments):
        return subprocess.check_output(["git", "--no-replace-objects", "-C", str(repository),
            "-c", "user.name=RC6 offline fixture", "-c", "user.email=rc6-offline@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *arguments])
    git("init", "--quiet")
    for name, body in ((probe["PROFILE_PATH"], b"# pinned miniature wrapper\n"),
                       ("source_module.py", b"VALUE = 'original'\n")):
        for root in (repository, source):
            destination = root/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body); destination.chmod(0o644)
    git("add", "--", probe["PROFILE_PATH"], "source_module.py")
    git("commit", "--quiet", "-m", "Native Git object qualification fixture")
    source_sha, source_tree = git("rev-parse", "HEAD").decode().strip(), git("rev-parse", "HEAD^{tree}").decode().strip()
    pin = {"schema":"rc6.complete-archive-source-pin.v1", "source_sha":source_sha,
        "source_tree":source_tree,"overlay_count":0,
        "raw_git_commit_sha256":hashlib.sha256(git("cat-file","commit",source_sha)).hexdigest(),
        **probe["code_inventory"](source)}
    return repository, source, source_sha, source_tree, pin, git


def test_complete_source_qualification_uses_real_git_objects_and_exact_modes(probe, miniature_git_source, record_property):
    record_property("evidence_scope", "NATIVE_MINIATURE_GIT_QUALIFICATION_NOT_NATIVE_WORKER_HORIZON")
    repository, source, source_sha, source_tree, pin, _ = miniature_git_source
    inventory, authority = probe["verify_source"](repository,source,source_sha,source_tree,pin)
    assert inventory == {key:pin[key] for key in ("files","modes","blob_ids")}
    assert authority["source_files"] == 2 and authority["source_sha"] == source_sha


@pytest.mark.parametrize("change", ("file_bytes","physical_mode","blob_id_index","mode_index","extra_file","empty_directory","commit_digest_index"))
def test_incomplete_or_drifted_source_pin_is_rejected_before_any_worker_fixture(probe, miniature_git_source, change, record_property):
    record_property("evidence_scope", "NATIVE_MINIATURE_GIT_ADVERSARY_NOT_NATIVE_WORKER_HORIZON")
    repository, source, source_sha, source_tree, pin, _ = miniature_git_source
    if change == "file_bytes":
        (source/"source_module.py").write_bytes(b"VALUE = 'changed'\n")
    elif change == "physical_mode":
        (source/"source_module.py").chmod(0o755)
    elif change == "blob_id_index":
        pin["blob_ids"]["source_module.py"] = "0"*40
    elif change == "mode_index":
        pin["modes"]["source_module.py"] = "100755"
    elif change == "extra_file":
        (source/"unexpected.py").write_bytes(b"# overlay\n")
    elif change == "empty_directory":
        (source/"untracked_empty").mkdir()
    else:
        pin["raw_git_commit_sha256"] = "0"*64
    with pytest.raises(ValueError, match="NATIVE_PROFILE_.*MISMATCH"):
        probe["verify_source"](repository,source,source_sha,source_tree,pin)


def test_rehashing_the_index_cannot_authorize_a_changed_uncommitted_blob(probe, miniature_git_source, record_property):
    record_property("evidence_scope", "NATIVE_MINIATURE_GIT_REBOUND_INDEX_NOT_NATIVE_WORKER_HORIZON")
    repository, source, source_sha, source_tree, pin, _ = miniature_git_source
    (source/"source_module.py").write_bytes(b"VALUE = 'rebound'\n")
    pin.update(probe["code_inventory"](source))
    with pytest.raises(ValueError, match="COMPLETE_SOURCE_HASH_MODE_OR_BLOB_MISMATCH"):
        probe["verify_source"](repository,source,source_sha,source_tree,pin)


def test_replace_refs_cannot_change_the_git_qualification_authority(probe, miniature_git_source, record_property):
    record_property("evidence_scope", "NATIVE_MINIATURE_GIT_REPLACEMENT_REJECTION_NOT_NATIVE_WORKER_HORIZON")
    repository, source, source_sha, source_tree, pin, git = miniature_git_source
    (repository/"source_module.py").write_bytes(b"VALUE = 'new commit'\n")
    git("add","--","source_module.py"); git("commit","--quiet","-m","Replacement target fixture")
    successor = git("rev-parse","HEAD").decode().strip()
    git("replace", source_sha, successor)
    with pytest.raises(ValueError, match="GIT_REPLACE_REF_FORBIDDEN"):
        probe["verify_source"](repository,source,source_sha,source_tree,pin)


@pytest.fixture
def miniature_members(probe):
    members = {name:b"original" for name in probe["ORIGINAL_MEMBERS"] if name != "manifest.json"}
    names = {"report":"report.json.gz", "checkpoint":"checkpoint.json.gz", "status":"status.json", "projection":"projection.sqlite"}
    manifest = {"schema":"rc6.shadow-evidence-generation.v2", "generation_id":"a"*32,"sequence":1,"as_of":"2026-10-05T13:35:00+00:00",
        "files":{role:{"name":name,"sha256":hashlib.sha256(members[name]).hexdigest()} for role,name in names.items()}}
    members["manifest.json"] = json.dumps(manifest,sort_keys=True).encode()
    pointer = {"generation_id":manifest["generation_id"],"sequence":1,
        "manifest_sha256":hashlib.sha256(members["manifest.json"]).hexdigest()}
    return members, pointer


def test_five_original_member_bytes_are_bound_to_manifest_and_actual_pointer(probe, miniature_members, record_property):
    record_property("evidence_scope", "MINIATURE_MANIFEST_BINDING_NOT_NATIVE_ARCHIVE_RECOVERY")
    members, pointer = miniature_members
    bound = probe["original_member_binding"](members,pointer=pointer,receipt=pointer,
        expected_as_of="2026-10-05T13:35:00+00:00")
    assert set(bound["original_member_sha256"]) == probe["ORIGINAL_MEMBERS"]
    assert bound["original_manifest_sha256"] == pointer["manifest_sha256"]


@pytest.mark.parametrize("change", ("payload","rebound_manifest","pointer","clock"))
def test_equal_five_member_dictionaries_do_not_bypass_original_hash_and_clock_bindings(probe, miniature_members, change, record_property):
    record_property("evidence_scope", "MINIATURE_MANIFEST_ADVERSARY_NOT_NATIVE_ARCHIVE_RECOVERY")
    members, pointer = miniature_members
    if change in ("payload","rebound_manifest"):
        members["projection.sqlite"] = b"changed projection"
        if change == "rebound_manifest":
            manifest = json.loads(members["manifest.json"])
            manifest["files"]["projection"]["sha256"] = hashlib.sha256(members["projection.sqlite"]).hexdigest()
            members["manifest.json"] = json.dumps(manifest,sort_keys=True).encode()
    elif change == "pointer":
        pointer["manifest_sha256"] = "0"*64
    assert members == dict(members)
    clock = "2026-10-05T13:35:01+00:00" if change == "clock" else "2026-10-05T13:35:00+00:00"
    with pytest.raises(AssertionError, match="NATIVE_PROFILE_ORIGINAL_MANIFEST_.*MISMATCH"):
        probe["original_member_binding"](members,pointer=pointer,expected_as_of=clock)


@pytest.fixture
def private_archive(tmp_path):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    for name, data in (("archive.lock", b""), ("a" * 32 + ".receipt.json", b"private synthetic metadata bytes")):
        descriptor = os.open(root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    return root


def private_archive_snapshot(root, fields):
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        before = {name: getattr(os.fstat(descriptor), name) for name in fields}
        result = {".": {"stat_fields": before}}
        with os.scandir(descriptor) as iterator:
            names = sorted(entry.name for entry in iterator)
        for name in names:
            member = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=descriptor)
            try:
                info = os.fstat(member)
                chunks = []
                while chunk := os.read(member, 65536):
                    chunks.append(chunk)
                result[name] = {"stat_fields": {field: getattr(info, field) for field in fields},
                                "sha256": hashlib.sha256(b"".join(chunks)).hexdigest()}
            finally:
                os.close(member)
        assert before == {name: getattr(os.fstat(descriptor), name) for name in fields}
        return result
    finally:
        os.close(descriptor)


def test_private_archive_admission_binds_actual_fixture_owner_without_metadata_writes(probe, private_archive, record_property):
    record_property("evidence_scope", "NATIVE_PRIVATE_NAMESPACE_OWNER_ADMISSION_NOT_FULL1201_HORIZON")
    before = private_archive_snapshot(private_archive, probe["FIELDS"])
    observed = probe["inspect_private_archive"](private_archive)
    assert observed["state"] == "WITHIN_QUOTA" and observed["owner_uid"] == os.geteuid()
    assert observed["max_bytes"] == 512 * 1024**2 and observed["maximum_files"] == 32768
    assert observed["files"] == 2 and observed["inodes"] == 3
    assert all(len(row["stat_fields"]) == 11 for row in before.values())
    assert before == private_archive_snapshot(private_archive, probe["FIELDS"])


@pytest.mark.parametrize("fault", ("directory_mode", "lock_mode", "member_mode", "hardlink"))
def test_private_archive_admission_keeps_mode_and_link_custody_fail_closed(probe, private_archive, fault, record_property):
    record_property("evidence_scope", "NATIVE_PRIVATE_NAMESPACE_CUSTODY_REJECTION_NOT_FULL1201_HORIZON")
    member = private_archive / ("a" * 32 + ".receipt.json")
    if fault == "directory_mode":
        private_archive.chmod(0o755)
    elif fault == "lock_mode":
        (private_archive / "archive.lock").chmod(0o644)
    elif fault == "member_mode":
        member.chmod(0o644)
    else:
        os.link(member, private_archive / ("b" * 32 + ".receipt.json"))
    before = private_archive_snapshot(private_archive, probe["FIELDS"])
    with pytest.raises(ValueError, match="^ARCHIVE_CUSTODY_INVALID$"):
        probe["inspect_private_archive"](private_archive)
    assert before == private_archive_snapshot(private_archive, probe["FIELDS"])


def test_explicit_wrong_archive_owner_remains_rejected_without_uid_changes(probe, private_archive, record_property):
    record_property("evidence_scope", "NATIVE_PRIVATE_NAMESPACE_WRONG_OWNER_POLICY_REJECTION_NOT_FULL1201_HORIZON")
    from rc6_shadow_runtime.archive_namespace import inspect_archive
    before = private_archive_snapshot(private_archive, probe["FIELDS"])
    with pytest.raises(ValueError, match="^ARCHIVE_CUSTODY_INVALID$"):
        inspect_archive(private_archive, owner_uid=os.geteuid() + 1)
    assert before == private_archive_snapshot(private_archive, probe["FIELDS"])
