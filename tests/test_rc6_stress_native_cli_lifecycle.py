"""Actual standalone CLI lifecycle controls; these small cases do not certify BIG."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap

import pytest

from scripts import rc6_issue465_stress as stress

SOURCE = Path(stress.__file__).absolute().parents[1]
CLI = SOURCE/'scripts/rc6_issue465_stress.py'

SUPERVISOR = """
import json, os, sys
from pathlib import Path
source, output = map(Path, sys.argv[1:3])
sys.path.insert(0, str(source))
from scripts import rc6_controlled_governed_runner as runner
runner.phase_namespace(source, output, 'execution')
environment = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
    'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'LC_ALL': 'C.UTF-8', 'LANG': 'C.UTF-8', 'TZ': 'UTC'}
for name in ('TMPDIR', 'RUNNER_TEMP', 'HYPOTHESIS_STORAGE_DIRECTORY', 'LOG_DIR'):
    environment[name] = os.environ[name]
report = runner.subprocess_phase(json.loads(sys.argv[3]), source,
    output/'phase.log', environment, limit=110)
runner.publish(output/'kernel.json', runner.canonical(report))
print(json.dumps(report))
"""


def native_phase(tmp_path, command):
    output = tmp_path/'kernel-output'
    output.mkdir(mode=0o700)
    completed = subprocess.run([sys.executable, '-I', '-B', '-c', SUPERVISOR,
        str(SOURCE), str(output), json.dumps(command)], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=150,
        env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
             'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'LC_ALL': 'C.UTF-8', 'LANG': 'C.UTF-8', 'TZ': 'UTC'})
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return json.loads(completed.stdout)


def assert_kernel_closed(kernel, code):
    assert kernel['returncode'] == code and kernel['actual_child_reaped']
    assert kernel['wait4_reaped_pid'] == kernel['pid']
    assert kernel['kernel_pre_popen_echild_verified'] and kernel['owned_children_exhaustion_verified']
    assert not kernel['kernel_wait4_zero_observed_irreversible_red']
    assert not kernel['late_observed_main_reap_irreversible_red']
    assert kernel['actual_launch_to_pid_reap_wall_seconds'] <= 110
    assert kernel['process_group_absent_at_main_reap'] and kernel['process_group_absent_after_reap']
    assert kernel['subreaper_restore_attempted_on_all_activated_paths']
    assert kernel['remaining_owned_children'] == kernel['residual_descendants_observed'] == []
    assert kernel['adopted_descendants_reaped'] == kernel['owned_group_signal_observations'] == []
    assert kernel['supervisor_errors'] == [] and not kernel['timed_out']


def cli_arguments(tmp_path):
    return ['--root', str(tmp_path/'data'), '--out', str(tmp_path/'result.json'),
            '--catalog-count', '20', '--observations-per-identity', '5', '--canonical-runtime']


def control_process(tmp_path, body):
    control = tmp_path/'control.py'
    control.write_text("import sys\nsys.path.insert(0, "+repr(str(SOURCE))+" )\n"+
        "if __name__ == '__main__':\n"+textwrap.indent(textwrap.dedent(body), '    '))
    return [sys.executable, '-I', '-B', str(control)]


@pytest.mark.parametrize('slow', (False, True), ids=('normal', 'slow-fsync'))
def test_standard_native_cli_reaps_actual_tracker_and_preserves_paper_business(tmp_path, slow):
    arguments = cli_arguments(tmp_path)+(['--slow-disk'] if slow else [])
    kernel = native_phase(tmp_path, [sys.executable, '-I', '-B', str(CLI), *arguments])
    result = json.loads((tmp_path/'result.json').read_text())
    assert_kernel_closed(kernel, 0)
    assert result['catalog_count'] == 20 and result['observations_materialized'] == 100
    assert result['business_resource_complete'] and result['source_database_unchanged']
    assert result['source_custody_before'] == result['source_custody_after']
    assert result['shadow']['cycle_completion'] and result['shadow']['child_cleanup_completed']
    assert 'OPEN_CYCLE_COMPLETED' in result['shadow']['phases']
    assert result['cycle_deadline_seconds'] == 90 and not result['diagnostic_only']
    assert result['resource_gates']['maximum_evidence_bytes'] == 128*1024**2
    assert result['resource_gates']['maximum_rss_bytes'] == 2*1024**3
    assert result['factual_exits']['closed'] == result['factual_exits']['sell_fills'] == 5
    assert result['real_orders_sent'] == result['provider_requests'] == 0
    assert result['real_routes'] == 'NOT_CALLED' and not result['runtime_touched']
    assert not result['import_proof_complete']  # No source pin supplied; no import attestation inferred.
    if slow:
        assert result['slow_fsync_exit_isolation_proven'] and result['shadow']['fsync']['fsync_completed']
    lifecycle = result['native_cli_lifecycle']
    assert lifecycle['status'] == 'GREEN' and lifecycle['entry_module'] == '__main__'
    assert lifecycle['pid'] == kernel['pid'] and lifecycle['cli_exit_code'] == 0
    assert lifecycle['native_resource_exit_code'] == 0 and lifecycle['fresh_infrastructure_verified']
    assert all(value is None for value in lifecycle['infrastructure_before'].values())
    assert lifecycle['original_inet_operation_audit_registration_witness_verified'] is True
    assert lifecycle['inet_socket_attempts'] == []
    capability = lifecycle['offline_ipv6_creation_capability']
    assert capability['status'] == 'INSTALLED_AND_KERNEL_WITNESSED' and capability['af_unix_ipc_functional']
    assert capability['creation_denial_witnesses'] == [
        {'family': 10, 'operation': 'socket_creation', 'errno': 97, 'socket_created': False}]
    finalization = lifecycle['child_infrastructure_finalization']
    assert lifecycle['child_infrastructure_finalized_before_main_exit']
    assert finalization['status'] == 'GREEN' and finalization['finalization_thread_finished']
    assert finalization['kernel_echild_before_phase_return'] and finalization['wall_seconds'] <= 5
    assert finalization['signal_guard_installed_and_witnessed']
    assert not finalization['forced_termination_attempted'] and not finalization['signal_vetoed']
    assert finalization['termination_signal_attempts'] == finalization['errors'] == []
    assert any(row['kind'] == 'resource_tracker' for row in finalization['stopped_owned_infrastructure'])


def test_existing_native_receipt_is_never_overwritten_and_no_fixture_starts(tmp_path):
    receipt = tmp_path/'result.json'
    receipt.write_bytes(b'KEEP_ORIGINAL_RECEIPT\n')
    kernel = native_phase(tmp_path, [sys.executable, '-I', '-B', str(CLI), *cli_arguments(tmp_path)])
    assert_kernel_closed(kernel, 1)
    assert receipt.read_bytes() == b'KEEP_ORIGINAL_RECEIPT\n'
    assert not (tmp_path/'data').exists()


def test_real_inet_bind_is_denied_before_fixture_and_native_cli_stays_red(tmp_path):
    command = control_process(tmp_path, """
        import socket
        from scripts import rc6_issue465_stress as stress
        def forbidden_bind(*arguments, **options):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as descriptor:
                descriptor.bind(('127.0.0.1', 0))
            raise AssertionError('NATIVE_BIND_UNEXPECTEDLY_ALLOWED')
        stress.run_stress = forbidden_bind
        try:
            stress.main("""+repr(cli_arguments(tmp_path))+""")
        except RuntimeError as error:
            assert str(error) == 'GOVERNED_INET_SOCKET_OPERATION_FORBIDDEN'
            raise SystemExit(1)
        raise AssertionError('DENIED_NATIVE_BIND_REPORTED_PASS')
    """)
    kernel = native_phase(tmp_path, command)
    assert_kernel_closed(kernel, 1)
    result = json.loads((tmp_path/'result.json').read_text())
    lifecycle = result['native_cli_lifecycle']
    assert not result['business_resource_complete'] and not result['resource_result_produced']
    assert lifecycle['status'] == 'RED' and lifecycle['cli_exit_code'] == 1
    assert lifecycle['inet_socket_attempts'] == ['socket.bind']
    assert lifecycle['original_inet_operation_audit_registration_witness_verified'] is True
    assert lifecycle['unexpected_error']['reason'] == 'GOVERNED_INET_SOCKET_OPERATION_FORBIDDEN'
    assert not (tmp_path/'data').exists()


def test_silent_audit_registration_veto_fails_closed_before_native_fixture(tmp_path):
    command = control_process(tmp_path, """
        import sys
        from scripts import rc6_issue465_stress as stress
        def veto_registration(event, arguments):
            if event == 'sys.addaudithook':
                raise RuntimeError('CONTROL_REGISTRATION_VETO')
        sys.addaudithook(veto_registration)
        try:
            stress.main("""+repr(cli_arguments(tmp_path))+""")
        except ValueError as error:
            assert str(error) == 'GOVERNED_INET_OPERATION_AUDIT_NOT_INSTALLED'
            raise SystemExit(1)
        raise AssertionError('SILENT_AUDIT_REGISTRATION_VETO_REPORTED_PASS')
    """)
    kernel = native_phase(tmp_path, command)
    assert_kernel_closed(kernel, 1)
    result = json.loads((tmp_path/'result.json').read_text())
    lifecycle = result['native_cli_lifecycle']
    assert not result['business_resource_complete'] and not result['resource_result_produced']
    assert lifecycle['status'] == 'RED' and lifecycle['cli_exit_code'] == 1
    assert lifecycle['original_inet_operation_audit_registration_witness_verified'] is False
    assert lifecycle['unexpected_error']['reason'] == 'GOVERNED_INET_OPERATION_AUDIT_NOT_INSTALLED'
    assert not (tmp_path/'data').exists()


def test_real_finalizer_sigterm_is_vetoed_and_cannot_turn_actual_business_into_cli_green(tmp_path):
    command = control_process(tmp_path, """
        import json, multiprocessing as mp, os, signal, time
        from multiprocessing import util
        from pathlib import Path
        from scripts import rc6_issue465_stress as stress
        from scripts import rc6_controlled_governed_runner as runner
        original = stress.run_stress
        owned, callbacks, veto_witness = [], [], {}
        def actual_forbidden_signal(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except RuntimeError as error:
                os.kill(pid, 0)
                veto_witness['child_alive_immediately_after_veto'] = owned[0].is_alive()
                veto_witness['reason'] = str(error)
                raise
        def actual_business_then_forbidden_finalizer(*arguments, **options):
            result = original(*arguments, **options)
            child = mp.get_context('fork').Process(target=time.sleep, args=(.75,))
            child.start()
            owned.append(child)
            callbacks.append(util.Finalize(child, actual_forbidden_signal,
                args=(child.pid,), exitpriority=1))
            return result
        stress.run_stress = actual_business_then_forbidden_finalizer
        code = stress.main("""+repr(cli_arguments(tmp_path))+""")
        child = owned[0]
        Path("""+repr(str(tmp_path/'observed.json'))+""").write_text(json.dumps({
            'child_pid': child.pid, 'veto_witness': veto_witness,
            'child_alive_after_natural_drain': child.is_alive(), 'child_exitcode': child.exitcode,
            'infrastructure_after_failed_cli': runner.child_infrastructure_snapshot(),
            'native_cli_code': code, 'real_finalizer_no_longer_active': not callbacks[0].still_active()}))
        # The original child ends naturally; there is no forced termination or
        # os._exit shortcut. The normal Python exit joins it before kernel reap.
        raise SystemExit(code)
    """)
    kernel = native_phase(tmp_path, command)
    # Safe natural JOIN/EOF closes the real tracker even though the veto is
    # permanently RED. No parent signal, adoption or main-reap race is waived.
    assert_kernel_closed(kernel, 1)
    result = json.loads((tmp_path/'result.json').read_text())
    observed = json.loads((tmp_path/'observed.json').read_text())
    tracker_pid = observed['infrastructure_after_failed_cli']['resource_tracker_pid']
    assert tracker_pid is None
    (tmp_path/'negative-control-observations.json').write_text(json.dumps({
        'status': 'RED_NATIVE_CLI_NOT_QUALIFIED', 'kernel_acceptance_claim': False,
        'process_group_absent_at_main_reap': kernel['process_group_absent_at_main_reap'],
        'kernel_wait4_zero_observed_irreversible_red': kernel['kernel_wait4_zero_observed_irreversible_red'],
        'adopted_descendants_reaped': kernel['adopted_descendants_reaped'],
        'tracked_resource_tracker_pid_after_failed_cli': tracker_pid,
        'kernel_echild_and_pgid_absent_after_owned_drain': True}, sort_keys=True))
    assert result['business_resource_complete'] and result['factual_exits']['closed'] == 5
    assert observed['veto_witness']['child_alive_immediately_after_veto']
    assert observed['veto_witness']['reason'] == 'CHILD_FINALIZATION_SIGNAL_DENIED_BEFORE_SYSCALL'
    assert not observed['child_alive_after_natural_drain'] and observed['child_exitcode'] == 0
    assert observed['real_finalizer_no_longer_active']
    assert observed['native_cli_code'] == 1
    lifecycle = result['native_cli_lifecycle']
    assert lifecycle['native_resource_exit_code'] == 0 and lifecycle['cli_exit_code'] == 1
    assert lifecycle['status'] == 'RED' and not lifecycle['child_infrastructure_finalized_before_main_exit']
    finalization = lifecycle['child_infrastructure_finalization']
    assert finalization['status'] == 'RED' and finalization['finalization_thread_finished']
    assert finalization['management_bound_seconds'] == 5 and finalization['wall_seconds'] <= 5
    assert finalization['kernel_echild_before_phase_return']
    assert {'pid': observed['child_pid'], 'exitcode': 0} in finalization['joined_children']
    assert any(row['kind'] == 'resource_tracker' for row in finalization['stopped_owned_infrastructure'])
    assert finalization['forced_termination_attempted'] and finalization['signal_vetoed']
    assert finalization['forced_termination'] is False
    assert finalization['termination_signal_attempts'] == [{'event': 'os.kill',
        'pid_or_pgid': observed['child_pid'], 'signal': signal.SIGTERM, 'vetoed_before_syscall': True}]
    assert any(row['reason'] == 'CHILD_FINALIZATION_SIGNAL_ATTEMPT_DENIED' for row in finalization['errors'])
