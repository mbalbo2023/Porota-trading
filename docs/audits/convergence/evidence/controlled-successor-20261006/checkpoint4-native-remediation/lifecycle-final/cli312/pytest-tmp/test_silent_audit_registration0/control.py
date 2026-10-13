import sys
sys.path.insert(0, '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source' )
if __name__ == '__main__':

    import sys
    from scripts import rc6_issue465_stress as stress
    def veto_registration(event, arguments):
        if event == 'sys.addaudithook':
            raise RuntimeError('CONTROL_REGISTRATION_VETO')
    sys.addaudithook(veto_registration)
    try:
        stress.main(['--root', '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/rc6-lifecycle-drain-focals-v0nrb2mk/cli312/pytest-tmp/test_silent_audit_registration0/data', '--out', '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/rc6-lifecycle-drain-focals-v0nrb2mk/cli312/pytest-tmp/test_silent_audit_registration0/result.json', '--catalog-count', '20', '--observations-per-identity', '5', '--canonical-runtime'])
    except ValueError as error:
        assert str(error) == 'GOVERNED_INET_OPERATION_AUDIT_NOT_INSTALLED'
        raise SystemExit(1)
    raise AssertionError('SILENT_AUDIT_REGISTRATION_VETO_REPORTED_PASS')
