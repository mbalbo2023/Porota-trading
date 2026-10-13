import sys
sys.path.insert(0, '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source' )
if __name__ == '__main__':

    import socket
    from scripts import rc6_issue465_stress as stress
    def forbidden_bind(*arguments, **options):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as descriptor:
            descriptor.bind(('127.0.0.1', 0))
        raise AssertionError('NATIVE_BIND_UNEXPECTEDLY_ALLOWED')
    stress.run_stress = forbidden_bind
    try:
        stress.main(['--root', '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/rc6-lifecycle-drain-focals-v0nrb2mk/cli311/pytest-tmp/test_real_inet_bind_is_denied_0/data', '--out', '/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/rc6-lifecycle-drain-focals-v0nrb2mk/cli311/pytest-tmp/test_real_inet_bind_is_denied_0/result.json', '--catalog-count', '20', '--observations-per-identity', '5', '--canonical-runtime'])
    except RuntimeError as error:
        assert str(error) == 'GOVERNED_INET_SOCKET_OPERATION_FORBIDDEN'
        raise SystemExit(1)
    raise AssertionError('DENIED_NATIVE_BIND_REPORTED_PASS')
