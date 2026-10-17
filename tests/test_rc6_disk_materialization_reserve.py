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
    loaded = text.index('POROTA_BUILD_ONCE_PROMOTION=GREEN')
    guard = text.index('RC6_DISK_POSTLOAD_RESERVE_GATE=GREEN', loaded)
    apply = text.index('porota_apply_host_control_plane_v2.py', loaded)
    assert loaded < guard < apply
    block = text[loaded:guard]
    assert '--phase postload' in block
    # The general housekeeper requires running containers; this stage is
    # deliberately drained. Only reference-safe Docker/cache cleanup applies.
    assert 'rc6_disk_housekeeping.py' not in block
    assert 'docker image prune -f' in block
    assert 'docker builder prune -af' in block
    assert 'docker system prune' not in block
    assert 'docker image prune -af' not in block
    assert block.count('check_postload_reserve') >= 3


@pytest.mark.parametrize('recovered,expected', [(True, 0), (False, 42)])
def test_actual_postload_shell_rechecks_after_cleanup_and_fails_closed(tmp_path, recovered, expected):
    import os
    import shlex
    workflow = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    start = workflow.index('          check_postload_reserve()')
    end = workflow.index('          sudo -n python3 "$REPO/scripts/porota_apply_host_control_plane_v2.py"', start)
    block = '\n'.join(line[10:] for line in workflow[start:end].splitlines())
    flag = tmp_path / 'pruned'
    calls = tmp_path / 'calls'
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    # Real guard/policy run in a child process. Only host metrics and Docker
    # effects are simulated; no Docker/production path is touched locally.
    python_stub = bin_dir / 'python3'
    python_stub.write_text('#!/bin/bash\nif [ "$1" = -c ]; then\n'
        + ('if [ -f "$PRUNED" ]; then echo 2147483648; else echo 1766100992; fi\n' if recovered else 'echo 1766100992\n')
        + 'echo 89.56\nelse\nexec ' + shlex.quote(sys.executable) + ' "$@"\nfi\n')
    sudo_stub = bin_dir / 'sudo'
    sudo_stub.write_text('#!/bin/bash\nset -eu\nshift\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in\n'
        '"docker image prune -f"|"docker builder prune -af"|"docker buildx prune -af") touch "$PRUNED";;\n'
        '"docker buildx version") exit 0;;\n'
        '*) exit 99;;\nesac\n')
    python_stub.chmod(0o700); sudo_stub.chmod(0o700)
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
               REPO=str(ROOT), PRUNED=str(flag), CALLS=str(calls))
    result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + block],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == expected, result.stderr + result.stdout
    assert 'RC6_DISK_POSTLOAD_RESERVE=RED' in result.stdout
    assert result.stdout.count('RC6_DISK_POSTLOAD_RESERVE=') == 2
    assert ('RC6_DISK_POSTLOAD_RESERVE_GATE=GREEN' in result.stdout) == recovered
    assert ('FIX_FORWARD_VALIDATION_REQUIRED' in result.stdout) != recovered
    assert calls.read_text().splitlines() == [
        'docker image prune -f', 'docker builder prune -af',
        'docker buildx version', 'docker buildx prune -af']


@pytest.mark.parametrize('housekeeping_rc,reason,expected', [
    (43, 'POST_CLEANUP_FREE_BELOW_MINIMUM', 0),
    (43, 'UNRECOGNIZED_FAILURE', 1),
    (44, 'POST_CLEANUP_FREE_BELOW_MINIMUM', 44),
    (0, '', 0),
])
def test_preclean_only_defers_exact_residual_space_result_to_final_admission(tmp_path, housekeeping_rc, reason, expected):
    import os
    import shlex
    workflow = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    start = workflow.index('          rm -f /tmp/porota-predeploy-housekeeping.json')
    end = workflow.index('          sudo -n docker image prune -f', start)
    block = '\n'.join(line[10:] for line in workflow[start:end].splitlines())
    receipt = tmp_path / 'housekeeping.json'
    block = block.replace('/tmp/porota-predeploy-housekeeping.json', shlex.quote(str(receipt)))
    stub = tmp_path / 'sudo'
    data = json.dumps({'status': 'RED', 'reason': reason, 'effective_mode': 'cleanup'})
    stub.write_text('#!/bin/bash\n' + "printf '%s' " + shlex.quote(data) + ' > ' + shlex.quote(str(receipt))
                    + '\nexit ' + str(housekeeping_rc) + '\n')
    stub.chmod(0o700)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], REPO=str(ROOT), TMP_POLICY=str(POLICY))
    result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + block + '\necho REACHED_DOCKER_CLEANUP'],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stderr + result.stdout
    assert ('REACHED_DOCKER_CLEANUP' in result.stdout) == (expected == 0)
