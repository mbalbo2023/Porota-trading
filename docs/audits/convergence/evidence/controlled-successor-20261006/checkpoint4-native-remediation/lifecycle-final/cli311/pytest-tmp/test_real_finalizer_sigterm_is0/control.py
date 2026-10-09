import sys
sys.path.insert(0, '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source' )
if __name__ == '__main__':

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
    code = stress.main(['--root', '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/rc6-lifecycle-drain-focals-v0nrb2mk/cli311/pytest-tmp/test_real_finalizer_sigterm_is0/data', '--out', '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/rc6-lifecycle-drain-focals-v0nrb2mk/cli311/pytest-tmp/test_real_finalizer_sigterm_is0/result.json', '--catalog-count', '20', '--observations-per-identity', '5', '--canonical-runtime'])
    child = owned[0]
    Path('/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/rc6-lifecycle-drain-focals-v0nrb2mk/cli311/pytest-tmp/test_real_finalizer_sigterm_is0/observed.json').write_text(json.dumps({
        'child_pid': child.pid, 'veto_witness': veto_witness,
        'child_alive_after_natural_drain': child.is_alive(), 'child_exitcode': child.exitcode,
        'infrastructure_after_failed_cli': runner.child_infrastructure_snapshot(),
        'native_cli_code': code, 'real_finalizer_no_longer_active': not callbacks[0].still_active()}))
    # The original child ends naturally; there is no forced termination or
    # os._exit shortcut. The normal Python exit joins it before kernel reap.
    raise SystemExit(code)
