"""Adversarial development checks; native quota execution needs separate admission."""
from pathlib import Path

import pytest

from scripts import rc6_g5a_aggregate_issuer as issuer
from scripts import rc6_g5a_host_capacity as host
from scripts import rc6_trusted_quota_setup as trusted


def test_host_project_scope_keeps_exact_20gib_and_small_native_control():
    assert trusted.validate_project_spec(hard_bytes=issuer.LIMIT,
        hard_inodes=issuer.INODES,
        operation_scope="G5A_HOST_STAGE_AGGREGATE_20GIB_V1")["hard_bytes"] == 20 * 1024**3
    assert trusted.validate_project_spec(hard_bytes=issuer.PROBE_LIMIT,
        hard_inodes=32768,
        operation_scope="G5A_HOST_STAGE_AGGREGATE_20GIB_V1")["hard_bytes"] == 1024**2
    for bytes_, inodes in ((issuer.LIMIT - 1024, issuer.INODES),
                           (issuer.LIMIT + 1024, issuer.INODES),
                           (issuer.LIMIT, issuer.INODES - 1),
                           (issuer.PROBE_LIMIT, issuer.INODES)):
        with pytest.raises(ValueError, match="TRUSTED_QUOTA_SCOPE_OR_LIMIT_REBOUND"):
            trusted.validate_project_spec(hard_bytes=bytes_, hard_inodes=inodes,
                operation_scope="G5A_HOST_STAGE_AGGREGATE_20GIB_V1")


def test_installer_compiler_git_and_reader_temp_roots_are_forced_into_own_project(tmp_path):
    for name in ("home", "tmp", "cache", "builds", "exports", "readers", "source"):
        (tmp_path / name).mkdir(mode=0o700)
    environment = issuer.stage_environment(tmp_path, {
        "TMPDIR": "/tmp/outside", "HOME": "/home/outside",
        "PIP_CACHE_DIR": "/tmp/other", "PATH": "/usr/bin"})
    for key in ("HOME", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME",
                "PIP_CACHE_DIR", "PYTHONPYCACHEPREFIX"):
        assert Path(environment[key]).is_relative_to(tmp_path)
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PATH"] == "/usr/bin"
    with pytest.raises(ValueError, match="G5A_AGGREGATE_NO_CHILD_BEARER_REQUIRED"):
        issuer.stage_environment(tmp_path, {"GH_TOKEN": "fake"})
    (tmp_path / "tmp").rmdir()
    (tmp_path / "tmp").symlink_to(tmp_path.parent)
    with pytest.raises(ValueError, match="G5A_AGGREGATE_ALL_CHILD_WRITE_ROOTS_REQUIRED"):
        issuer.stage_environment(tmp_path, {})


def test_native_emitter_document_cannot_authorize_any_stage_without_live_proof():
    evidence = issuer.incomplete_evidence(setup={"aggregate": {"hard_bytes": issuer.LIMIT}},
        unlinked_control={"errno": 122}, boundary={"outside_errno": 13},
        stages=sorted(issuer.STAGES))
    assert evidence["all_stages_observed"] is True
    assert evidence["launch_authorized"] is evidence["G5_GREEN"] is False
    for stage in issuer.STAGES:
        with pytest.raises(ValueError, match="^G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED$"):
            host.require_host_stage_admission(stage=stage, enforcement=evidence,
                owner_acceptance={"revision_approved": True})


def test_unlinked_probe_rejects_changed_limit_before_any_native_allocation():
    for limit in (issuer.LIMIT - 1024, issuer.LIMIT + 1024, 0, True):
        with pytest.raises(ValueError, match="G5A_AGGREGATE_EXACT_PROBE_LIMIT_REQUIRED"):
            issuer.nonroot_unlinked_edquot_probe("/must-not-open", expected_id=1,
                hard_bytes=limit)
