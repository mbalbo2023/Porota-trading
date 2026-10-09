"""Native ownership of the explicit eleven-input artifact-parser fixture.

These controls do not qualify the RC6 Source, build artifact or retained quota.
The original 319 cases remain in their four complete modules.
"""
import hashlib
import os
import stat
import subprocess

from tests.test_issue465_provenance import (
    _ISSUE465_FIXTURE_INPUTS, _issue465_build_seed, _issue465_clone_seed,
    _issue465_native_identity, _issue465_snapshot, _issue465_stat10, git, write,
)


def test_native_synthetic_seed_is_exact_eleven_input_root_history_readonly(tmp_path):
    seed = _issue465_build_seed(tmp_path / "seed")
    identity = seed["identity"]
    assert len(identity["blob_oids"]) == 11
    assert len(identity["tree_oids"]) == 9
    assert len(identity["object_ids"]) == 21
    assert git(seed["root"], "rev-list", "--count", "HEAD") == "1"
    assert not git(seed["root"], "remote")
    assert not git(seed["root"], "for-each-ref", "refs/remotes")
    assert ".git/objects/info/alternates" not in seed["snapshot"]
    for relative, content in _ISSUE465_FIXTURE_INPUTS.items():
        row = seed["snapshot"][relative]
        assert row["sha256"] == hashlib.sha256(content).hexdigest()
        assert stat.S_IMODE(row["stat10"][2]) == (0o555 if relative == "scripts/run" else 0o444)
    assert all(not row["stat10"][2] & 0o222 for row in seed["snapshot"].values())
    assert _issue465_snapshot(seed["root"]) == seed["snapshot"]
    assert _issue465_native_identity(seed["root"]) == identity


def test_native_fixture_clones_own_pack_index_and_mutable_worktree_independently(tmp_path):
    seed = _issue465_build_seed(tmp_path / "seed")
    first = tmp_path / "first"; second = tmp_path / "second"
    left = _issue465_clone_seed(first, seed)
    right = _issue465_clone_seed(second, seed)
    assert left["identity"] == right["identity"] == seed["identity"]
    assert left["tracked_inputs"] == right["tracked_inputs"] == 11
    for report in (left, right):
        assert report["assertion_scope"] == "EXPLICIT_SYNTHETIC_ONLY"
        assert report["complete_rc6_source_claimed"] is False
        assert report["runtime_or_artifact_qualification_claimed"] is False
        assert report["aggregate_retained_quota_pass_claimed"] is False
        assert len(report["pack_paths"]) == 2
        assert all(row["stat10"][3] == 1 for row in report["records"].values() if row["kind"] == "file")
    for relative in [".git/index", "app.py", "scripts/run", *left["pack_paths"]]:
        a = left["records"][relative]["stat10"]; b = right["records"][relative]["stat10"]
        assert (a[0], a[1]) != (b[0], b[1])
    write(first, "worker.py", b"VALUE = 91\n")
    git(first, "add", "worker.py")
    git(first, "commit", "-qm", "first private mutation")
    assert git(first, "rev-parse", "HEAD") != seed["identity"]["head"]
    assert git(first, "rev-parse", "HEAD^") == seed["identity"]["head"]
    assert _issue465_native_identity(second) == seed["identity"]
    assert _issue465_snapshot(second) == right["records"]
    assert _issue465_snapshot(seed["root"]) == seed["snapshot"]


def test_native_corruption_of_one_owned_pack_cannot_change_other_clone_or_seed(tmp_path):
    seed = _issue465_build_seed(tmp_path / "seed")
    first = tmp_path / "first"; second = tmp_path / "second"
    left = _issue465_clone_seed(first, seed)
    right = _issue465_clone_seed(second, seed)
    pack = next(path for path in left["pack_paths"] if path.endswith(".pack"))
    descriptor = os.open(first / pack, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        assert _issue465_stat10(os.fstat(descriptor)) == left["records"][pack]["stat10"]
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    descriptor = os.open(first / pack, os.O_RDWR | os.O_NOFOLLOW)
    try:
        current = os.fstat(descriptor); previous = left["records"][pack]["stat10"]
        assert current.st_ino == previous[1] and current.st_dev == previous[0] and current.st_nlink == 1
        assert os.pwrite(descriptor, b"X", 0) == 1
    finally:
        os.close(descriptor)
    actual = subprocess.run(["git", "--no-replace-objects", "-C", str(first), "cat-file", "commit", seed["identity"]["head"]],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert actual.returncode != 0
    assert actual.stderr
    assert _issue465_native_identity(second) == seed["identity"]
    assert _issue465_snapshot(second) == right["records"]
    assert _issue465_native_identity(seed["root"]) == seed["identity"]
    assert _issue465_snapshot(seed["root"]) == seed["snapshot"]


def test_native_alternates_replacement_cannot_remove_clone_own_head_objects(tmp_path):
    seed = _issue465_build_seed(tmp_path / "seed")
    own = tmp_path / "own"; foreign = tmp_path / "foreign"
    _issue465_clone_seed(own, seed)
    _issue465_clone_seed(foreign, seed)
    write(foreign, "foreign-object.txt", b"real foreign fixture object\n")
    git(foreign, "add", "foreign-object.txt")
    git(foreign, "commit", "-qm", "real foreign object authority")
    foreign_blob = git(foreign, "rev-parse", "HEAD:foreign-object.txt")
    write(own, ".git/objects/info/alternates", (str(foreign / ".git/objects") + "\n").encode())
    assert subprocess.check_output(["git", "--no-replace-objects", "-C", str(own), "cat-file", "blob", foreign_blob]) == b"real foreign fixture object\n"
    assert _issue465_native_identity(own) == seed["identity"]
    assert git(own, "show", "HEAD:app.py") == _ISSUE465_FIXTURE_INPUTS["app.py"].decode().strip()
    assert _issue465_native_identity(seed["root"]) == seed["identity"]
    assert _issue465_snapshot(seed["root"]) == seed["snapshot"]



def test_real_reverse_index_is_rejected_by_unchanged_pair_guard_with_bounded_own_names(tmp_path, monkeypatch):
    """Adversarial native .rev, not evidence about the earlier unavailable names."""
    import json
    from pathlib import Path
    import pytest
    from tests import test_issue465_provenance as fixture_module

    seed = _issue465_build_seed(tmp_path / "seed")
    repo = tmp_path / "clone-with-native-reverse-index"
    real_check_output = subprocess.check_output
    produced = []

    def native_clone_then_real_reverse_index(argv, *args, **kwargs):
        output = real_check_output(argv, *args, **kwargs)
        if isinstance(argv, list) and len(argv) > 2 and argv[0] == "git" and "clone" in argv:
            assert Path(argv[-1]) == repo
            members = sorted((repo / ".git/objects/pack").iterdir())
            assert len(members) == 2 and {path.suffix for path in members} == {".pack", ".idx"}
            pack = next(path for path in members if path.suffix == ".pack")
            reverse = pack.with_suffix(".rev")
            assert not os.path.lexists(reverse)
            # Actual native index-pack output. No replacement returncode, object
            # counts, snapshot, kernel, or guard result is supplied by this seam.
            real_check_output(["git", "-C", str(repo), "-c", "pack.writeReverseIndex=true",
                               "index-pack", "--rev-index", str(pack)])
            info = reverse.lstat()
            assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
            produced.append(reverse.name)
        return output

    monkeypatch.setattr(fixture_module.subprocess, "check_output", native_clone_then_real_reverse_index)
    with pytest.raises(RuntimeError, match="ISSUE465_SYNTHETIC_FIXTURE_OWN_PACK_PAIR") as failure:
        _issue465_clone_seed(repo, seed)
    assert len(produced) == 1
    reason = str(failure.value)
    detail = json.loads(reason.split("OWN_PACK_PAIR ", 1)[1])
    assert detail["scope"] == "VALIDATED_OWN_REGULAR_GIT_PACK_ENTRIES"
    assert detail["member_count"] == 3 and detail["members_truncated"] is False
    assert {row["name"] for row in detail["first_8_members"]} == {
        produced[0], produced[0].replace(".rev", ".idx"), produced[0].replace(".rev", ".pack")}
    assert len(reason.encode()) < 4096 and str(tmp_path) not in reason
    assert _issue465_snapshot(seed["root"]) == seed["snapshot"]
