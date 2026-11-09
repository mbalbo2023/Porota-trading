"""Only digest-pinned candidate pytest hooks may enter trusted OCI harness."""

import hashlib
from pathlib import Path
from unittest.mock import patch
import sys

import pytest

from scripts import rc6_ci_ws12_trusted_pytest_launch as launcher


def test_only_reviewed_plugin_bytes_are_loaded_before_candidate_import(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    shard = source / "scripts/rc6_ci_shard_quorum.py"
    plugin = source / "scripts/rc6_ci_pytest_identity.py"
    shard.write_text("MARK = 'SHARD_VERIFIED'\n")
    plugin.write_text("from scripts.rc6_ci_shard_quorum import MARK\n")
    hashes = {shard.name: hashlib.sha256(shard.read_bytes()).hexdigest(),
              plugin.name: hashlib.sha256(plugin.read_bytes()).hexdigest()}
    with patch.dict(sys.modules):
        module = launcher.load_verified_plugin(source, hashes)
    assert module.MARK == "SHARD_VERIFIED"
    assert Path(module.__file__) == plugin
    assert module.__name__ == "scripts.rc6_ci_pytest_identity"


def test_plugin_content_change_is_denied_before_execution(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    shard = source / "scripts/rc6_ci_shard_quorum.py"
    plugin = source / "scripts/rc6_ci_pytest_identity.py"
    shard.write_text("MARK = 1\n")
    plugin.write_text("MARK = 1\n")
    hashes = {shard.name: hashlib.sha256(shard.read_bytes()).hexdigest(),
              plugin.name: hashlib.sha256(plugin.read_bytes()).hexdigest()}
    plugin.write_text("MARK = 2\n")
    with patch.dict(sys.modules), pytest.raises(launcher.TrustedPluginHold):
        launcher.load_verified_plugin(source, hashes)


def test_symlink_plugin_is_denied(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    shard = source / "scripts/rc6_ci_shard_quorum.py"
    shard.write_text("MARK = 1\n")
    plugin = source / "scripts/rc6_ci_pytest_identity.py"
    plugin.symlink_to(shard)
    hashes = {shard.name: hashlib.sha256(shard.read_bytes()).hexdigest(),
              plugin.name: hashlib.sha256(shard.read_bytes()).hexdigest()}
    with patch.dict(sys.modules), pytest.raises(launcher.TrustedPluginHold):
        launcher.load_verified_plugin(source, hashes)


def test_main_locked_candidate_hash_policy_is_nonempty_and_exact():
    assert launcher.REVIEWED_CANDIDATE_SHA == "2c4a6de6ead224589cead1e2859b72bffcb34a9b"
    assert launcher.REVIEWED_CANDIDATE_TREE == "d053c2d8be145c83d6ef3bc5a31bb105465a5ab9"
    assert all(len(h) == 64 for h in launcher.REVIEWED_MODULES.values())
