"""Regression of Deploy V2 38982377181's measured disk admission failure."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.rc6_disk_space_guard import load_policy, required_pretransfer_free

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'ops/policy/rc6-disk-housekeeping-v1.json'


def test_recorded_insufficient_host_is_rejected_before_transfer():
    required = required_pretransfer_free(414838735, 1174520298, 1490200,
                                         load_policy(POLICY))
    # The old 1x image allowance admitted this host; its measured final free
    # bytes were 1766100992, below the unchanged 2147483648 reserve.
    assert required > 3963834368
    assert required == 4914343379


@pytest.mark.parametrize('free,inodes,expected', [
    (1766100992, 89.56, 42),
    (2147483647, 89.56, 42),
    (2147483648, 9.99, 42),
    (2147483648, 10.0, 0),
])
def test_postload_checks_actual_reserve_and_inodes_without_new_transfer_budget(free, inodes, expected):
    result = subprocess.run([
        sys.executable, str(ROOT / 'scripts/rc6_disk_space_guard.py'),
        '--policy', str(POLICY), '--phase', 'postload',
        '--image-tar-bytes', '0', '--image-unpacked-bytes', '0', '--bundle-bytes', '0',
        '--available-bytes', str(free), '--inode-free-percent', str(inodes), '--json',
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stderr + result.stdout
    payload = json.loads(result.stdout.splitlines()[0])
    assert payload['required_bytes'] == 2147483648
    assert payload['status'] == ('GREEN' if expected == 0 else 'RED')
    assert 'RC6_DISK_POSTLOAD_RESERVE=' in result.stdout


def test_postload_guard_precedes_host_services_and_long_contract_sweep():
    text = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    loaded = text.index('POROTA_BUILD_ONCE_MATERIALIZED=GREEN')
    guard = text.index('RC6_DISK_POSTLOAD_RESERVE_GATE=GREEN', loaded)
    apply = text.index('porota_apply_host_control_plane_v2.py', loaded)
    assert loaded < guard < apply
    assert guard < text.index('POROTA_BUILD_ONCE_PROMOTION=GREEN', guard)
    block = text[loaded:guard]
    assert '--phase postload' in block
    # The drained stage cannot prove ownership of host-wide Docker cache.
    # A low measured reserve must fail closed before services or contract sweep.
    assert 'rc6_disk_housekeeping.py' not in block
    assert 'FOREIGN_CACHE_PRESERVED' in block
    assert 'docker image prune -f' not in block
    assert 'docker builder prune -af' not in block
    assert 'docker buildx prune -af' not in block
    assert block.count('check_postload_reserve') >= 2


@pytest.mark.parametrize('free,expected', [(2147483648, 0), (1766100992, 42)])
def test_actual_postload_shell_preserves_foreign_cache_and_fails_closed(tmp_path, free, expected):
    import os
    import shlex
    workflow = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    start = workflow.index('          check_postload_reserve()')
    end = workflow.index('          sudo -n python3 "$REPO/scripts/porota_apply_host_control_plane_v2.py"', start)
    block = '\n'.join(line[10:] for line in workflow[start:end].splitlines())
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    python_stub = bin_dir / 'python3'
    python_stub.write_text('#!/bin/bash\nif [ "$1" = -c ]; then\n'
        'echo "$RC6_TEST_FREE_BYTES"\necho 89.56\nelse\nexec '
        + shlex.quote(sys.executable) + ' "$@"\nfi\n')
    python_stub.chmod(0o700)
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
               REPO=str(ROOT), RC6_TEST_FREE_BYTES=str(free))
    result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + block],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == expected, result.stderr + result.stdout
    assert result.stdout.count('RC6_DISK_POSTLOAD_RESERVE=') == 1
    assert ('RC6_DISK_POSTLOAD_RESERVE_GATE=GREEN' in result.stdout) == (expected == 0)
    assert ('FOREIGN_CACHE_PRESERVED' in result.stdout) == (expected != 0)


@pytest.mark.parametrize('audit_rc,status,expected', [
    (0, 'GREEN', 0),
    (43, 'RED', 43),
    (44, 'RED', 44),
])
def test_preclean_hostwide_audit_never_accepts_error_as_cleanup_success(tmp_path, audit_rc, status, expected):
    import base64
    import os
    import shlex
    workflow = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    start = workflow.index('          sudo -n rm -f /tmp/porota-predeploy-housekeeping.json')
    end = workflow.index('          # Shared Docker images and build caches', start)
    block = '\n'.join(line[10:] for line in workflow[start:end].splitlines())
    assert '--mode audit --preserve-backups' in block
    assert '--mode cleanup' not in block
    receipt = tmp_path / 'housekeeping.json'
    block = block.replace('/tmp/porota-predeploy-housekeeping.json', shlex.quote(str(receipt)))
    stub = tmp_path / 'sudo'
    supplied = tmp_path / 'supplied-helper.py'
    helper = (ROOT / 'scripts/rc6_disk_housekeeping.py').read_bytes()
    data = json.dumps({'status': status, 'effective_mode': 'audit'})
    stub.write_text('#!/bin/bash\nset -eu\nshift\nif [ "$1" = rm ]; then shift; exec rm "$@"; fi\n'
                    + '[ "$1" = python3 ]\n[ "$2" = - ]\n'
                    + 'cat > ' + shlex.quote(str(supplied)) + '\n'
                    + "printf '%s' " + shlex.quote(data) + ' > ' + shlex.quote(str(receipt))
                    + '\nexit ' + str(audit_rc) + '\n')
    stub.chmod(0o700)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               REPO=str(ROOT), TMP_POLICY=str(POLICY),
               HOUSEKEEPING_B64=base64.b64encode(helper).decode('ascii'))
    result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + block + '\necho REACHED_OWNED_CLEANUP'],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stderr + result.stdout
    assert supplied.read_bytes() == helper
    assert ('REACHED_OWNED_CLEANUP' in result.stdout) == (expected == 0)
