"""Native private codec fixtures and receipt guards; never CI image approval."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import select
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time

import pytest

from scripts import rc6_archive_v3_image_smoke as smoke
from scripts import porota_artifact_provenance as provenance
from scripts.porota_artifact_provenance import canonical_bytes, create_source_manifest


ROOT = Path(__file__).resolve().parents[1]
MAX_PRIVATE_FULLGIT_BYTES = 256 * 1024**2


def private_git_environment():
    # An inherited GIT_DIR/common-dir/index/alternate must not redirect any
    # fixture operation. Source's native objects are the sole input authority.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    return environment


def private_git_command(directory, *arguments):
    return ["git", "--no-replace-objects", "-c", "protocol.allow=never", "-c", "gc.auto=0",
            "-c", "core.hooksPath=/dev/null", "-c", "pack.writeReverseIndex=false", "--git-dir=" + str(directory),
            "--work-tree=" + str(directory.parent), *arguments]


def private_git_bytes(directory, *arguments):
    result = subprocess.run(private_git_command(directory, *arguments),
        env=private_git_environment(), capture_output=True, check=True, timeout=30)
    assert len(result.stdout) <= 16 * 1024**2, "PRIVATE_FULLGIT_METADATA_BOUND"
    return result.stdout


def private_git_allocated(directory):
    """Count every owned Git inode without following links or changing atime."""
    total = 0
    queue = [directory]
    while queue:
        origin = queue.pop()
        descriptor = smoke.directory(origin)
        try:
            info = os.fstat(descriptor)
            assert info.st_uid == os.geteuid(), "PRIVATE_FULLGIT_OWNER_REQUIRED"
            total += info.st_blocks * 512
            for name in sorted(os.listdir(descriptor)):
                path = origin / name
                row = path.lstat()
                assert row.st_uid == os.geteuid(), "PRIVATE_FULLGIT_OWNER_REQUIRED"
                if stat.S_ISDIR(row.st_mode):
                    queue.append(path)
                else:
                    assert stat.S_ISREG(row.st_mode) and row.st_nlink == 1, "PRIVATE_FULLGIT_ALIAS_FORBIDDEN"
                    total += row.st_blocks * 512
            assert smoke.identity(os.fstat(descriptor)) == smoke.identity(info)
        finally:
            os.close(descriptor)
    assert total <= MAX_PRIVATE_FULLGIT_BYTES, "PRIVATE_FULLGIT_ALLOCATED_BOUND"
    return total


class PrivateGitStream:
    """Bounded read buffering with a deadline before every potentially blocking read."""
    def __init__(self, stream, deadline):
        self.stream, self.deadline, self.pending = stream, deadline, b""

    def read(self, maximum):
        if self.pending:
            chunk, self.pending = self.pending[:maximum], self.pending[maximum:]
            return chunk
        remaining = self.deadline - time.monotonic()
        assert remaining > 0 and select.select([self.stream], [], [], remaining)[0], \
            "PRIVATE_FULLGIT_READ_DEADLINE"
        return os.read(self.stream.fileno(), min(65536, maximum))

    def readline(self, maximum):
        line = bytearray()
        while len(line) < maximum:
            chunk = self.read(65536)
            if not chunk: break
            end = chunk.find(b"\n")
            if end >= 0:
                self.pending = chunk[end + 1:]
                line.extend(chunk[:end + 1]); break
            line.extend(chunk)
        assert len(line) <= maximum, "PRIVATE_FULLGIT_OBJECT_HEADER_BOUND"
        return bytes(line)


def copy_native_fullgit(source, target, *, raw_anchor=provenance.RAW_CUSTODY_SOURCE_SHA,
                        raw_roots=provenance.RAW_EVIDENCE_ROOTS,
                        legacy_anchor=smoke.LEGACY_REACHABLE_ANCHOR,
                        legacy_blobs=smoke.LEGACY_GIT_BLOBS):
    """Pack the exact native Source/RAW/legacy closure, not all old Git packs.

    All real ancestor commits remain present: no shallow frontier, graft,
    replacement, alternate or invented commit. Current Source has every tree
    and blob. Custody's recursive ls-tree requires all its trees, but only RAW
    blobs; the legacy anchor similarly needs its real trees and pinned blobs.
    Historical unrelated blobs/trees and unreachable Git bulk are not inputs
    to these readers. This is not a claim to restore every historical checkout.
    """
    assert not target.exists(), "PRIVATE_FULLGIT_EXCLUSIVE_TARGET_REQUIRED"
    source_fd = smoke.directory(source)
    os.close(source_fd)
    assert not (source / "objects/info/alternates").exists() and not (source / "info/grafts").exists(), \
        "PRIVATE_FULLGIT_FOREIGN_GRAPH_FORBIDDEN"
    assert private_git_bytes(source, "rev-parse", "--is-shallow-repository").strip() == b"false", \
        "PRIVATE_FULLGIT_COMPLETE_COMMIT_GRAPH_REQUIRED"
    assert private_git_bytes(source, "rev-parse", "--show-object-format").strip() == b"sha1"
    head = private_git_bytes(source, "rev-parse", "HEAD").decode().strip()
    commits = private_git_bytes(source, "rev-list", head).decode().splitlines()
    assert 0 < len(commits) <= 50_000 and len(commits) == len(set(commits))
    assert raw_anchor in commits and legacy_anchor in commits, "PRIVATE_FULLGIT_ANCHOR_NOT_ANCESTOR"
    objects = {oid: "commit" for oid in commits}

    def listing(anchor, *paths):
        rows = []
        arguments = ["ls-tree", "-rtz", "--full-tree", anchor]
        if paths: arguments += ["--", *paths]
        for literal in private_git_bytes(source, *arguments).split(b"\0"):
            if not literal: continue
            header, name = literal.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            assert kind in {"tree", "blob"} and mode in {"040000", "100644", "100755"}
            assert len(oid) == 40 and all(character in "0123456789abcdef" for character in oid)
            rows.append((name.decode("utf-8"), mode, kind, oid))
        return rows

    for anchor in {head, raw_anchor, legacy_anchor}:
        objects[private_git_bytes(source, "rev-parse", anchor + "^{tree}").decode().strip()] = "tree"
        for _name, _mode, kind, oid in listing(anchor):
            if kind == "tree" or anchor == head: objects[oid] = kind
    for _name, _mode, kind, oid in listing(raw_anchor, *raw_roots):
        objects[oid] = kind
    legacy = {name: (mode, oid) for name, mode, kind, oid in listing(legacy_anchor, *legacy_blobs)
              if kind == "blob"}
    assert legacy == {name: ("100644", oid) for name, oid in legacy_blobs.items()}, \
        "PRIVATE_FULLGIT_LEGACY_BLOB_REBOUND"
    objects.update({oid: "blob" for oid in legacy_blobs.values()})
    assert len(objects) <= 100_000, "PRIVATE_FULLGIT_OBJECT_COUNT_BOUND"

    target.mkdir(mode=0o700)
    subprocess.run(["git", "init", "--bare", "--quiet", "--template=", str(target)],
        env=private_git_environment(), check=True, capture_output=True, timeout=30)
    private_git_bytes(target, "config", "core.bare", "false")
    private_git_bytes(target, "config", "core.worktree", str(target.parent))
    pack_root = target / "objects/pack"
    # Index v2 uses 40 bytes/object plus a bounded header/trailer and optional
    # 64-bit offsets. Reserve its full physical space before streaming the pack.
    index_reserve = ((1024 + len(objects) * 40 + 40 + 4095) // 4096) * 4096
    identifiers = target / "private-object-list"
    fd = os.open(identifiers, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        for oid in sorted(objects): output.write(oid.encode("ascii") + b"\n")
    initial = private_git_allocated(target)
    assert initial + index_reserve <= MAX_PRIVATE_FULLGIT_BYTES, "PRIVATE_FULLGIT_ALLOCATED_BOUND"
    temporary_pack = pack_root / "private-stream.pack"
    pack_fd = os.open(temporary_pack, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    input_fd = None
    process = None
    digest, tail, header, logical = hashlib.sha1(), b"", b"", 0
    deadline = time.monotonic() + 30
    try:
        input_fd = os.open(identifiers, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
        initial = private_git_allocated(target)
        process = subprocess.Popen(private_git_command(source, "pack-objects", "--stdout",
            "--quiet", "--window=0", "--threads=1", "--no-reuse-delta"),
            env=private_git_environment(), stdin=input_fd, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        packed = PrivateGitStream(process.stdout, deadline)
        while chunk := packed.read(65536):
            logical += len(chunk)
            # Round the growing pack up to an actual allocation unit before
            # writing. Check actual blocks too: logical/sparse claims grant no credit.
            unit = os.fstatvfs(pack_fd).f_frsize
            rounded = ((logical + unit - 1) // unit) * unit
            assert initial + index_reserve + rounded <= MAX_PRIVATE_FULLGIT_BYTES, \
                "PRIVATE_FULLGIT_ALLOCATED_BOUND"
            if len(header) < 12: header = (header + chunk)[:12]
            pending = tail + chunk
            digest.update(pending[:-20]); tail = pending[-20:]
            view = memoryview(chunk)
            while view:
                written = os.write(pack_fd, view)
                assert written > 0, "PRIVATE_FULLGIT_WRITE_TRUNCATED"
                view = view[written:]
            assert initial + index_reserve + os.fstat(pack_fd).st_blocks * 512 <= MAX_PRIVATE_FULLGIT_BYTES, \
                "PRIVATE_FULLGIT_ALLOCATED_BOUND"
        assert process.wait(timeout=30) == 0, "PRIVATE_FULLGIT_PACK_EXIT_RED"
        assert header[:4] == b"PACK" and struct.unpack(">II", header[4:]) == (2, len(objects))
        assert digest.digest() == tail and len(tail) == 20, "PRIVATE_FULLGIT_PACK_DIGEST_MISMATCH"
    finally:
        if input_fd is not None: os.close(input_fd)
        os.close(pack_fd)
        if process is not None:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
            process.stdout.close()
    pack = pack_root / ("pack-" + tail.hex() + ".pack")
    assert not pack.exists()
    temporary_pack.rename(pack)
    private_git_bytes(target, "index-pack", "--index-version=2", "-o", str(pack.with_suffix(".idx")), str(pack))
    private_git_allocated(target)
    identifiers.unlink()  # Only this owned temporary input; no foreign cleanup.

    # Recompute every native imported object ID from streamed exact bytes.
    # A valid pack trailer or shape-correct Git filename alone grants no custody.
    reader = subprocess.Popen(private_git_command(target, "cat-file", "--batch"),
        env=private_git_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    decoded = PrivateGitStream(reader.stdout, time.monotonic() + 30)
    try:
        for oid, kind in sorted(objects.items()):
            reader.stdin.write(oid.encode("ascii") + b"\n"); reader.stdin.flush()
            fields = decoded.readline(128).decode("ascii").strip().split()
            assert len(fields) == 3 and fields[:2] == [oid, kind], "PRIVATE_FULLGIT_OBJECT_HEADER_INVALID"
            remaining = int(fields[2])
            assert 0 <= remaining <= MAX_PRIVATE_FULLGIT_BYTES, "PRIVATE_FULLGIT_OBJECT_BYTES_BOUND"
            hashed = hashlib.sha1((kind + " " + str(remaining)).encode("ascii") + b"\0")
            while remaining:
                chunk = decoded.read(min(65536, remaining))
                assert chunk, "PRIVATE_FULLGIT_OBJECT_TRUNCATED"
                hashed.update(chunk); remaining -= len(chunk)
            assert decoded.read(1) == b"\n" and hashed.hexdigest() == oid, "PRIVATE_FULLGIT_OBJECT_REBOUND"
        reader.stdin.close()
        assert reader.wait(timeout=30) == 0, "PRIVATE_FULLGIT_READER_EXIT_RED"
    finally:
        if reader.poll() is None: reader.kill(); reader.wait(timeout=5)
        for stream in (reader.stdin, reader.stdout):
            if not stream.closed: stream.close()
    private_git_bytes(target, "update-ref", "--no-deref", "HEAD", head)
    assert private_git_bytes(target, "rev-list", head).decode().splitlines() == commits
    for anchor in (raw_anchor, legacy_anchor):
        private_git_bytes(target, "merge-base", "--is-ancestor", anchor, head)
    assert not (target / "objects/info/alternates").exists() and not (target / "shallow").exists()
    assert not (target / "info/grafts").exists() and not private_git_bytes(target, "for-each-ref", "refs/replace")
    assert not private_git_bytes(target, "remote")
    return private_git_allocated(target)


def native_git_only_history(tmp_path):
    """Small Git-format counterexample; no RC6 Source or financial fixture."""
    repo = tmp_path / "git-only-input"
    repo.mkdir(mode=0o700)
    subprocess.run(["git", "init", "--quiet", "--template=", str(repo)],
                   env=private_git_environment(), check=True, capture_output=True)
    directory = repo / ".git"
    private_git_bytes(directory, "config", "user.email", "git-only@example.invalid")
    private_git_bytes(directory, "config", "user.name", "Git Closure Counterexample")
    raw_name, legacy_name = "raw/original.txt", "legacy/codec.txt"
    raw = b"Git custody payload; not a financial workload.\n"
    legacy = b"Git legacy anchor payload; not a financial workload.\n"
    for name, content in ((raw_name, raw), (legacy_name, legacy), ("obsolete.bin", os.urandom(1024**2))):
        path = repo / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    private_git_bytes(directory, "add", ".")
    private_git_bytes(directory, "commit", "--quiet", "-m", "real original Git commit")
    anchor = private_git_bytes(directory, "rev-parse", "HEAD").decode().strip()
    legacy_oid = private_git_bytes(directory, "rev-parse", anchor + ":" + legacy_name).decode().strip()
    old_oid = private_git_bytes(directory, "rev-parse", anchor + ":obsolete.bin").decode().strip()
    (repo / "obsolete.bin").unlink()
    private_git_bytes(directory, "add", ".")
    private_git_bytes(directory, "commit", "--quiet", "-m", "real Source without obsolete blob")
    # The input has another authentic but unreachable blob. It must not be
    # packed simply because a Git directory happens to contain it.
    extra = subprocess.check_output(private_git_command(directory, "hash-object", "-w", "--stdin"),
        input=os.urandom(1024**2), env=private_git_environment()).decode().strip()
    return directory, anchor, raw_name, legacy_name, legacy_oid, raw, legacy, (old_oid, extra)


def test_native_git_closure_omits_irrelevant_reachable_and_unreachable_bulk_without_losing_custody(tmp_path, monkeypatch):
    source, anchor, raw_name, legacy_name, legacy_oid, raw, legacy, omitted = native_git_only_history(tmp_path)
    # The source .git is over 2 MiB; the required native closure fits within
    # a deliberately smaller test-only budget. The production limit is unchanged.
    assert MAX_PRIVATE_FULLGIT_BYTES == 256 * 1024**2
    monkeypatch.setattr(sys.modules[__name__], "MAX_PRIVATE_FULLGIT_BYTES", 256 * 1024)
    target = tmp_path / "own-git"
    allocated = copy_native_fullgit(source, target, raw_anchor=anchor, raw_roots=("raw/",),
        legacy_anchor=anchor, legacy_blobs={legacy_name: legacy_oid})
    assert allocated < MAX_PRIVATE_FULLGIT_BYTES
    assert private_git_bytes(target, "rev-parse", "--is-shallow-repository").strip() == b"false"
    assert private_git_bytes(target, "rev-list", "HEAD") == private_git_bytes(source, "rev-list", "HEAD")
    assert private_git_bytes(target, "cat-file", "blob", anchor + ":" + raw_name) == raw
    assert private_git_bytes(target, "cat-file", "blob", anchor + ":" + legacy_name) == legacy
    assert private_git_bytes(target, "ls-tree", "-rtz", anchor) == private_git_bytes(source, "ls-tree", "-rtz", anchor)
    assert private_git_bytes(target, "ls-tree", "-rtz", "HEAD") == private_git_bytes(source, "ls-tree", "-rtz", "HEAD")
    for oid in omitted:
        assert private_git_bytes(source, "cat-file", "-t", oid).strip() == b"blob"
        result = subprocess.run(private_git_command(target, "cat-file", "-e", oid),
            env=private_git_environment(), capture_output=True, timeout=5)
        assert result.returncode != 0  # Actual missing object, no mocked Git output.
    assert len(list((target / "objects/pack").iterdir())) == 2


def test_native_git_closure_rejects_required_source_bytes_over_unchanged_physical_budget(tmp_path, monkeypatch):
    source, anchor, raw_name, legacy_name, legacy_oid, *_rest = native_git_only_history(tmp_path)
    required = source.parent / "required-source.bin"
    required.write_bytes(os.urandom(512 * 1024))
    private_git_bytes(source, "add", ".")
    private_git_bytes(source, "commit", "--quiet", "-m", "required real Source bytes")
    monkeypatch.setattr(sys.modules[__name__], "MAX_PRIVATE_FULLGIT_BYTES", 256 * 1024)
    target = tmp_path / "bounded-own-git"
    with pytest.raises(AssertionError, match="PRIVATE_FULLGIT_ALLOCATED_BOUND"):
        copy_native_fullgit(source, target, raw_anchor=anchor, raw_roots=("raw/",),
            legacy_anchor=anchor, legacy_blobs={legacy_name: legacy_oid})
    assert private_git_allocated(target) <= MAX_PRIVATE_FULLGIT_BYTES
    assert not (target / "index").exists()
    result = subprocess.run(private_git_command(target, "rev-parse", "--verify", "HEAD"),
        env=private_git_environment(), capture_output=True, timeout=5)
    assert result.returncode != 0  # A pressure failure cannot leave a usable fixture HEAD.


def test_native_git_stream_deadline_closes_a_stalled_partial_header_and_reaps_own_child():
    process = subprocess.Popen([sys.executable, "-I", "-S", "-c",
        "import os,time;os.write(1,b'x');time.sleep(2)"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        stream = PrivateGitStream(process.stdout, time.monotonic() + 0.1)
        with pytest.raises(AssertionError, match="PRIVATE_FULLGIT_READ_DEADLINE"):
            stream.readline(128)
    finally:
        if process.poll() is None: process.kill()
        process.wait(timeout=5); process.stdout.close()
    assert process.returncode is not None


def synthetic_receipt(source, frozen):
    """Explicit metadata-only fixture for binders, not executed native evidence."""
    rows = {row["path"]: row for row in source["files"]}
    hashes = {name: rows[name]["sha256"] for name in smoke.SOURCE_PATHS}
    legacy = json.loads((ROOT / smoke.CODEC_FIXTURE).read_bytes())
    archived = json.loads((ROOT / smoke.ARCHIVE_FIXTURE).read_bytes())
    member_hashes = {name: row["sha256"] for name, row in archived["members"].items()}
    cases = {name: {"status": "GREEN_EXPECTED_RED" if i in {2, 3, 5, 8, 9, 12, 13} else "GREEN"}
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
                             (9, "FileNotFoundError:EXACT_DEPENDENCY"),
                             (12, "RETENTION_COMPONENT_PACK_HASH_MISMATCH"),
                             (13, "FileNotFoundError:EXACT_DEPENDENCY")):
        cases[smoke.CASE_IDS[index]].update(restore_signature=signature, archive_signature=signature,
            new_ack_written=False, origin_deleted=False, source_bytes_and_all_stats_unchanged=True)
        cases[smoke.CASE_IDS[index]]["same_reader_success_before_mutation"] = True
    cases[smoke.CASE_IDS[10]].update(verification_level="BOUNDED_ARCHIVE_NAMESPACE_AND_CUSTODY_METADATA",
        isolated_python_flags=["-I", "-S"], repo_package_imports=0, source_bytes_and_all_stats_unchanged=True,
        source_sha256=hashes["rc6_shadow_runtime/archive_namespace.py"])
    cases[smoke.CASE_IDS[11]].update(recipe_schemas=["RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V3",
        "RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V4", "RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V3"],
        generations=3, original_members_per_generation=5, verification_level=smoke.V3_LEVEL,
        member_sha256=[member_hashes, member_hashes, member_hashes], source_bytes_and_all_stats_unchanged=True,
        repeated_restore_fresh_verification=True, whole_parent_graph_verified=True, recovery_encoder_calls=0,
        native_write_recipe_schema="RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V3", native_v4_write_enabled=False,
        original_horizon_or_capacity_equivalence_claimed=False)
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
        "rss_current_bytes": 16 * 1024**2, "rss_observation_pid": 123,
        "rss_observation_scope": smoke.RSS_SCOPE,
        "signal_lifetime_rss_peak_bytes": 64 * 1024**2,
        "scratch_allocated_bytes": 1024**2, "output_limit_bytes": 65536,
        "cases": cases, "case_count": len(smoke.CASE_IDS), "runtime_approval": False,
        "nine_hour_archive_capacity": "PENDING_SEPARATE_NATIVE_GATE",
        "large_producer_health_browser": "PENDING_SEPARATE_NATIVE_GATES"}


@pytest.fixture(scope="module")
def native_private_source(tmp_path_factory):
    """A fresh native Git checkout plus owned new files, without module overlays."""
    temporary = tmp_path_factory.mktemp("native-codec-source")
    repo = temporary / "source"
    env = dict(private_git_environment(), PYTHONDONTWRITEBYTECODE="1")
    previous = os.umask(0o022)
    try:
        repo.mkdir(mode=0o755)
        fullgit_bytes = copy_native_fullgit(ROOT / ".git", repo / ".git")
        assert 0 < fullgit_bytes <= MAX_PRIVATE_FULLGIT_BYTES
        head = private_git_bytes(ROOT / ".git", "rev-parse", "HEAD").decode().strip()
        # The exact Source tree/blobs are in the owned pack. Native reset builds
        # the fresh private index/worktree; no shared objects or overlay loader.
        subprocess.run(["git", "-C", str(repo), "reset", "--hard", "--quiet", head], check=True,
                       capture_output=True, env=env)
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
    final_git_bytes = private_git_allocated(repo / ".git")
    diagnostic = {"scope": "NATIVE_PRIVATE_GIT_FIXTURE_ONLY_NO_RC6_GATE_QUALIFICATION",
        "source_head": head, "fixture_head": manifest["candidate_sha"],
        "source_tree": private_git_bytes(ROOT / ".git", "rev-parse", head + "^{tree}").decode().strip(),
        "git_allocated_after_verified_import_bytes": fullgit_bytes,
        "git_allocated_after_private_checkout_commit_bytes": final_git_bytes,
        "original_git_physical_limit_bytes": MAX_PRIVATE_FULLGIT_BYTES,
        "original_raw_custody": manifest["raw_evidence_custody"],
        "all_source_files": manifest["file_count"], "G0_G8_qualification": False,
        "all_original_1202_horizon_or_droplet_capacity_claimed": False}
    (temporary / "native-private-git-closure.json").write_bytes(canonical_bytes(diagnostic))
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
    assert report["cases"][smoke.CASE_IDS[11]]["native_v4_write_enabled"] is False
    assert report["cases"][smoke.CASE_IDS[11]]["generations"] == 3
    receipt = path.parent / "native-cli.json"
    receipt.write_bytes(completed.stdout); receipt.chmod(0o644)


def test_exec_memory_peak_excludes_launcher_peak_and_retains_own_freed_allocation():
    child = """
import json, resource
from scripts.rc6_archive_v3_image_smoke import current_exec_rss
before = current_exec_rss()
allocated = bytearray(32 * 1024**2)
for offset in range(0, len(allocated), 4096):
    allocated[offset] = 1
during = current_exec_rss()
del allocated
after = current_exec_rss()
print(json.dumps({'before': before, 'during': during, 'after': after,
    'signal_lifetime_rss_peak_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}))
"""
    parent = """
import subprocess, sys
allocated = bytearray(64 * 1024**2)
for offset in range(0, len(allocated), 4096):
    allocated[offset] = 1
completed = subprocess.run([sys.executable, '-B', '-c', CHILD],
    capture_output=True, timeout=10, check=True)
sys.stdout.buffer.write(completed.stdout)
""".replace("CHILD", repr(child))
    completed = subprocess.run([sys.executable, "-I", "-S", "-c", parent], cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), capture_output=True, timeout=15, check=True)
    report = json.loads(completed.stdout)
    before, during, after = (report[name] for name in ("before", "during", "after"))
    assert before["rss_observation_scope"] == smoke.RSS_SCOPE
    assert before["rss_observation_pid"] == during["rss_observation_pid"] == after["rss_observation_pid"]
    assert report["signal_lifetime_rss_peak_bytes"] > before["rss_peak_bytes"] + 32 * 1024**2
    assert during["rss_peak_bytes"] >= before["rss_peak_bytes"] + 24 * 1024**2
    assert after["rss_peak_bytes"] >= during["rss_peak_bytes"] - 1024**2
    assert after["rss_current_bytes"] < during["rss_current_bytes"] - 24 * 1024**2
    assert after["rss_peak_bytes"] <= smoke.MAX_RSS == 512 * 1024**2


@pytest.mark.parametrize("status", [
    "Pid: 122\nVmHWM: 32 kB\nVmRSS: 16 kB\n",
    "Pid: 123\nVmRSS: 16 kB\n",
    "Pid: 123\nVmHWM: 32 kB\nVmHWM: 32 kB\nVmRSS: 16 kB\n",
    "Pid: 123\nVmHWM: 32 MB\nVmRSS: 16 kB\n",
    "Pid: 123\nVmHWM: -32 kB\nVmRSS: 16 kB\n",
    "Pid: 123\nVmHWM: 0 kB\nVmRSS: 0 kB\n",
    "Pid: 123\nVmHWM: 16 kB\nVmRSS: 32 kB\n",
    "Pid: 123\nVmHWM: 32 kB\nVmRSS: 16 kB\n" + "x" * smoke.MAX_PROCESS_STATUS,
])
def test_current_exec_rss_rejects_unbound_inconsistent_or_unavailable_peak_metadata(status):
    with pytest.raises(smoke.SmokeError, match="RSS_OBSERVATION_INVALID"):
        smoke.parse_current_exec_rss(status, expected_pid=123)


def test_current_exec_rss_keeps_original_limit_and_fails_closed_when_proc_unavailable(monkeypatch):
    status = f"Pid: {os.getpid()}\nVmHWM: {smoke.MAX_RSS // 1024 + 1} kB\nVmRSS: 16 kB\n"
    monkeypatch.setattr(smoke, "open", lambda *_args, **_kwargs: io.StringIO(status), raising=False)
    with pytest.raises(smoke.SmokeError, match="SMOKE_RSS_LIMIT"):
        smoke.current_exec_rss()
    def unavailable(*_args, **_kwargs):
        raise OSError("private process metadata unavailable")
    monkeypatch.setattr(smoke, "open", unavailable)
    with pytest.raises(smoke.SmokeError, match="RSS_OBSERVATION_UNAVAILABLE"):
        smoke.current_exec_rss()


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
