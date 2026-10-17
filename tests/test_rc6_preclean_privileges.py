"""The SSH user must clear a previous owner's receipt through its privilege boundary."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import time

from scripts import rc6_deploy_quiescence as quiescence

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/porota-deploy-v2-promote.yml'


def test_canonical_preclean_clears_retained_receipt_as_unprivileged_user(record_testsuite_property):
    # The governed Ubuntu runner MUST exercise different real UIDs. A local
    # capability-restricted sandbox uses an explicitly labelled permission
    # model; that result is never reported as the native OS proof.
    with tempfile.TemporaryDirectory(prefix='rc6-preclean-sticky-') as folder:
        root = Path(folder); root.chmod(0o1777)
        receipt = root / 'retained-root-receipt.json'
        receipt.write_text('OLD_RECEIPT'); receipt.chmod(0o644)
        bin_dir = root / 'bin'; bin_dir.mkdir(mode=0o755)
        request, ack = root / 'request', root / 'ack'
        stop = threading.Event(); calls = []; failures = []
        def owner():
            while not stop.is_set():
                if request.exists() and request.read_text() == 'CLEAR_EXACT_FIXTURE':
                    try:
                        receipt.unlink(); calls.append('EXACT_FIXTURE_REMOVED')
                        pending = root / 'ack.tmp'; pending.write_text('OK'); pending.replace(ack)
                    except OSError as exc: failures.append(type(exc).__name__)
                    break
                stop.wait(.01)
        thread = threading.Thread(target=owner); thread.start()
        sudo = bin_dir / 'sudo'
        sudo.write_text('#!/usr/bin/python3\nfrom pathlib import Path\nimport sys,time\n'
            + 'assert sys.argv[1:] == ' + repr(['-n','rm','-f',str(receipt)]) + '\n'
            + 'request=Path(' + repr(str(request)) + ');ack=Path(' + repr(str(ack)) + ')\n'
            + "request.write_text('CLEAR_EXACT_FIXTURE');deadline=time.monotonic()+5\n"
            + 'while not ack.exists() and time.monotonic()<deadline:time.sleep(.01)\n'
            + "assert ack.read_text()=='OK'\n")
        sudo.chmod(0o755)
        target = '/tmp/porota-predeploy-housekeeping.json'
        line = next(x.strip() for x in WORKFLOW.read_text().splitlines()
                    if x.strip().endswith('rm -f ' + target))
        command = line.replace(target, shlex.quote(str(receipt)))
        env = {'PATH': str(bin_dir) + ':/usr/bin:/bin'}
        capabilities = next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                            if line.startswith('CapEff:'))
        root_drop = os.geteuid() == 0 and bool(int(capabilities,16) & (1 << 7))
        privilege = shutil.which('sudo') if os.geteuid() != 0 else None
        can_sudo = bool(privilege and subprocess.run([privilege,'-n','true'],
            capture_output=True, timeout=5).returncode == 0)
        mode = 'OS_DIFFERENT_UID' if root_drop or can_sudo else 'LOCAL_PERMISSION_MODEL'
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            assert mode == 'OS_DIFFERENT_UID', 'NATIVE_PRIVILEGE_PROOF_REQUIRED'
        record_testsuite_property('RC6_PRECLEAN_PRIVILEGE_BOUNDARY', mode)
        if mode == 'LOCAL_PERMISSION_MODEL':
            # No setuid/IPC permission is requested from the local sandbox.
            # The native gate is required to replace this model with OS proof.
            rm = bin_dir / 'rm'
            rm.write_text('#!/bin/bash\nexit 1\n'); rm.chmod(0o755)
        def launch(text):
            args = ['/bin/bash','-c',text]; identity = {}
            if root_drop:
                identity = {'user': 65534, 'group': 65534}
            elif can_sudo:
                args = [privilege,'-n','-u','nobody','--','/usr/bin/env','PATH='+env['PATH'],*args]
            return subprocess.run(args,env=env,cwd='/tmp',capture_output=True,text=True,timeout=10,**identity)
        try:
            negative = launch('rm -f ' + shlex.quote(str(receipt)))
            assert negative.returncode != 0 and receipt.exists()
            result = launch(command)
        finally:
            stop.set(); thread.join(timeout=2)
        assert result.returncode == 0, result.stderr
        assert not receipt.exists()
        assert calls == ['EXACT_FIXTURE_REMOVED'] and not failures


def test_pretransfer_failure_also_enters_measured_failure_cleanup():
    text = WORKFLOW.read_text()
    start = text.index('- name: Dynamic pre-transfer disk guard and conditional safe cleanup')
    end = text.index('- name: Transfer frozen artifact', start)
    assert 'id: diskguard' in text[start:end]
    start = text.index('- name: Failure-safe host cleanup')
    end = text.index('timeout-minutes:', start)
    assert "steps.diskguard.outcome == 'failure'" in text[start:end]
    assert "steps.diskguard.outcome == 'cancelled'" in text[start:end]


def test_cleanup_before_any_drain_cannot_restart_a_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(quiescence, 'ROOT', tmp_path)
    def forbidden(*args, **kwargs): raise AssertionError('NO_RUNTIME_MUTATION_PERMITTED')
    monkeypatch.setattr(quiescence, 'command', forbidden)
    result = quiescence.run('a'*40, 'recover')
    assert result == {'status': 'NO_DRAIN_RECEIPT'}
