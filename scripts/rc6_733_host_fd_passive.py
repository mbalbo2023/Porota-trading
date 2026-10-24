#!/usr/bin/env python3
"""Short-lived root-to-user-to-sudo FD probe; no canonical lock or service."""
import fcntl
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r'[0-9a-f]{40}', sys.argv[2]):
        raise ValueError('FD_PROBE_IDENTITY_INVALID')
    if os.geteuid() != 0:
        raise ValueError('FD_PROBE_REQUIRES_ROOT')
    user = pwd.getpwnam(sys.argv[1])
    if user.pw_uid == 0:
        raise ValueError('FD_PROBE_REMOTE_USER_ROOT')

    def drop():
        os.initgroups(user.pw_name, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)

    with tempfile.TemporaryFile(prefix='rc6-733-fd-probe-', dir='/run/lock') as handle:
        fd = handle.fileno()
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code = ('import os,sys; f=int(sys.argv[1]); '
                'print("FD_OPEN" if os.path.exists("/proc/self/fd/"+str(f)) else "FD_CLOSED")')
        report = {'schema': 'RC6_733_HOST_FD_PROBE_V1', 'utc': datetime.now(timezone.utc).isoformat(),
                  'product_head_expected': sys.argv[2], 'canonical_lock_touched': False,
                  'temporary_inode_link_count': os.fstat(fd).st_nlink,
                  'droplet_stress': False, 'ppi_watch_touched': False}
        for key, flags, expected in (
            ('baseline', [], 'SUDO_WORKS'),
            ('default_fd', [], None),
            ('closefrom_fd', ['-C', str(fd+1)], None),
        ):
            args = ['sudo','-n', *flags, 'python3','-c',
                    'print("SUDO_WORKS")' if expected else code]
            if expected is None:
                args.append(str(fd))
            cp = subprocess.run(args, pass_fds=(fd,), preexec_fn=drop,
                                capture_output=True, text=True, timeout=8, check=False)
            output = cp.stdout.strip()
            report[key] = {'rc': cp.returncode,
                           'result': output if output in ('SUDO_WORKS','FD_OPEN','FD_CLOSED')
                                     else 'NO_EXPECTED_OUTPUT',
                           'stderr_present': bool(cp.stderr)}
        report['status'] = ('DIAGNOSED' if report['baseline']['rc'] == 0 and
                            report['baseline']['result'] == 'SUDO_WORKS' else 'SUDO_BASELINE_FAILED')
    print('RC6_733_HOST_FD_PROBE=' + json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'DIAGNOSED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
