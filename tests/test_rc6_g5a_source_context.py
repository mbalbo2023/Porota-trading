"""Actual owned Git/export IO; never ROOT, unit launch, quota or G5 acceptance."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pickle
import shutil
import subprocess

import pytest

from scripts import rc6_g5a_source_context as exported
from scripts import rc6_material_horizon_producer as producer
from scripts import rc6_privileged_custody as custody


def git(repo, *args, payload=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], input=payload,
        env={**os.environ, "GIT_CONFIG_COUNT": "0", "GIT_OPTIONAL_LOCKS": "0"}, stderr=subprocess.PIPE)


@pytest.fixture
def actual_export(tmp_path):
    repo, source, inputs = (tmp_path / name for name in ("repository", "export", "inputs"))
    for path in (repo, source, inputs): path.mkdir(mode=0o755)
    git(repo, "init", "--quiet")
    (repo / "scripts").mkdir(mode=0o755)
    (repo / exported.MEMBER).write_bytes(Path(exported.__file__).read_bytes())
    (repo / "original.dat").write_bytes(b"OWN_SOURCE_ALL_BYTES\0\xff\x01")
    git(repo, "add", "--all")
    tree = git(repo, "write-tree").decode().strip()
    body = ("tree " + tree + "\nauthor Unit <unit@example.invalid> 1700000000 +0000\n"
            "committer Unit <unit@example.invalid> 1700000000 +0000\n\nOWN_UNIT_GIT_OBJECT_ONLY\n").encode()
    sha = git(repo, "hash-object", "-t", "commit", "-w", "--stdin", payload=body).decode().strip()
    git(repo, "symbolic-ref", "HEAD", "refs/heads/unit")
    git(repo, "update-ref", "refs/heads/unit", sha)
    (source / "scripts").mkdir(mode=0o755)
    for name in (exported.MEMBER, "original.dat"):
        shutil.copyfile(repo / name, source / name); (source / name).chmod(0o644)
    inventory = producer.code_inventory(source)
    pin = {"schema": "rc6.complete-archive-source-pin.v1", "source_sha": sha, "source_tree": tree,
        "overlay_count": 0, "raw_git_commit_sha256": hashlib.sha256(body).hexdigest(), **inventory}
    index = inputs / "source.index.json"
    index.write_text(json.dumps(pin, sort_keys=True)); index.chmod(0o600)
    spec = importlib.util.spec_from_file_location("owned_source_context_unit", repo / exported.MEMBER)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    binding = {"candidate_sha": sha, "candidate_tree": tree, "producer": "Horizon-private-original-cas",
        "attempt_id": "OWN_UNIT_ONLY", "owner_id": "OWN_UNIT_ONLY", "workload_fingerprint": "original",
        "runner_class": "github-hosted/ubuntu-24.04"}
    args = {"source_repo": repo, "source_root": source, "index": index,
        "source_sha": sha, "source_tree": tree, "binding": binding}
    return module, args


def test_actual_owned_raw_git_export_mints_opaque_context_and_rechecks_every_byte(actual_export):
    module, args = actual_export
    context = module.observe_export(**args)
    value = module.validate_export(context, **args)
    assert value["source_root"] == str(args["source_root"])
    assert value["source_repo"] == str(args["source_repo"])
    assert value["index_sha256"] == hashlib.sha256(args["index"].read_bytes()).hexdigest()
    # Explicitly only Source correspondence: no ROOT/native FIN/G5 authority.
    assert "G5_GREEN" not in value and "ROOT" not in value and "cleanup" not in value
    with pytest.raises(TypeError): module.ExportedSourceContext(value)
    with pytest.raises(TypeError): pickle.dumps(context)


@pytest.mark.parametrize("kind", ["dict", "unregistered", "copy", "other_module"])
def test_json_or_forged_object_cannot_become_source_authority(actual_export, kind):
    module, args = actual_export
    original = module.observe_export(**args)
    if kind == "dict": context = {"source_root": str(args["source_root"]), "verified": True}
    elif kind == "unregistered": context = object.__new__(module.ExportedSourceContext)
    elif kind == "other_module": context = object.__new__(exported.ExportedSourceContext)
    else:
        with pytest.raises(TypeError): copy.copy(original)
        return
    with pytest.raises(ValueError, match="OPAQUE_CURRENT_NATIVE_CONTEXT_REQUIRED"):
        module.validate(context, **args)


@pytest.mark.parametrize("change", ["bytes", "same_bytes_new_inode", "hardlink", "symlink", "extra_file", "empty_directory", "mode", "atime"])
def test_changed_export_rejected_after_registration(actual_export, change):
    module, args = actual_export; context = module.observe_export(**args)
    path = args["source_root"] / "original.dat"
    if change == "bytes": path.write_bytes(b"CHANGED")
    elif change == "same_bytes_new_inode":
        raw = path.read_bytes(); path.unlink(); path.write_bytes(raw); path.chmod(0o644)
    elif change == "hardlink": os.link(path, args["source_root"] / "alias")
    elif change == "symlink": path.unlink(); path.symlink_to(args["source_repo"] / "original.dat")
    elif change == "extra_file": (args["source_root"] / "ghost.dat").write_bytes(b"ghost")
    elif change == "empty_directory": (args["source_root"] / "empty").mkdir()
    elif change == "mode": path.chmod(0o600)
    else: os.utime(path, ns=(path.stat().st_atime_ns + 1, path.stat().st_mtime_ns))
    with pytest.raises((ValueError, OSError)):
        module.validate(context, **args)


@pytest.mark.parametrize("change", ["bytes", "same_bytes_new_inode", "hardlink", "symlink", "oversize", "duplicate"])
def test_index_physical_origin_and_exact_bytes_cannot_rebind(actual_export, change):
    module, args = actual_export; context = module.observe_export(**args); index = args["index"]
    if change == "bytes": index.write_bytes(b"{}")
    elif change == "same_bytes_new_inode":
        raw = index.read_bytes(); index.unlink(); index.write_bytes(raw); index.chmod(0o600)
    elif change == "hardlink": os.link(index, index.parent / "alias")
    elif change == "symlink": index.rename(index.parent / "target"); index.symlink_to(index.parent / "target")
    elif change == "oversize":
        with index.open("wb") as stream: stream.truncate(module.MAX_INDEX + 1)
    else: index.write_bytes(b'{"schema":"rc6.complete-archive-source-pin.v1","schema":"aliased"}')
    with pytest.raises((ValueError, OSError)):
        module.validate(context, **args)


@pytest.mark.parametrize("field", ["attempt_id", "owner_id", "producer", "candidate_sha", "candidate_tree", "runner_class"])
def test_context_is_bound_to_exact_original_candidate_session(actual_export, field):
    module, args = actual_export; context = module.observe_export(**args)
    changed = {**args, "binding": {**args["binding"], field: "unbound"}}
    with pytest.raises(ValueError, match="REGISTERED_SOURCE_OR_ISSUER_REBOUND"):
        module.validate(context, **changed)


@pytest.mark.parametrize("change", ["tracked", "untracked", "replace_ref"])
def test_original_git_must_stay_clean_and_unreplaced(actual_export, change):
    module, args = actual_export; context = module.observe_export(**args)
    if change == "tracked": (args["source_repo"] / "original.dat").write_bytes(b"DIRTY")
    elif change == "untracked": (args["source_repo"] / "ghost").write_bytes(b"GHOST")
    else: git(args["source_repo"], "update-ref", "refs/replace/" + args["source_sha"], args["source_sha"])
    with pytest.raises(ValueError): module.validate(context, **args)


def test_git_config_is_restored_and_readonly_inventory_preserves_all11_fields(actual_export, monkeypatch):
    module, args = actual_export
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "UNIT_ORIGINAL_KEY")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "UNIT_ORIGINAL_VALUE")
    path = args["source_root"] / "original.dat"
    before = module._identity(path.stat())
    context = module.observe_export(**args)
    module.validate(context, **args)
    assert module._identity(path.stat()) == before
    assert os.environ["GIT_CONFIG_COUNT"] == "0"
    assert os.environ["GIT_CONFIG_KEY_0"] == "UNIT_ORIGINAL_KEY"
    assert os.environ["GIT_CONFIG_VALUE_0"] == "UNIT_ORIGINAL_VALUE"


def test_export_context_cannot_cross_process_issuer_birth(actual_export, monkeypatch):
    module, args = actual_export; context = module.observe_export(**args)
    actual = module._birth
    monkeypatch.setattr(module, "_birth", lambda: {**actual(), "start_ticks": "unbound"})
    with pytest.raises(ValueError, match="REGISTERED_SOURCE_OR_ISSUER_REBOUND"):
        module.validate(context, **args)


def test_actual_credential_metadata_start_ticks_is_kernel_integer():
    value = {"pid": os.getpid(), "start_ticks": 12, "boot_id": "12345678-1234-1234-1234-123456789abc",
        "uid": os.getuid(), "gid": os.getgid(), "fd": 12, "name": "rc6-readonly-api", "bytes": 33}
    assert custody._g5a_credential_value(value) == "rc6-readonly-api:/proc/" + str(os.getpid()) + "/fd/12"
    for unbound in ("12", True, -1):
        with pytest.raises(ValueError): custody._g5a_credential_value({**value, "start_ticks": unbound})
