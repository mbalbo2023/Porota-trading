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
from tests.test_rc6_controlled_governed_runner import (
    controlled_phase_output, controlled_unit_parent, literal_git_tree)

SOURCE = Path(stress.__file__).absolute().parents[1]
CLI = SOURCE/'scripts/rc6_issue465_stress.py'

SUPERVISOR = """
import json, os, sys
from pathlib import Path
source, output = map(Path, sys.argv[1:3])
sys.path.insert(0, str(source))
from scripts import rc6_controlled_governed_runner as runner
context = json.loads(sys.argv[4])
unit_source = Path(context['source_root'])
claim = context['namespace_receipt']
pin = runner.source_pin(unit_source, claim['binding']['candidate_sha'], claim['binding']['candidate_tree'])
runner.phase_namespace(unit_source, output, 'execution', authenticated_binding=claim)
environment = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
    'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'LC_ALL': 'C.UTF-8', 'LANG': 'C.UTF-8', 'TZ': 'UTC'}
for name in ('TMPDIR', 'RUNNER_TEMP', 'HYPOTHESIS_STORAGE_DIRECTORY', 'LOG_DIR'):
    environment[name] = os.environ[name]
report = runner.subprocess_phase(json.loads(sys.argv[3]), source,
    output/'phase.log', environment, limit=110)
after = runner.source_pin(unit_source, claim['binding']['candidate_sha'], claim['binding']['candidate_tree'])
runner.compare_source(pin, after)
runner.publish(output/'kernel.json', runner.canonical(report))
print(json.dumps(report))
"""


@pytest.fixture
def tmp_path(tmp_path_factory):
    """Short real namespace and tiny literal Git pin, diagnostic fixture only."""
    parent = controlled_unit_parent()
    root, sha, tree = literal_git_tree(parent)
    output, claim = controlled_phase_output(root, sha, tree, 'native-cli-lifecycle', parent=parent)
    context = {'source_root': str(root), 'namespace_receipt': claim,
               'scope': 'CONTROLLED_UNIT_FIXTURE_ONLY', 'promotion_qualified': False}
    (output/'unit-phase-binding.json').write_text(json.dumps(context))
    return output


def native_phase(tmp_path, command):
    output = tmp_path/'kernel-output'
    output.mkdir(mode=0o700)
    context = json.loads((tmp_path/'unit-phase-binding.json').read_text())
    completed = subprocess.run([sys.executable, '-I', '-B', '-c', SUPERVISOR,
        str(SOURCE), str(output), json.dumps(command), json.dumps(context)], stdout=subprocess.PIPE,
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
    control_path = tmp_path/'result.json.owned-fin.json'
    control = json.loads(control_path.read_text())
    assert control['schema'] == 'rc6.issue465.native-cli-owned-fin.v1'
    assert control['pid'] == kernel['pid'] and control['entry_module'] == '__main__'
    assert control['native_cli_lifecycle'] == lifecycle
    assert control['physical_fin_closed'] and control['finalized_before_payload_postreads']
    assert control['original_child_finalization_seconds'] == 5 and control['cycle_deadline_seconds'] == 90
    assert control['business_GREEN_inferred_from_FIN'] is False and control['real_orders_sent'] == 0
    assert control['payload_path'] == str(tmp_path/'result.json')
    assert control_path.stat().st_mtime_ns <= (tmp_path/'result.json').stat().st_mtime_ns
    assert lifecycle['child_infrastructure_finalized_before_payload_postreads']
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


@pytest.mark.parametrize('existing', ('result.json', 'result.json.owned-fin.json'))
def test_existing_native_receipt_is_never_overwritten_and_no_fixture_starts(tmp_path, existing):
    receipt = tmp_path/existing
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



def test_actual_initial_read_payload_is_released_before_both_native_ticks(tmp_path):
    command = control_process(tmp_path, """
        import json, queue, threading, weakref
        from pathlib import Path
        from cg_paper_workspace import artifact_root
        from rc6_dynamic_universe import runtime
        from rc6_shadow_runtime.worker import ShadowRuntime
        from rc6_shadow_runtime.read_contract import DEFAULT_READ_CONTRACT, current_read_contract
        from scripts import rc6_issue465_stress as stress
        from scripts import rc6_controlled_governed_runner as runner
        initial = runner.child_infrastructure_snapshot()
        assert all(value is None for value in initial.values())
        capability = runner.restrict_inet_creation()
        observations = {'inet_socket_attempts': [], 'inet_socket_constructor_requests': [],
                        'subprocess_executable_counts': {}}
        assert runner.install_phase_audit(observations) is True
        class WeakResult(dict):
            pass
        class WeakList(list):
            pass
        original_read, original_tick = runtime.read_runtime, ShadowRuntime.tick
        references, scalars, ticks, contract_fingerprints = [], {}, [], []
        def tracked_initial_read(database, **options):
            assert not references
            assert options == {'as_of': stress.AT, 'row_limit': 20000}
            contract = current_read_contract()
            assert contract.runtime_query_seconds == .5
            assert contract.fingerprint() == DEFAULT_READ_CONTRACT.fingerprint()
            contract_fingerprints.append(contract.fingerprint())
            actual = original_read(database, **options)
            scalars.update(observation_count=len(actual['observations']),
                catalog_count=len(actual['catalog']),
                observation_read_truncated=actual['observation_read_truncated'])
            payload = WeakResult(actual)
            payload['observations'] = WeakList(actual['observations'])
            payload['catalog'] = WeakList(actual['catalog'])
            assert payload == actual
            references.extend(weakref.ref(value) for value in
                (payload, payload['observations'], payload['catalog']))
            return payload
        def checked_tick(worker, at):
            assert len(references) == 3 and all(reference() is None for reference in references)
            ticks.append(at.isoformat())
            return original_tick(worker, at)
        database = Path("""+repr(str(tmp_path/'data'/'paper_v17'/'observer_v17.db'))+""")
        database.parent.mkdir(mode=0o700, parents=True)
        stress.fixture_database(database, catalog_count=20, observations_per_identity=5)
        source_before = stress.source_custody_snapshot(database)
        output = artifact_root(database)/'dynamic-shadow'
        messages = queue.Queue()
        runtime.read_runtime, ShadowRuntime.tick = tracked_initial_read, checked_tick
        try:
            stress._shadow_child_work(str(database), str(output), threading.Event(),
                threading.Event(), messages, False, 128*1024**2, True, None, None, None)
        finally:
            runtime.read_runtime, ShadowRuntime.tick = original_read, original_tick
            finalization = runner.finalize_child_infrastructure(initial, limit=5)
        assert finalization['status'] == 'GREEN' and finalization['finalization_thread_finished']
        assert finalization['kernel_echild_before_phase_return'] and finalization['wall_seconds'] <= 5
        assert not finalization['forced_termination_attempted'] and not finalization['signal_vetoed']
        assert finalization['termination_signal_attempts'] == finalization['errors'] == []
        assert observations['inet_socket_attempts'] == []
        records = []
        while not messages.empty():
            records.append(messages.get_nowait())
        finals = [record for record in records if record.get('_probe_event') == 'FINAL']
        assert len(finals) == 1
        shadow = finals[0]
        Path("""+repr(str(tmp_path/'lifetime-observed.json'))+""").write_text(json.dumps({
            'scope': 'CONTROLLED_UNIT_FIXTURE_ONLY', 'shadow': shadow,
            'scalars': scalars, 'ticks': ticks, 'contract_fingerprints': contract_fingerprints,
            'big_qualified': False}, sort_keys=True))
        assert shadow['cycle_completion'] and shadow['committed_sequence'] == 2
        assert contract_fingerprints == [DEFAULT_READ_CONTRACT.fingerprint()]
        assert ticks == [stress.PRE.isoformat(), stress.AT.isoformat()]
        assert scalars == {'observation_count': 100, 'catalog_count': 20,
                          'observation_read_truncated': False}
        assert shadow['observations_returned'] == scalars['observation_count']
        assert shadow['observation_read_truncated'] is scalars['observation_read_truncated']
        assert shadow['catalog_ready_count'] == scalars['catalog_count']
        source_after = stress.source_custody_snapshot(database)
        assert source_before == source_after
        evidence = {'schema': 'rc6.issue465.initial-read-lifetime-control.v1',
            'scope': 'NATIVE20_COMPONENT_LIFETIME_ONLY_NOT_FULL_STRESS_OR_BIG',
            'initial_payload_and_both_lists_released': all(reference() is None for reference in references),
            'scalars': scalars, 'tick_cutoffs': ticks, 'shadow': shadow,
            'read_contract_sha256': contract_fingerprints[0],
            'source_custody_before': source_before, 'source_custody_after': source_after,
            'child_infrastructure_finalization': finalization,
            'offline_ipv6_creation_capability': capability, 'operation_audit': observations,
            'big_qualified': False, 'import_proof_complete': False}
        with Path("""+repr(str(tmp_path/'lifetime.json'))+""").open('x') as stream:
            json.dump(evidence, stream, sort_keys=True)
    """)
    kernel = native_phase(tmp_path, command)
    assert_kernel_closed(kernel, 0)
    evidence = json.loads((tmp_path/'lifetime.json').read_text())
    assert evidence['initial_payload_and_both_lists_released']
    assert evidence['shadow']['cycle_completion'] and evidence['shadow']['committed_sequence'] == 2
    assert evidence['shadow']['real_orders_sent'] == evidence['shadow']['provider_requests'] == 0
    assert evidence['shadow']['real_routes'] == 'NOT_CALLED'
    assert not evidence['big_qualified'] and not evidence['import_proof_complete']


def test_real_live_shadow_producer_vetoes_payload_postreads_until_actual_reap(tmp_path):
    command = control_process(tmp_path, """
        import json, multiprocessing as mp
        from pathlib import Path
        from scripts import rc6_issue465_stress as stress
        from scripts import rc6_controlled_governed_runner as runner
        initial = runner.child_infrastructure_snapshot()
        assert all(value is None for value in initial.values())
        capability = runner.restrict_inet_creation()
        observations = {'inet_socket_attempts': [], 'inet_socket_constructor_requests': [],
                        'subprocess_executable_counts': {}}
        assert runner.install_phase_audit(observations) is True
        database = Path("""+repr(str(tmp_path/'data'/'source.db'))+""")
        database.parent.mkdir(mode=0o700, parents=True)
        stress.fixture_database(database, catalog_count=20, observations_per_identity=5)
        source_before = stress.source_custody_snapshot(database)
        context = mp.get_context('fork')
        started, release = context.Event(), context.Event()
        def real_live_producer():
            started.set()
            assert release.wait(5)
        child = context.Process(target=real_live_producer)
        child.start()
        postread_calls, veto = [], None
        try:
            assert started.wait(5) and child.is_alive()
            try:
                stress._require_reaped_shadow_producer(child)
                postread_calls.append('SOURCE_AFTER')
                stress.source_custody_snapshot(database)
            except RuntimeError as error:
                veto = str(error)
            assert veto == 'SHADOW_PRODUCER_NOT_REAPED_NO_PAYLOAD_POSTREAD'
            assert child.is_alive() and child.exitcode is None and postread_calls == []
        finally:
            release.set()
            child.join(5)
            finalization = runner.finalize_child_infrastructure(initial, limit=5)
        assert not child.is_alive() and child.exitcode == 0
        assert finalization['status'] == 'GREEN' and finalization['finalization_thread_finished']
        assert finalization['kernel_echild_before_phase_return'] and finalization['wall_seconds'] <= 5
        assert not finalization['forced_termination_attempted'] and not finalization['signal_vetoed']
        assert finalization['termination_signal_attempts'] == finalization['errors'] == []
        assert observations['inet_socket_attempts'] == []
        stress._require_reaped_shadow_producer(child)
        source_after = stress.source_custody_snapshot(database)
        assert source_before == source_after
        evidence = {'schema': 'rc6.issue465.live-producer-postread-veto-control.v1',
            'scope': 'REAL_LIVE_PRODUCER_NEGATIVE_GUARD_ONLY_NOT_STRESS_RESOURCE_QUALIFICATION',
            'producer_pid': child.pid, 'producer_exitcode_after_natural_reap': child.exitcode,
            'veto_reason': veto, 'payload_postread_calls_while_live': postread_calls,
            'source_custody_before': source_before, 'source_custody_after': source_after,
            'child_infrastructure_finalization': finalization,
            'offline_ipv6_creation_capability': capability, 'operation_audit': observations,
            'big_qualified': False, 'cycle_deadline_seconds_changed': False,
            'original_cleanup_deadline_can_be_reclassified': False}
        with Path("""+repr(str(tmp_path/'postread-veto.json'))+""").open('x') as stream:
            json.dump(evidence, stream, sort_keys=True)
    """)
    kernel = native_phase(tmp_path, command)
    assert_kernel_closed(kernel, 0)
    evidence = json.loads((tmp_path/'postread-veto.json').read_text())
    assert evidence['veto_reason'] == 'SHADOW_PRODUCER_NOT_REAPED_NO_PAYLOAD_POSTREAD'
    assert evidence['payload_postread_calls_while_live'] == []
    assert evidence['producer_exitcode_after_natural_reap'] == 0
    assert not evidence['big_qualified'] and not evidence['cycle_deadline_seconds_changed']


def test_native_absolute_cleanup_deadline_rejects_extra_budget_before_real_finalizer(tmp_path):
    command = control_process(tmp_path, """
        import errno, json, math, os, time
        from multiprocessing import util
        from pathlib import Path
        from scripts import rc6_controlled_governed_runner as runner
        initial = runner.child_infrastructure_snapshot()
        assert all(value is None for value in initial.values())
        calls, denials = [], []
        finalizer = util.Finalize(None, lambda: calls.append('ACTUAL_FINALIZER'), exitpriority=1)
        for deadline in (time.monotonic()+60, time.monotonic()-1, math.inf, math.nan, True):
            try:
                runner.finalize_child_infrastructure(initial, limit=5, cleanup_deadline=deadline)
            except ValueError as error:
                assert str(error) == 'ORIGINAL_ABSOLUTE_CHILD_FINALIZATION_DEADLINE_REQUIRED'
                denials.append(type(deadline).__name__)
            else:
                raise AssertionError('ABSOLUTE_DEADLINE_EXTENSION_OR_INVALID_CLOCK_ACCEPTED')
            assert finalizer.still_active() and calls == []
        finalizer.cancel()
        try:
            os.wait4(-1, os.WNOHANG)
        except ChildProcessError as error:
            assert error.errno == errno.ECHILD
        else:
            raise AssertionError('KERNEL_ECHILD_NOT_OBSERVED')
        Path("""+repr(str(tmp_path/'deadline-api.json'))+""").write_text(json.dumps({
            'denials': denials, 'finalizer_executed': calls,
            'management_bound_seconds': 5, 'gate_qualified': False}))
    """)
    kernel = native_phase(tmp_path, command)
    assert_kernel_closed(kernel, 0)
    result = json.loads((tmp_path/'deadline-api.json').read_text())
    assert len(result['denials']) == 5 and result['finalizer_executed'] == []
    assert result['management_bound_seconds'] == 5 and not result['gate_qualified']


def test_real_early_finalization_exception_never_retries_or_resets_deadline(tmp_path):
    command = control_process(tmp_path, """
        import json
        from pathlib import Path
        from scripts import rc6_issue465_stress as stress
        from scripts import rc6_controlled_governed_runner as runner
        original = runner.finalize_child_infrastructure
        attempts, actual = [], []
        def real_finalization_then_failure(initial, limit=5, *, cleanup_deadline=None):
            attempts.append({'limit': limit, 'deadline': cleanup_deadline})
            receipt = original(initial, limit=limit, cleanup_deadline=cleanup_deadline)
            assert receipt['status'] == 'GREEN' and receipt['finalization_thread_finished']
            actual.append(receipt)
            raise RuntimeError('REAL_EARLY_FINALIZATION_RECEIPT_NOT_RETURNED')
        runner.finalize_child_infrastructure = real_finalization_then_failure
        try:
            stress.main("""+repr(cli_arguments(tmp_path))+""")
        except RuntimeError as error:
            assert str(error) == 'REAL_EARLY_FINALIZATION_RECEIPT_NOT_RETURNED'
        else:
            raise AssertionError('UNKNOWN_EARLY_FINALIZATION_REPORTED_GREEN')
        assert len(attempts) == 1 and attempts[0]['limit'] == 5
        assert actual[0]['cleanup_deadline_monotonic'] == attempts[0]['deadline']
        Path("""+repr(str(tmp_path/'attempts.json'))+""").write_text(json.dumps({
            'attempts': attempts, 'actual_receipt': actual[0], 'gate_qualified': False}))
        raise SystemExit(1)
    """)
    kernel = native_phase(tmp_path, command)
    assert_kernel_closed(kernel, 1)
    result = json.loads((tmp_path/'result.json').read_text())
    control = json.loads((tmp_path/'result.json.owned-fin.json').read_text())
    observed = json.loads((tmp_path/'attempts.json').read_text())
    lifecycle = result['native_cli_lifecycle']
    assert lifecycle['status'] == 'RED' and lifecycle['cli_exit_code'] == 1
    assert not lifecycle['child_infrastructure_finalized_before_payload_postreads']
    assert not lifecycle['child_infrastructure_finalized_before_main_exit']
    assert lifecycle['terminal_cleanup_blocked_by_prior_unknown']
    assert len(lifecycle['child_finalization_attempts']) == len(observed['attempts']) == 1
    attempt = lifecycle['child_finalization_attempts'][0]
    assert attempt['stage'] == 'before_payload_postreads' and attempt['receipt'] is None
    assert attempt['error']['reason'] == 'REAL_EARLY_FINALIZATION_RECEIPT_NOT_RETURNED'
    assert attempt['cleanup_deadline_monotonic'] == lifecycle['child_finalization_single_deadline_monotonic']
    assert not control['physical_fin_closed'] and not control['finalized_before_payload_postreads']
    assert observed['actual_receipt']['management_bound_seconds'] == 5
    assert not observed['gate_qualified']
