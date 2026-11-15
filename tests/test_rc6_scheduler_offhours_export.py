"""A recent CLOSED scheduler snapshot avoids repeated host systemctl scans."""
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import shlex

import ak_byma_calendar as calendar
from co_market_sessions_hf6 import TZ, byma_paper_spot_phase


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/porota_export_scheduler_state_rc6.sh'


def test_scheduler_snapshot_closed_skip_and_corrupt_fail_open(tmp_path):
    # Only relocate the host lock; retain the real shell/flock/export logic.
    # Offhost tests must never need access to the host's /run/lock directory.
    lock = tmp_path / 'scheduler-export.lock'
    lock_assignment = 'LOCK="/run/lock/porota-scheduler-export-rc6.lock"'
    source = SCRIPT.read_text()
    assert source.count(lock_assignment) == 1
    script = tmp_path / SCRIPT.name
    script.write_text(source.replace(lock_assignment, 'LOCK=' + shlex.quote(str(lock))))
    directory = tmp_path / 'scheduler'
    directory.mkdir()
    output = directory / 'systemd_timers.json'
    output.write_text(json.dumps({'schema': 'porota-scheduler-systemd-v1',
        'state': 'OK', 'timers': [{'unit': f'porota-{n}.timer'} for n in range(20)]}))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'systemctl'
    fake.write_text('''#!/usr/bin/env bash
echo "$1" >> "$RC6_TEST_SYSTEMCTL_LOG"
if [ "$1" = list-unit-files ]; then
    for n in $(seq 1 20); do printf 'porota-%s.timer enabled\\n' "$n"; done
else
    printf 'ActiveState=active\\nUnitFileState=enabled\\nResult=success\\n'
fi
''')
    fake.chmod(0o755)
    log = tmp_path / 'systemctl.log'
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
           'POROTA_SCHEDULER_DIR': str(directory), 'RC6_TEST_SYSTEMCTL_LOG': str(log)}

    def invoke():
        result = subprocess.run(['bash', str(script)], cwd=SCRIPT.parents[1],
            env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr

    before = datetime.now(TZ)
    invoke()
    after = datetime.now(TZ)
    audited = (before.year in calendar.ANIOS_AUDITADOS and
               0 <= (before.date() - calendar.CALENDARIO_ACTUALIZADO_EL).days <= 60)
    if before.date() == after.date() and byma_paper_spot_phase(before) == byma_paper_spot_phase(after):
        closed = audited and byma_paper_spot_phase(before) == 'CLOSED'
        assert log.exists() is not closed

    # A recent but malformed artifact cannot justify skipping an export.
    output.write_text('{corrupt')
    if log.exists():
        log.unlink()
    # Contention must prevent scanning and replacing corrupt evidence.
    with lock.open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        invoke()
        assert not log.exists()
        assert output.read_text() == '{corrupt'
    invoke()
    assert 'list-unit-files' in log.read_text()
    assert json.loads(output.read_text())['state'] == 'OK'
