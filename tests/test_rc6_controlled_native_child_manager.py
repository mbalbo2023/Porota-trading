"""Real kernel controls for the RC6 ready-child drain; no mocked custody."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from scripts import rc6_controlled_native_child_manager as native
from scripts import porota_predeploy_test_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / 'docs/audits/rc6-convergence-persistence-evidence/run_full_horizon_1201_v2.py.source'
LEGACY_SHA256 = 'ac9ba0f457ee4592646d91c5654f037efbfa2bc4f758469e16b7cf51d87e7aec'
PRODUCER_SOURCE = '"""Owned native process controls only; no product/source/data/network operations."""\nfrom __future__ import annotations\nimport argparse\nimport hashlib\nimport json\nimport os\nfrom pathlib import Path\nimport stat\nimport time\n\n\ndef proc_identity(pid):\n    proc = Path(\'/proc\') / str(pid)\n    fields = {key: value.strip() for key, value in\n              (line.split(\':\', 1) for line in (proc/\'status\').read_text().splitlines() if \':\' in line)}\n    tail = (proc/\'stat\').read_text().rsplit(\')\', 1)[1].split()\n    return {\'pid\': int(fields[\'Pid\']), \'ppid\': int(fields[\'PPid\']),\n            \'state\': fields[\'State\'].split()[0], \'uid\': [int(x) for x in fields[\'Uid\'].split()],\n            \'pgid\': int(tail[2]), \'session\': int(tail[3])}\n\n\ndef publish_control(path, receipt):\n    assert path.parent.is_dir() and stat.S_IMODE(path.parent.stat().st_mode) == 0o700\n    data = (json.dumps(receipt, sort_keys=True, indent=2)+\'\\n\').encode()\n    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)\n    with os.fdopen(fd, \'wb\') as stream:\n        stream.write(data)\n    assert path.stat().st_nlink == 1\n\n\ndef main():\n    parser = argparse.ArgumentParser(description=__doc__)\n    parser.add_argument(\'--case\', choices=(\'ready100\', \'liveDetached6\', \'simpleExit\'), required=True)\n    parser.add_argument(\'--receipt\', type=Path, required=True)\n    parser.add_argument(\'--exit-code\', type=int, choices=(0, 1), required=True)\n    parser.add_argument(\'--separate-session\', action=\'store_true\')\n    args = parser.parse_args()\n    assert os.getuid() == os.geteuid() != 0\n    assert not args.receipt.exists()\n    rows = []\n    witnesses = []\n    producer = proc_identity(os.getpid())\n    if args.case == \'ready100\':\n        pids = []\n        for unused in range(100):\n            pid = os.fork()\n            if pid == 0:\n                try:\n                    if args.separate_session:\n                        os.setsid()\n                except BaseException:\n                    os._exit(95)\n                os._exit(0)\n            pids.append(pid)\n        for pid in pids:\n            result = os.waitid(os.P_PID, pid, os.WEXITED|os.WNOWAIT)\n            assert result.si_pid == pid and result.si_code == os.CLD_EXITED and result.si_status == 0\n            identity = proc_identity(pid)\n            assert identity[\'pid\'] == pid and identity[\'ppid\'] == os.getpid()\n            assert identity[\'state\'] == \'Z\' and identity[\'uid\'] == producer[\'uid\']\n            if args.separate_session:\n                assert identity[\'pgid\'] == identity[\'session\'] == pid\n            else:\n                assert identity[\'pgid\'] == producer[\'pgid\'] and identity[\'session\'] == producer[\'session\']\n            rows.append(identity)\n            witnesses.append({\'pid\': pid, \'si_code\': result.si_code, \'si_status\': result.si_status,\n                              \'flags\': \'WEXITED|WNOWAIT\', \'reaped\': False})\n        assert len(rows) == 100 and len({row[\'pid\'] for row in rows}) == 100\n    elif args.case == \'liveDetached6\':\n        assert not args.separate_session\n        reader, writer = os.pipe()\n        pid = os.fork()\n        if pid == 0:\n            os.close(reader)\n            os.setsid()\n            os.write(writer, b\'R\')\n            os.close(writer)\n            time.sleep(6)\n            os._exit(0)\n        os.close(writer)\n        assert os.read(reader, 1) == b\'R\'\n        os.close(reader)\n        identity = proc_identity(pid)\n        assert identity[\'pid\'] == pid and identity[\'ppid\'] == os.getpid()\n        assert identity[\'state\'] not in (\'Z\', \'X\') and identity[\'uid\'] == producer[\'uid\']\n        assert identity[\'pgid\'] == identity[\'session\'] == pid\n        rows.append(identity)\n    else:\n        assert not args.separate_session\n    receipt = {\'schema\': \'rc6.real-owned-process-control.v1\',\n               \'classification\': \'PROCESS_CONTROL_ONLY_NOT_GOV_BIG_IMAGE_RUNTIME_QUALIFICATION\',\n               \'case\': args.case, \'producer\': producer, \'children\': rows,\n               \'waitid_wnowait_witnesses\': witnesses,\n               \'separate_session\': args.separate_session,\n               \'all100_verified_zombie_before_producer_exit\': args.case == \'ready100\',\n               \'live_detached_seconds\': 6 if args.case == \'liveDetached6\' else None,\n               \'live6_does_not_change_cleanup5_or_phase_budget\': True,\n               \'producer_exit_code\': args.exit_code, \'child_reaps_by_producer\': 0,\n               \'termination_signals_sent\': [], \'source_or_data_reads\': False,\n               \'network_operations\': False}\n    publish_control(args.receipt, receipt)\n    os._exit(args.exit_code)\n\n\nif __name__ == \'__main__\':\n    main()\n'
SUPERVISOR_SOURCE = "import ctypes\nimport errno\nimport hashlib\nimport json\nimport os\nfrom pathlib import Path\nimport runpy\nimport sys\nimport time\n\nmanager_path, expected_sha, producer_path, case, output_root = sys.argv[1:]\noutput_root = Path(output_root)\nraw = Path(manager_path).read_bytes()\nassert hashlib.sha256(raw).hexdigest() == expected_sha\nmanager = runpy.run_path(manager_path)\nkillpg_requests = []\ndef observe_killpg(event, arguments):\n    if event == 'os.killpg':\n        killpg_requests.append({'pgid': arguments[0], 'signal': arguments[1]})\nsys.addaudithook(observe_killpg)\ninitial = manager['pre_capture_kernel_state']()\ncommand = [sys.executable, '-I', '-B', producer_path, '--case', case,\n           '--receipt', str(output_root / 'producer-control.json'),\n           '--exit-code', '0' if case == 'simpleExit' else '1']\nkernel = manager['managed_native_child'](command, output_root,\n    output_root / 'producer.log', dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),\n    60, terminate_grace=2, progress_poll=5)\nclosed = manager['managed_custody_closed'](kernel)\nphase_green = manager['managed_phase_green'](kernel)\nfollowup = None\nif closed:\n    final = manager['pre_capture_kernel_state']()\n    assert final['subreaper_state_after'] == initial['subreaper_state_after']\nelse:\n    # This is a separate failed-control diagnostic by the SAME supervisor.\n    # It never amends, accepts or converts the original phase's UNKNOWN/RED.\n    assert kernel['actual_child_reaped'] and kernel['wait4_reaped_pid'] == kernel['pid']\n    assert kernel['kernel_pre_popen_echild_verified']\n    assert kernel['subreaper_activation_readback_verified']\n    assert kernel['supervisor_pid'] == os.getpid()\n    libc = ctypes.CDLL(None, use_errno=True)\n    state = ctypes.c_int()\n    assert libc.prctl(37, ctypes.byref(state), 0, 0, 0) == 0 and state.value == 1\n    started = time.monotonic()\n    deadline = started + 5\n    observations = []\n    exhausted = False\n    while len(observations) < 1024 and time.monotonic() < deadline:\n        try:\n            pid, status, usage = os.wait4(-1, os.WNOHANG)\n        except ChildProcessError as error:\n            assert error.errno == errno.ECHILD\n            observations.append({'outcome': 'ECHILD', 'errno': error.errno})\n            exhausted = True\n            break\n        if pid:\n            observations.append({'outcome': 'PID_REAPED', 'pid': pid,\n                'exit_code': os.waitstatus_to_exitcode(status),\n                'kernel_lifetime_peak_rss_bytes': usage.ru_maxrss * 1024})\n        else:\n            observations.append({'outcome': 'LIVE_OWN_CHILD', 'pid': 0})\n            time.sleep(.05)\n    assert exhausted\n    try:\n        os.killpg(kernel['pid'], 0)\n    except ProcessLookupError:\n        absent = True\n    else:\n        absent = False\n    assert absent\n    assert libc.prctl(36, kernel['subreaper_previous_state'], 0, 0, 0) == 0\n    restored = ctypes.c_int()\n    assert libc.prctl(37, ctypes.byref(restored), 0, 0, 0) == 0\n    assert restored.value == kernel['subreaper_previous_state'] == initial['subreaper_state_before']\n    final = manager['pre_capture_kernel_state']()\n    followup = {'scope': 'SEPARATE_SAME_PID_FAILED_CONTROL_DIAGNOSTIC_NOT_PHASE_ACCEPTANCE',\n        'supervisor_pid': os.getpid(), 'original_phase_result_changed': False,\n        'original_phase_owned_fin_closed': closed, 'original_phase_green': phase_green,\n        'diagnostic_bound_seconds': 5, 'diagnostic_wall_seconds': time.monotonic()-started,\n        'kernel_observations': observations, 'ECHILD_verified': exhausted,\n        'original_owned_PGID_absent': absent, 'subreaper_restored': True,\n        'producer_payload_reads_before_diagnostic_FIN': 0, 'termination_signals_sent': []}\n# No Source/producer/log reads between launch and physical kernel FIN.\npath = output_root / 'producer-control.json'\nfd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)\nwith os.fdopen(fd, 'rb') as stream:\n    before = os.fstat(stream.fileno())\n    fields = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_gid',\n              'st_size', 'st_blocks', 'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns')\n    before11 = {name: getattr(before, name) for name in fields}\n    assert before.st_uid == os.geteuid() and before.st_nlink == 1\n    assert before.st_mode & 0o777 == 0o600 and before.st_size <= 65536\n    assert before.st_dev == output_root.lstat().st_dev\n    control_raw = stream.read(65537)\n    assert len(control_raw) == before.st_size\n    assert before11 == {name: getattr(os.fstat(stream.fileno()), name) for name in fields}\n    assert before11 == {name: getattr(path.lstat(), name) for name in fields}\ncontrol = json.loads(control_raw)\nprint(json.dumps({'supervisor_pid': os.getpid(), 'manager_sha256': expected_sha,\n    'initial_kernel': initial, 'main_phase_kernel': kernel,\n    'main_phase_owned_fin_closed': closed, 'main_phase_green': phase_green,\n    'diagnostic_followup': followup, 'final_kernel': final,\n    'producer_control_sha256': hashlib.sha256(control_raw).hexdigest(),\n    'producer_control': control, 'producer_control_11stat': before11,\n    'python_killpg_requests': killpg_requests,\n    'producer_or_log_reads_while_owned_child_active': 0}, sort_keys=True))\n"

def exclusive_file(path, raw):
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def native_control(tmp_path, *, legacy, case):
    output = tmp_path / 'owned-native-control'
    os.mkdir(output, 0o700)
    producer = output / 'producer.py'
    supervisor = output / 'supervisor.py'
    exclusive_file(producer, PRODUCER_SOURCE.encode())
    exclusive_file(supervisor, SUPERVISOR_SOURCE.encode())
    source = LEGACY if legacy else ROOT / workspace.DRIVER
    expected = LEGACY_SHA256 if legacy else workspace.ORIGINAL_DRIVER_SHA256
    command = [sys.executable, '-I', '-B', str(supervisor), str(source), expected,
               str(producer), case, str(output)]
    # The outer manager also obtains actual FIN before reading its own log.
    before = native.pre_capture_kernel_state()
    outer = native.managed_native_child(command, output, output / 'supervisor.log',
        dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), 60)
    exclusive_file(output / 'outer-kernel.json', (json.dumps(outer, sort_keys=True)+'\n').encode())
    assert native.managed_custody_closed(outer) and native.managed_phase_green(outer)
    assert native.pre_capture_kernel_state()['subreaper_state_after'] == before['subreaper_state_after']
    result = json.loads((output / 'supervisor.log').read_bytes())
    assert result['manager_sha256'] == expected
    assert result['supervisor_pid'] == outer['pid']
    assert result['initial_kernel']['kernel_echild_verified'] is True
    assert result['final_kernel']['kernel_echild_verified'] is True
    assert result['producer_or_log_reads_while_owned_child_active'] == 0
    assert len(result['producer_control_11stat']) == 11
    assert all(row['pgid'] == result['main_phase_kernel']['pid'] and row['signal'] == 0
               for row in result['python_killpg_requests'])
    return result


@pytest.mark.parametrize('legacy', [True, False], ids=['preserved-v2-red', 'fix-forward-fin-closed-red'])
def test_ready100_real_owned_zombies_keep_exit1_red_and_close_only_within_proved_scope(tmp_path, legacy):
    result = native_control(tmp_path, legacy=legacy, case='ready100')
    kernel, producer = result['main_phase_kernel'], result['producer_control']
    children = producer['children']
    assert producer['all100_verified_zombie_before_producer_exit'] is True
    assert producer['child_reaps_by_producer'] == 0
    assert len(children) == len({row['pid'] for row in children}) == 100
    assert all(row['state'] == 'Z' and row['ppid'] == producer['producer']['pid']
               and row['uid'] == producer['producer']['uid'] for row in children)
    assert all(row['reaped'] is False and row['si_status'] == 0
               and row['flags'] == 'WEXITED|WNOWAIT' for row in producer['waitid_wnowait_witnesses'])
    assert kernel['pid'] == producer['producer']['pid'] and kernel['returncode'] == 1
    assert result['main_phase_green'] is False
    assert kernel['timed_out'] is kernel['late_observed_main_reap_irreversible_red'] is False
    assert kernel['launcher_management_deadline_seconds'] == 60
    assert kernel['owned_cleanup_management_bound_seconds'] == 5
    assert kernel['owned_group_signal_observations'] == []
    assert kernel['kernel_wait4_zero_observed_irreversible_red'] is False
    first_reaps = kernel['adopted_descendants_reaped']
    if legacy:
        assert result['main_phase_owned_fin_closed'] is False
        assert kernel['owned_children_exhaustion_verified'] is False
        assert not any(row['phase'] == 'after_main_pid_reap' and row['outcome'] == 'ECHILD'
                       for row in kernel['kernel_owned_child_observations'])
        assert any(row['reason'] == 'OWN_CHILD_EXHAUSTION_NOT_PROVED_WITHIN_BOUND'
                   for row in kernel['supervisor_errors'])
        followup = result['diagnostic_followup']
        assert followup['original_phase_result_changed'] is False
        assert followup['original_phase_owned_fin_closed'] is followup['original_phase_green'] is False
        assert followup['ECHILD_verified'] is followup['original_owned_PGID_absent'] is True
        assert followup['subreaper_restored'] is True
        assert followup['supervisor_pid'] == kernel['supervisor_pid'] == result['supervisor_pid']
        assert followup['producer_payload_reads_before_diagnostic_FIN'] == 0
        extra_reaps = [row for row in followup['kernel_observations'] if row['outcome'] == 'PID_REAPED']
    else:
        assert result['main_phase_owned_fin_closed'] is True
        assert kernel['termination_reap_restore_cleanup_seconds'] < 5
        assert kernel['owned_children_exhaustion_verified'] is True
        assert kernel['process_group_absent_after_reap'] is True
        assert kernel['subreaper_restore_attempted'] is kernel['subreaper_restoration_readback_verified'] is True
        assert kernel['supervisor_errors'] == [] and result['diagnostic_followup'] is None
        extra_reaps = []
    reaps = first_reaps + extra_reaps
    assert len(reaps) == 100 and {row['pid'] for row in reaps} == {row['pid'] for row in children}
    assert all(row['exit_code'] == 0 for row in reaps)


def test_normal_exit0_has_same_parent_kernel_fin_without_residuals(tmp_path):
    result = native_control(tmp_path, legacy=False, case='simpleExit')
    kernel = result['main_phase_kernel']
    assert result['main_phase_owned_fin_closed'] is result['main_phase_green'] is True
    assert result['diagnostic_followup'] is None
    assert kernel['returncode'] == 0 and kernel['adopted_descendants_reaped'] == []
    assert kernel['owned_children_exhaustion_verified'] is True
    assert kernel['process_group_absent_at_main_reap'] is kernel['process_group_absent_after_reap'] is True
    assert kernel['subreaper_restoration_readback_verified'] is True
    assert kernel['owned_group_signal_observations'] == kernel['residual_descendants_observed'] == []
    assert kernel['supervisor_errors'] == []


def test_live_detached6_remains_unknown_at_cleanup5_and_vetoes_absent_group_signals(tmp_path):
    result = native_control(tmp_path, legacy=False, case='liveDetached6')
    kernel = result['main_phase_kernel']
    assert result['main_phase_owned_fin_closed'] is result['main_phase_green'] is False
    assert kernel['returncode'] == 1 and kernel['timed_out'] is False
    assert kernel['kernel_wait4_zero_observed_irreversible_red'] is True
    assert kernel['owned_cleanup_management_bound_seconds'] == 5
    assert kernel['owned_children_exhaustion_verified'] is False
    assert kernel['process_group_absent_at_main_reap'] is kernel['process_group_absent_after_reap'] is True
    assert kernel['owned_group_signal_observations'] == []
    assert any(row['reason'] == 'OWN_CHILD_EXHAUSTION_NOT_PROVED_WITHIN_BOUND'
               for row in kernel['supervisor_errors'])
    followup = result['diagnostic_followup']
    assert followup['original_phase_result_changed'] is False
    assert followup['original_phase_owned_fin_closed'] is followup['original_phase_green'] is False
    assert followup['ECHILD_verified'] is followup['original_owned_PGID_absent'] is True
    assert followup['subreaper_restored'] is True
    assert followup['termination_signals_sent'] == []
    reaps = [row for row in followup['kernel_observations'] if row['outcome'] == 'PID_REAPED']
    assert len(reaps) == 1 and reaps[0]['pid'] == result['producer_control']['children'][0]['pid']
    assert reaps[0]['exit_code'] == 0
