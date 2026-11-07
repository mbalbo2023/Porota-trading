"""A786: execute the A205 remote sudo-FD probe OFFHOST with a fake sudo.

The fake sudo forks/execs the actual Python fstat probe; no host, Docker,
network or privileged operation is used by this test.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '.github/workflows/rc6-a205-paper-book-readonly.yml'
BEGIN = '# RC6_A786_BEGIN_SUDO_FD_PROBE'
END = '# RC6_A786_END_SUDO_FD_PROBE'


def probe_script() -> str:
    source = SOURCE.read_text(encoding='utf-8')
    pattern = rf'(?m)^([ ]+){re.escape(BEGIN)}\n(.*?)^\1{re.escape(END)}$'
    match = re.search(pattern, source, re.S | re.M)
    if match is None:
        raise AssertionError('A205_SUDO_FD_PROBE_MISSING')
    script = textwrap.dedent(match.group(2))
    if re.search(r'\b(?:docker|systemctl|rm|unlink|rmtree|mkdir|tee)\b', script):
        raise AssertionError('A205_SUDO_FD_PROBE_NOT_READ_ONLY')
    return script


FAKE_SUDO = '''#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
if len(args)<8 or args[:2]!=['-n','-C'] or args[3]!='env':
    sys.exit(79)
close_from=int(args[2])
fd_field=next((a for a in args if a.startswith('RC6_A786_FD=')),None)
if fd_field is None:
    sys.exit(78)
fd=int(fd_field.split('=',1)[1])
if close_from!=fd+1 or not args[4].startswith('RC6_A786_FD='):
    sys.exit(77)
if os.environ.get('A786_SUDO_MODE')=='reject':
    sys.exit(73)
if os.environ.get('A786_SUDO_MODE')=='drop':
    os.close(fd)
os.execvp(args[3],args[3:])
'''


class SudoFdPreadmission(unittest.TestCase):
    def run_probe(self, mode: str) -> subprocess.CompletedProcess[str]:
        script = probe_script()
        with tempfile.TemporaryDirectory() as folder:
            bin_dir = Path(folder)
            fake = bin_dir / 'sudo'
            fake.write_text(FAKE_SUDO, encoding='utf-8')
            fake.chmod(0o700)
            env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                       A786_SUDO_MODE=mode)
            return subprocess.run(['bash', '-c', script], text=True,
                                  capture_output=True, timeout=10, env=env)

    def test_owner_fd_survives_closefrom_capability_probe(self):
        result = self.run_probe('keep')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         'RC6_A786_SUDO_FD=GREEN|host_written=NO')

    def test_dropped_descriptor_cannot_report_false_green(self):
        result = self.run_probe('drop')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('RC6_A786_SUDO_FD=GREEN', result.stdout)

    def test_sudoers_closefrom_denial_cannot_report_false_green(self):
        result = self.run_probe('reject')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('RC6_A786_SUDO_FD=GREEN', result.stdout)


if __name__ == '__main__':
    unittest.main()
