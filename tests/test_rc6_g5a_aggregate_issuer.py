"""Adversarial development checks; native quota execution needs separate admission."""
from pathlib import Path

import pytest

from scripts import rc6_g5a_aggregate_issuer as issuer
from scripts import rc6_g5a_host_capacity as host
from scripts import rc6_trusted_quota_setup as trusted
from scripts import rc6_g5a_native_feasibility as feasibility
from scripts import rc6_g5a_writer_inventory as writers


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
    for name in ("home", "tmp", "cache", "builds", "exports", "readers", "source",
                 "config", "data"):
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


def test_all_stage_write_roots_reject_outside_or_rebound_project(tmp_path, monkeypatch):
    root = tmp_path / 'quota'
    root.mkdir()
    inside = root / 'readers'
    inside.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    monkeypatch.setattr(issuer.calibration, 'project_attributes',
        lambda path: {'project_id': 55 if Path(path) != outside else 56,
                      'inheritance': True, 'mount_id': 77})
    for stage in issuer.STAGES:
        proof = issuer.require_stage_topology(root, stage=stage,
            project_id=55, write_roots={'readers': inside})
        assert proof['launch_authorized'] is proof['G5_GREEN'] is False
        with pytest.raises(ValueError, match='OUTSIDE_PROJECT_WRITE_ROOT_FORBIDDEN'):
            issuer.require_stage_topology(root, stage=stage,
                project_id=55, write_roots={'escape': outside})
        with pytest.raises(ValueError, match='STAGE_PROJECT_READBACK_REQUIRED'):
            issuer.require_stage_topology(root, stage=stage,
                project_id=56, write_roots={'readers': inside})
        with pytest.raises(ValueError, match='^G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED$'):
            host.require_host_stage_admission(stage=stage, enforcement=proof)


def test_sealed_command_rejects_forged_stage_before_exec(tmp_path):
    with pytest.raises(ValueError, match='EXACT_STAGE_TOPOLOGY_REQUIRED'):
        issuer.guarded_stage_command(stage='reader-green', root=tmp_path,
            outside_control=tmp_path, project_id=55, cwd=tmp_path,
            argv=['/bin/true'], python='/usr/bin/python3')
    with pytest.raises(ValueError, match='SEALED_STAGE_ARGUMENTS_REQUIRED'):
        issuer._sealed_stage_main(['--sealed-stage', 'reader-green',
            str(tmp_path), str(tmp_path), '55', '--', '/bin/true'])


def test_current_source_writer_inventory_preserves_first_unenclosed_process():
    source = Path(__file__).absolute().parents[1]
    report = writers.audit(source)
    assert report['all_writers_enclosed'] is False
    assert report['outside_project'][0] == 'PROJECT_ROOT_NOT_ESTABLISHED'
    assert any(row['call'] == 'governed.source_pin' for row in report['unconfined_parent_call_sites'])
    assert any(row['call'] == 'horizon.preflight' and row['managed_wire']
               for row in report['process_call_sites'])
    assert any(row['call'] == 'subprocess.run' for row in report['unconfined_parent_call_sites'])
    with pytest.raises(ValueError, match='^PROJECT_ROOT_NOT_ESTABLISHED$'):
        writers.require_closed(report)
    report = writers.audit(source, project_root=source.parent / 'another-project')
    with pytest.raises(ValueError, match='^SOURCE_REPOSITORY_OUTSIDE_PROJECT$'):
        writers.require_closed(report)


def test_readonly_feasibility_rejects_ext4_label_without_actual_project_quota(monkeypatch):
    same = {'mount_id': 17, 'device': 43, 'filesystem': 'ext4',
            'source': '/dev/claimed-block', 'mount_options': ['rw'], 'super_options': []}
    monkeypatch.setattr(feasibility, '_mount_for', lambda path: dict(same))
    monkeypatch.setattr(feasibility.os.path, 'exists', lambda path: False)
    monkeypatch.setattr(feasibility.calibration, 'quota_format',
        lambda device: pytest.fail('Q_GETFMT must not run without a real block device'))
    result = feasibility.observe({'source': '/source', 'home': '/home'})
    assert result['status'] == 'NO_GO'
    assert result['reasons'] == ['G5A_ORIGINAL_MOUNT_BLOCK_DEVICE_UNAVAILABLE']
    assert result['format']['called'] is False
    assert result['twenty_gib_test_admitted'] is False


def test_readonly_feasibility_keeps_actual_q_getfmt_failure(monkeypatch):
    same = {'mount_id': 17, 'device': 43, 'filesystem': 'ext4',
            'source': '/dev/claimed-block', 'mount_options': ['rw'], 'super_options': []}
    monkeypatch.setattr(feasibility, '_mount_for', lambda path: dict(same))
    monkeypatch.setattr(feasibility.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(feasibility.os.path, 'islink', lambda path: False)
    monkeypatch.setattr(feasibility.os, 'stat', lambda path: type('Stat', (), {
        'st_mode': 0o060000, 'st_rdev': 43})())
    from scripts.rc6_capacity_calibration import CalibrationOperationError
    failure = {'operation': 'quotactl/Q_GETFMT', 'errno': 95, 'return': -1}
    def fail(device):
        raise CalibrationOperationError('CALIBRATION_OWN_NATIVE_OPERATION_FAILED', failure)
    monkeypatch.setattr(feasibility.calibration, 'quota_format', fail)
    result = feasibility.observe({'source': '/source', 'home': '/home'})
    assert result['format']['operation'] == failure
    assert result['reasons'] == ['G5A_NATIVE_Q_GETFMT_FAILED']
    assert result['project_quota_enforcement_proved'] is False


def test_superblock_feature_read_is_readonly_and_does_not_claim_active_quota(monkeypatch):
    import struct
    raw = bytearray(1024)
    struct.pack_into('<H', raw, 0x38, feasibility.EXT4_MAGIC)
    struct.pack_into('<I', raw, 0x64,
        feasibility.EXT4_RO_COMPAT_QUOTA | feasibility.EXT4_RO_COMPAT_PROJECT)
    monkeypatch.setattr(feasibility.os, 'open', lambda *args: 99)
    monkeypatch.setattr(feasibility.os, 'fstat', lambda fd: type('Stat', (), {
        'st_mode': 0o060000, 'st_rdev': 43})())
    monkeypatch.setattr(feasibility.os, 'pread', lambda fd, size, offset: bytes(raw))
    monkeypatch.setattr(feasibility.os, 'close', lambda fd: None)
    result = feasibility._ext4_feature_bits('/dev/root', 43)
    assert result['required_features_present'] is True
    assert result['quota_active_proved'] is result['enforcement_proved'] is False
    struct.pack_into('<I', raw, 0x64, feasibility.EXT4_RO_COMPAT_QUOTA)
    assert feasibility._ext4_feature_bits('/dev/root', 43)['project_feature'] is False


def test_prior_esrch_is_not_retried_and_unreadable_features_stay_unknown(monkeypatch):
    same = {'mount_id': 27, 'device': 43, 'filesystem': 'ext4',
            'source': '/dev/root', 'mount_options': ['rw'], 'super_options': []}
    monkeypatch.setattr(feasibility, '_mount_for', lambda path: dict(same))
    monkeypatch.setattr(feasibility.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(feasibility.os.path, 'islink', lambda path: False)
    monkeypatch.setattr(feasibility.os, 'stat', lambda path: type('Stat', (), {
        'st_mode': 0o060000, 'st_rdev': 43})())
    monkeypatch.setattr(feasibility, '_ext4_feature_bits', lambda *args: {
        'status': 'NO_VERIFICADO', 'errno': 13, 'required_features_present': None})
    monkeypatch.setattr(feasibility.calibration, 'quota_format',
        lambda device: pytest.fail('identical Q_GETFMT must not be retried'))
    with pytest.raises(ValueError, match='EXACT_PRIOR_FAILURE_REQUIRED'):
        feasibility.observe({'source': '/source', 'home': '/home'}, check_format=False,
                            prior_format_observation={'errno': 3})
    result = feasibility.observe({'source': '/source', 'home': '/home'},
        check_format=False, prior_format_observation=feasibility.PRIOR_ESRCH)
    assert result['status'] == 'NO_GO'
    assert result['format']['called'] is False
    assert result['format']['prior_observation_reference_only'] == feasibility.PRIOR_ESRCH
    assert result['ext4_superblock']['errno'] == 13
    assert result['project_quota_enforcement_proved'] is False


def test_static_inventory_cannot_claim_runtime_process_fd_closure():
    report = {'schema': writers.SCHEMA, 'outside_project': [],
              'unconfined_parent_call_sites': [], 'all_writers_enclosed': True,
              'runtime_coverage_proved': True}
    with pytest.raises(ValueError, match='G5A_RUNTIME_WRITER_COVERAGE_NOT_VERIFIED'):
        writers.require_closed(report)


def test_source_inventory_records_potential_write_sites_without_claiming_destinations():
    report = writers.audit(Path(__file__).absolute().parents[1])
    assert report['coverage'] == 'STATIC_SOURCE_PARTIAL'
    assert report['runtime_coverage_proved'] is False
    assert any(row['call'] == 'os.open' for row in report['write_call_sites'])
    assert any(row['call'] == 'calibration.publish' for row in report['write_call_sites'])
    assert all(row['runtime_destination_proved'] is False
               for row in report['write_call_sites'])


def test_open_regular_fd_outside_project_is_rejected_before_landlock(tmp_path, monkeypatch):
    root = tmp_path/'project'
    root.mkdir()
    external = tmp_path/'external-log'
    external.write_bytes(b'owned by another writer')
    monkeypatch.setattr(issuer.calibration, 'project_attributes',
        lambda path: {'mount_id': 17})
    with external.open('rb') as stream:
        with pytest.raises(ValueError, match='INHERITED_OUTSIDE_FILE_FORBIDDEN'):
            issuer.require_inherited_descriptors(root, 55, [stream.fileno()])


def test_replaced_child_mount_id_is_rejected_before_stage_command(tmp_path, monkeypatch):
    root = tmp_path/'project'
    root.mkdir()
    child = root/'readers'
    child.mkdir()
    monkeypatch.setattr(issuer.calibration, 'project_attributes',
        lambda path: {'project_id': 55, 'inheritance': True,
                      'mount_id': 18 if Path(path) == child else 17})
    with pytest.raises(ValueError, match='STAGE_CHILD_PROJECT_REBOUND'):
        issuer.require_stage_topology(root, stage='reader-execution',
            project_id=55, write_roots={'readers': child})
