"""Read-only feasibility screen for a host ext4 project quota.

This screen never creates a project, mounts, changes quota or grants a stage.
Even a successful Q_GETFMT only admits consideration of a separately approved
small native control; actual 20 GiB enforcement still needs EDQUOT and FIN.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import struct

from scripts import rc6_capacity_calibration as calibration

SCHEMA = "porota.rc6.g5a-host-quota-feasibility.v1"
# ext4 superblock offsets and feature values from e2fsprogs ext2_fs.h.
# This reads feature bits only; it cannot establish that quotas are active.
EXT4_MAGIC = 0xEF53
EXT4_RO_COMPAT_QUOTA = 0x0100
EXT4_RO_COMPAT_PROJECT = 0x2000
PRIOR_ESRCH = {'run_id': 38105317601,
               'artifact_sha256': '81c7e6d06b4c8ccbc47be6bab932b97af5e7241bbfd041a834b52c565d0b136a',
               'operation': 'quotactl/Q_GETFMT', 'errno': 3}


def _ext4_feature_bits(device, expected_device):
    """Read one existing block-device superblock without changing the host."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(device, flags)
        try:
            held = os.fstat(fd)
            if not stat.S_ISBLK(held.st_mode) or held.st_rdev != expected_device:
                return {'status': 'DEVICE_REBOUND', 'required_features_present': False}
            block = os.pread(fd, 1024, 1024)
        finally:
            os.close(fd)
    except OSError as error:
        return {'status': 'NO_VERIFICADO', 'errno': error.errno,
                'error_type': type(error).__name__, 'required_features_present': None}
    if len(block) != 1024 or struct.unpack_from('<H', block, 0x38)[0] != EXT4_MAGIC:
        return {'status': 'INVALID_EXT4_SUPERBLOCK', 'required_features_present': False}
    flags = struct.unpack_from('<I', block, 0x64)[0]
    quota = bool(flags & EXT4_RO_COMPAT_QUOTA)
    project = bool(flags & EXT4_RO_COMPAT_PROJECT)
    return {'status': 'READ_ONLY_FEATURE_BITS', 'ro_compat': flags,
            'quota_feature': quota, 'project_feature': project,
            'required_features_present': quota and project,
            'quota_active_proved': False, 'enforcement_proved': False}


def _mount_for(path):
    path = Path(path).absolute()
    fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info, mount_id = os.fstat(fd), calibration.descriptor_mount_id(fd)
    finally:
        os.close(fd)
    rows = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, separator, right = line.partition(' - ')
        if not separator:
            raise ValueError('G5A_FEASIBILITY_MOUNTINFO_INVALID')
        fields, tail = left.split(), right.split()
        if int(fields[0]) == mount_id:
            rows.append((fields, tail))
    if len(rows) != 1 or len(rows[0][0]) < 6 or len(rows[0][1]) < 2:
        raise ValueError('G5A_FEASIBILITY_MOUNT_ID_AMBIGUOUS')
    fields, tail = rows[0]
    return {'path': str(path), 'mount_id': mount_id, 'device': info.st_dev,
            'filesystem': tail[0], 'source': tail[1],
            'mount_options': fields[5].split(','), 'super_options': tail[2].split(',') if len(tail) > 2 else []}


def observe(paths, *, check_format=True, prior_format_observation=None):
    """Inspect destinations; a prior ESRCH can defer an identical Q_GETFMT."""
    if type(paths) is not dict or not paths or not all(type(k) is str and
            type(v) is str and Path(v).is_absolute() for k, v in paths.items()):
        raise ValueError('G5A_FEASIBILITY_EXACT_RUNNER_PATHS_REQUIRED')
    if type(check_format) is not bool or (not check_format and
            prior_format_observation != PRIOR_ESRCH):
        raise ValueError('G5A_FEASIBILITY_EXACT_PRIOR_FAILURE_REQUIRED')
    destinations = {}
    reasons = []
    for name, value in paths.items():
        try:
            destinations[name] = _mount_for(value)
        except (OSError, ValueError) as error:
            destinations[name] = {'path': value, 'error': type(error).__name__,
                                  'errno': getattr(error, 'errno', None)}
            reasons.append('G5A_RUNNER_MOUNT_READBACK_FAILED:' + name)
    valid = [row for row in destinations.values() if 'mount_id' in row]
    devices = {(row['mount_id'], row['device']) for row in valid}
    home = destinations.get('home')
    source = destinations.get('source')
    if home is None or source is None or len(devices) != 1:
        reasons.append('G5A_SOURCE_AND_HOME_ONE_MOUNT_REQUIRED')
    if len(valid) != len(destinations) or any(row['filesystem'] != 'ext4' or 'rw' not in row['mount_options']
           for row in valid):
        reasons.append('G5A_RUNNER_DESTINATIONS_NOT_RW_EXT4')
    device = home.get('source', '') if home is not None else ''
    superblock = {'status': 'NOT_ADMITTED', 'required_features_present': None}
    if not device.startswith('/') or not os.path.exists(device) or os.path.islink(device):
        reasons.append('G5A_ORIGINAL_MOUNT_BLOCK_DEVICE_UNAVAILABLE')
    else:
        try:
            details = os.stat(device)
            if not stat.S_ISBLK(details.st_mode) or details.st_rdev != home['device']:
                reasons.append('G5A_ORIGINAL_MOUNT_BLOCK_DEVICE_MISMATCH')
            else:
                superblock = _ext4_feature_bits(device, home['device'])
        except OSError:
            reasons.append('G5A_ORIGINAL_MOUNT_BLOCK_DEVICE_UNAVAILABLE')
    format_result = {'called': False, 'status': 'NOT_ADMITTED'}
    if not check_format:
        format_result = {'called': False, 'status': 'PRIOR_ESRCH_NOT_RETRIED',
                         'prior_observation_reference_only': prior_format_observation}
        reasons.append('G5A_NATIVE_Q_GETFMT_PRIOR_ESRCH_UNRESOLVED')
        if superblock['required_features_present'] is False:
            reasons.append('G5A_EXT4_REQUIRED_FEATURES_ABSENT_OR_DEVICE_REBOUND')
        elif superblock['required_features_present'] is None:
            reasons.append('G5A_EXT4_SUPERBLOCK_FEATURES_NO_VERIFICADO')
    elif not reasons:
        # This is read-only. EPERM/ENOSYS/ENOTBLK are retained as first facts,
        # never treated as proof that project quotas work through another API.
        format_result = {'called': True}
        try:
            actual = calibration.quota_format(device)
        except calibration.CalibrationOperationError as error:
            operation = error.details
            format_result.update(status='FAILED', operation=operation)
            reasons.append('G5A_NATIVE_Q_GETFMT_FAILED')
        except (OSError, ValueError) as error:
            format_result.update(status='FAILED', errno=getattr(error, 'errno', None),
                                 reason=str(error).split(':', 1)[0])
            reasons.append('G5A_NATIVE_Q_GETFMT_FAILED')
        else:
            format_result.update(status='Q_GETFMT_ONLY', operation=actual)
    return {'schema': SCHEMA, 'destinations': destinations,
            'ext4_superblock': superblock,
            'format': format_result, 'reasons': reasons,
            'status': 'NO_GO' if reasons else 'SMALL_CONTROL_REQUIRES_SEPARATE_AUTHORIZATION',
            'project_quota_enforcement_proved': False, 'twenty_gib_test_admitted': False,
            'root_operation_called': False, 'material_operation_called': False,
            'quotactl_fd_evaluated_only': True, 'quotactl_fd_bypass_allowed': False,
            'G5_GREEN': False, 'real_orders_sent': 0}
