from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sqlite3
import stat
import subprocess
import sys
import time

SOURCE = Path('/workspace/rc6-whole-source-20261005-packed400')
RAW = Path('/tmp/rc6-canonical-400a677c-big-raw')
DATA = Path('/workspace/rc6-canonical-400a677c-big')
INTERPRETER = '/workspace/venv_rc6_frozen311/bin/python'
PIN_PATH = Path('/tmp/rc6-whole-source-20261005-packed400-raw/source.index.json')
PIN = json.loads(PIN_PATH.read_bytes())

def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()

def preflight():
    checks = {'schema': 'rc6.native-frozen311-entry-preflight.v1',
              'interpreter': sys.executable, 'required_interpreter': INTERPRETER,
              'source_sha': PIN['source_sha'], 'source_tree': PIN['source_tree'],
              'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
              'passed': False, 'before_fixture': not DATA.exists(),
              'scope': 'INSTALLED_NAMES_AND_VERSIONS_ONLY_NOT_DISTRIBUTION_BYTE_PROOF'}
    installed = []
    installed_map = {}
    for distribution in importlib.metadata.distributions():
        name = normalized(distribution.metadata['Name'])
        if name in installed_map:
            raise ValueError('PREFLIGHT_DUPLICATE_DISTRIBUTION')
        installed_map[name] = distribution.version
        installed.append({'name': name, 'version': distribution.version})
    checks['distributions'] = sorted(installed, key=lambda row: row['name'])
    checks['distribution_count'] = len(installed)
    required = {}
    checks['locks'] = {}
    for lock_name in ('requirements.lock.txt', 'requirements.build.lock.txt'):
        wire = (SOURCE / lock_name).read_bytes()
        count = 0
        for line in wire.decode().splitlines():
            content = line.strip().removesuffix('\\').strip()
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', content)
            if match is None:
                if '==' in content and not content.startswith('#'):
                    raise ValueError('PREFLIGHT_UNSUPPORTED_LOCK_RECORD')
                continue
            name, version = normalized(match[1]), match[2]
            if name in required:
                raise ValueError('PREFLIGHT_DUPLICATE_LOCK_PIN')
            required[name] = version
            count += 1
        checks['locks'][lock_name] = {'sha256': hashlib.sha256(wire).hexdigest(), 'pins': count}
    checks['missing'] = sorted(set(required) - set(installed_map))
    checks['extra'] = sorted(set(installed_map) - set(required))
    checks['mismatches'] = [dict(name=name, required=version, installed=installed_map.get(name))
                            for name, version in required.items() if installed_map.get(name) != version]
    checks['passed'] = (sys.executable == INTERPRETER and platform.python_version() == '3.11.16'
                        and len(installed) == len(required) == 157 and installed_map == required
                        and checks['locks']['requirements.lock.txt']['pins'] == 154
                        and checks['locks']['requirements.build.lock.txt']['pins'] == 3
                        and checks['before_fixture'])
    (RAW / 'entry-preflight.json').write_text(json.dumps(checks, indent=2, sort_keys=True) + '\n')
    if not checks['passed']:
        raise ValueError('PREFLIGHT_FROZEN311_INTERPRETER_OR_EXACT_LOCK_SET_REJECTED')
    return checks

def inventory():
    result = {}
    for name in PIN['files']:
        path = SOURCE / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('SOURCE_REGULAR_UNALIASED_FILE_REQUIRED')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
        try:
            parts = []
            while block := os.read(descriptor, 1024 * 1024):
                parts.append(block)
            wire = b''.join(parts)
            if os.fstat(descriptor) != info or path.lstat() != info:
                raise ValueError('SOURCE_CHANGED_DURING_WRAPPER_CAPTURE')
        finally:
            os.close(descriptor)
        result[name] = {'sha256': hashlib.sha256(wire).hexdigest(),
                        'git_mode': '100' + format(stat.S_IMODE(info.st_mode), '03o'),
                        'git_blob': hashlib.sha1(b'blob ' + str(len(wire)).encode() + b'\0' + wire).hexdigest()}
    if {str(path.relative_to(SOURCE)) for path in SOURCE.rglob('*') if path.is_file()} != set(result):
        raise ValueError('SOURCE_NAMESPACE_MISMATCH')
    if not all(row['sha256'] == PIN['files'][name] and row['git_mode'] == PIN['modes'][name]
               and row['git_blob'] == PIN['blob_ids'][name] for name, row in result.items()):
        raise ValueError('SOURCE_SHA_MODE_OR_GIT_BLOB_MISMATCH')
    return result

checks = preflight()
before = inventory()
if PIN['source_sha'] != '400a677c7a2d94e52fcc7e1c598f94a66862253c' or PIN['source_tree'] != '4694f053baf294c324e4cea7f66500b22b4c3004' or type(PIN['overlay_count']) is not int or PIN['overlay_count'] != 0:
    raise ValueError('SOURCE_PIN_MISMATCH')
if hashlib.sha256(Path('/tmp/rc6-whole-source-20261005-packed400-raw/source.tar').read_bytes()).hexdigest() != PIN['tar_sha256']:
    raise ValueError('SOURCE_ARCHIVE_HASH_MISMATCH')
os.umask(0o077)
DATA.mkdir(mode=0o700, exist_ok=False)
command = [INTERPRETER, '-B', '-u', str(SOURCE / 'scripts/rc6_issue465_stress.py'),
           '--root', str(DATA), '--out', str(RAW / 'native-big-result.json'),
           '--catalog-count', '12000', '--observations-per-identity', '5',
           '--slow-disk', '--canonical-runtime']
environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONPATH': ''}
environment.pop('PYTHONHOME', None)
metadata = {'schema': 'rc6.native-big-whole-source-trial-wrapper.v2',
            'source_sha': PIN['source_sha'], 'source_tree': PIN['source_tree'],
            'source_index_sha256': hashlib.sha256(PIN_PATH.read_bytes()).hexdigest(),
            'tar_sha256': PIN['tar_sha256'], 'overlay_count': 0, 'source_files': len(before),
            'command': command, 'cwd': str(SOURCE), 'umask': '077', 'uid': os.geteuid(), 'gid': os.getegid(),
            'data_mode': format(stat.S_IMODE(DATA.stat().st_mode), '04o'),
            'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version, 'platform': platform.platform(),
            'interpreter': sys.executable, 'entry_preflight_passed': checks['passed'],
            'entry_preflight_sha256': hashlib.sha256((RAW / 'entry-preflight.json').read_bytes()).hexdigest(),
            'distributions': checks['distributions'], 'distribution_count': len(checks['distributions']),
            'scope': 'SOURCE_ONLY_UNINSTRUMENTED_CANONICAL_BIG_NATIVE157_NOT_GOV_ARTIFACT_RUNTIME_VALIDATION',
            'acceptance_complete': False, 'artifact_validated': False, 'runtime_validated': False,
            'diagnostic_only': False, 'code_custody_scope': 'ALL_GIT_FILE_BYTES_MODES_AND_BLOB_IDS',
            'environment_controls': {key: environment.get(key) for key in
                                     ('PYTHONDONTWRITEBYTECODE', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONPATH', 'PYTHONHOME')}}
(RAW / 'wrapper-before.json').write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
start = time.monotonic()
with (RAW / 'big.log').open('wb') as log:
    process = subprocess.run(command, cwd=SOURCE, env=environment, stdout=log, stderr=subprocess.STDOUT)
try:
    after = inventory()
    metadata['whole_source_files_and_modes_unchanged'] = before == after
except BaseException as error:
    metadata['whole_source_files_and_modes_unchanged'] = False
    metadata['source_error'] = {'class': type(error).__name__, 'reason': str(error)}
metadata.update(returncode=process.returncode, wrapper_elapsed_wall_seconds=time.monotonic() - start,
                rawlog_sha256=hashlib.sha256((RAW / 'big.log').read_bytes()).hexdigest())
for name, field in (('native-big-result.json', 'native_result_sha256'), ('child-stacks.log', 'diagnostic_stacks_sha256')):
    path = RAW / name
    if path.exists():
        metadata[field] = hashlib.sha256(path.read_bytes()).hexdigest()
(RAW / 'wrapper-final.json').write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
print(json.dumps({key: metadata[key] for key in
                 ('source_sha', 'source_files', 'returncode', 'whole_source_files_and_modes_unchanged',
                  'wrapper_elapsed_wall_seconds', 'distribution_count', 'diagnostic_only')}), flush=True)
raise SystemExit(process.returncode or int(not metadata['whole_source_files_and_modes_unchanged']))
