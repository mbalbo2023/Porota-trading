"""Exact-source stdlib launcher. Execute only inside Root's assigned slot.

Focal selects five pre-fixture guards. Complete retains all 95 discovered cases.
Neither phase runs Chromium or claims canonical BIG browser acceptance.
"""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

RAW = Path('/tmp/rc6-ux-prepared-small-34294523-raw')
SOURCE = Path('/workspace/rc6-ux-prepared-small-34294523-whole')
SHA = '34294523f057abfb308db12c57a74bf0c0dd5eb8'
TREE = '088f804a6aa72d3db1a11246ba185a7140207bb4'
GIT_DIR = '/workspace/porota_rc6_convergence/.git/worktrees/ux-browser-coverage'
EXPECTED_METADATA_SHA = '754416932c5894eda247dffbc31ef666bc9b703d25ffab7fe824cffadfcdd5ac'
PRODUCT_PYTHONS = {'3.11': '/workspace/venv_rc6_frozen311/bin/python',
                   '3.12': '/workspace/venv_rc6_py312/bin/python'}
MODULES = ['tests/test_rc6_browser_product_ipc.py',
           'tests/test_rc6_projection_large_browser.py',
           'tests/test_rc6_projection_browser_diagnostic.py',
           'tests/test_rc6_browser_family_health_coverage.py',
           'tests/test_rc6_browser_prepared.py']
FOCAL_NODES = [
    'tests/test_rc6_browser_prepared.py::test_multifamily_rpc_cannot_generate_fixture_before_its_twenty_second_request',
    'tests/test_rc6_browser_prepared.py::test_native_preparation_cli_rejects_output_before_fixture_or_source_write',
    'tests/test_rc6_browser_prepared.py::test_native_preparation_wrong_interpreter_rejects_before_any_fixture_output',
]


def inventory():
    result = {}
    for path in SOURCE.rglob('*'):
        assert not path.is_symlink()
        if not path.is_file():
            continue
        descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            payload = stream.read()
        result[str(path.relative_to(SOURCE))] = {
            'sha256': hashlib.sha256(payload).hexdigest(),
            'mode': stat.S_IMODE(info.st_mode),
            'blob': hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest(),
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('focal', 'complete'), required=True)
    arguments = parser.parse_args()
    phase = arguments.phase
    version = '.'.join(map(str, sys.version_info[:2]))
    expected_python = PRODUCT_PYTHONS[version]
    suffix = version.replace('.', '')
    assert os.path.abspath(sys.executable) == expected_python
    assert Path(sys.prefix).resolve() == Path(expected_python).parent.parent.resolve()
    installed = {}
    for distribution in metadata.distributions():
        name = re.sub(r'[-_.]+', '-', distribution.metadata['Name']).lower()
        assert name not in installed
        installed[name] = distribution.version
    expected = {}
    for filename in ('requirements.lock.txt', 'requirements.build.lock.txt'):
        for line in (SOURCE / filename).read_text().splitlines():
            match = re.match(r'^([A-Za-z0-9_.-]+)==([^\\\s]+)', line)
            if match:
                name, value = match.groups()
                name = re.sub(r'[-_.]+', '-', name).lower()
                assert name not in expected or expected[name] == value
                expected[name] = value
    metadata_sha = hashlib.sha256(json.dumps(installed, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert len(expected) == 157 and installed == expected
    assert metadata_sha == EXPECTED_METADATA_SHA
    index = json.loads((RAW / 'source.index.json').read_text())
    assert index['schema'] == 'rc6.complete-archive-source-pin.v1'
    assert index['source_sha'] == SHA and index['source_tree'] == TREE and index['overlay_count'] == 0
    commit = (RAW / 'source.commit.raw').read_bytes()
    assert hashlib.sha256(commit).hexdigest() == index['raw_git_commit_sha256']
    assert hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest() == SHA
    assert commit.split(b'\n', 1)[0] == b'tree ' + TREE.encode()
    assert hashlib.sha256((RAW / 'source.tar').read_bytes()).hexdigest() == index['tar_sha256']
    before = inventory()
    assert {key: value['sha256'] for key, value in before.items()} == index['files']
    assert {key: '100755' if value['mode'] & 0o111 else '100644'
            for key, value in before.items()} == index['modes']
    assert {key: value['blob'] for key, value in before.items()} == index['blob_ids']
    environment = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1',
                       GIT_DIR=GIT_DIR, GIT_WORK_TREE=str(SOURCE))
    environment.pop('PYTHONPATH', None)
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, env=environment, text=True).strip() == SHA
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=SOURCE, env=environment, text=True).strip() == TREE
    stem = f'prepared-small-{phase}-{suffix}'
    base = RAW / f'pytest-{phase}-{suffix}'
    assert not base.exists()
    expected_count = 5 if phase == 'focal' else 95
    selection = FOCAL_NODES if phase == 'focal' else MODULES
    command = [sys.executable, '-B', '-m', 'pytest', '-vv', '-p', 'no:cacheprovider', *selection,
               '--basetemp', str(base), '--junitxml', str(RAW / f'{stem}.xml')]
    receipt = {
        'schema': 'rc6.ux-prepared-small-functional-execution.v1', 'phase': phase,
        'source_sha': SHA, 'source_tree': TREE, 'whole_source': str(SOURCE),
        'index': str(RAW / 'source.index.json'), 'overlay_count': 0,
        'sys_executable': sys.executable, 'python': sys.version, 'installed_count': len(installed),
        'installed': installed, 'metadata_sha256': metadata_sha,
        'native_scope': ('PRE_FIXTURE_PROTOCOL_AND_OUTPUT_PREFLIGHT_ONLY_NO_FINANCIAL_FIXTURE'
                         if phase == 'focal' else 'ACTUAL_CANONICAL_SMALL_FAMILY_AND_REAL_PRIVATE_LIVE_HEALTH_PLUS_IPC'),
        'expected_cases': expected_count, 'complete_expected_cases': 95,
        'fixture_dependent_new_controls_deferred': 18 if phase == 'focal' else 0,
        'original_65_pass_7_error_source': '6d2a9f144da872b9066cc1ffb531386d5dba56da',
        'original_receipts_untouched': True, 'git_dir_readonly_control': GIT_DIR,
        'git_work_tree': str(SOURCE), 'command': command,
        'started_at_epoch': time.time(), 'launcher_pid': os.getpid(), 'source_before': before,
        'acceptance_complete': False, 'fresh_big_browser_acceptance': 'PENDING',
        'chrome_executed': False,
    }
    begin = time.perf_counter()
    process = None
    interrupted = False
    with (RAW / f'{stem}.stdout').open('xb') as stdout, (RAW / f'{stem}.stderr').open('xb') as stderr:
        try:
            process = subprocess.Popen(command, cwd=SOURCE, env=environment,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            receipt['pytest_pid'] = process.pid
            (RAW / f'{stem}-started.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
            print(json.dumps({'event': 'START', 'phase': phase, 'pytest_pid': process.pid,
                              'launcher_pid': os.getpid(), 'source_sha': SHA, 'source_tree': TREE,
                              'executable': sys.executable, 'installed_count': len(installed),
                              'expected_cases': expected_count, 'command': command}), flush=True)
            code = process.wait()
        except BaseException:
            interrupted = True
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
            code = process.returncode if process is not None else 98
        finally:
            after = inventory()
            receipt.update(returncode=code, elapsed_seconds=time.perf_counter() - begin,
                           finished_at_epoch=time.time(), source_after=after,
                           source_hashes_modes_blobs_unchanged=before == after,
                           tracked_source_files=len(before), interrupted=interrupted,
                           pytest_reaped=process is not None and process.poll() is not None)
            for extension in ('stdout', 'stderr', 'xml'):
                path = RAW / f'{stem}.{extension}'
                if path.exists():
                    receipt[extension + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            xml = RAW / f'{stem}.xml'
            if xml.exists():
                document = ET.parse(xml).getroot()
                cases = document.findall('.//testcase')
                errors = sum(case.find('error') is not None for case in cases)
                failures = sum(case.find('failure') is not None for case in cases)
                skipped = sum(case.find('skipped') is not None for case in cases)
                receipt['junit'] = {'tests': len(cases), 'passed': len(cases) - errors - failures - skipped,
                                    'errors': errors, 'failures': failures, 'skipped': skipped}
            receipt['functional_scope_pass'] = (code == 0 and before == after and not interrupted
                and receipt.get('junit') == {'tests': expected_count, 'passed': expected_count,
                                            'errors': 0, 'failures': 0, 'skipped': 0})
            path = RAW / f'{stem}-receipt.json'
            path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
            print(json.dumps({'event': 'END', 'phase': phase,
                              'pytest_pid': process.pid if process is not None else None,
                              'returncode': code, 'functional_scope_pass': receipt['functional_scope_pass'],
                              'source_hashes_modes_blobs_unchanged': before == after,
                              'elapsed_seconds': receipt['elapsed_seconds'],
                              'junit': receipt.get('junit'), 'receipt': str(path)}), flush=True)
    return 0 if receipt['functional_scope_pass'] else (code if code else 97)


if __name__ == '__main__':
    raise SystemExit(main())
