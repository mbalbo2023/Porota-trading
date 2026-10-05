from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import socket
import sqlite3
import stat
import sys
import time
import xml.etree.ElementTree as ET

RAW = Path('/tmp/rc6-packed-capture-0b380438-157-raw')
SOURCE = Path('/workspace/rc6-packed-capture-0b380438-157-source')
FIXTURES = Path('/workspace/rc6-packed-capture-0b380438-157-fixtures')
INTERPRETER = '/workspace/venv_rc6_frozen311/bin/python'
PIN = json.loads((RAW/'source.index.json').read_bytes())
normalized = lambda name: re.sub(r'[-_.]+', '-', name).lower()


def preflight():
    installed, required, locks = {}, {}, {}
    for distribution in importlib.metadata.distributions():
        name = normalized(distribution.metadata['Name'])
        if name in installed:
            raise ValueError('PREFLIGHT_DUPLICATE_DISTRIBUTION')
        installed[name] = distribution.version
    for name in ('requirements.lock.txt', 'requirements.build.lock.txt'):
        wire = (SOURCE/name).read_bytes()
        count = 0
        for line in wire.decode().splitlines():
            content = line.strip().removesuffix('\\').strip()
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', content)
            if match is None:
                if '==' in content and not content.startswith('#'):
                    raise ValueError('PREFLIGHT_UNSUPPORTED_LOCK_RECORD')
                continue
            package, version = normalized(match[1]), match[2]
            if package in required:
                raise ValueError('PREFLIGHT_DUPLICATE_LOCK_PIN')
            required[package] = version
            count += 1
        locks[name] = dict(sha256=hashlib.sha256(wire).hexdigest(), pins=count)
    result = dict(schema='rc6.native-frozen311-entry-preflight.v1',
        interpreter=sys.executable, required_interpreter=INTERPRETER,
        source_sha=PIN['source_sha'], source_tree=PIN['source_tree'],
        python=platform.python_version(), sqlite=sqlite3.sqlite_version,
        distribution_count=len(installed), before_fixture=not FIXTURES.exists(),
        distributions=[dict(name=key, version=value) for key,value in sorted(installed.items())],
        missing=sorted(set(required)-set(installed)), extra=sorted(set(installed)-set(required)),
        mismatches=[dict(name=key, required=value, installed=installed.get(key))
                    for key,value in required.items() if installed.get(key) != value], locks=locks,
        scope='INSTALLED_NAMES_AND_VERSIONS_ONLY_NOT_DISTRIBUTION_BYTE_PROOF')
    result['passed'] = (sys.executable == INTERPRETER and result['python'] == '3.11.16'
        and len(installed) == len(required) == 157 and installed == required and result['before_fixture']
        and locks['requirements.lock.txt']['pins'] == 154
        and locks['requirements.build.lock.txt']['pins'] == 3)
    (RAW/'entry-preflight.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    if not result['passed']:
        raise ValueError('PREFLIGHT_FROZEN311_INTERPRETER_OR_EXACT_LOCK_SET_REJECTED')
    return result


def inventory():
    result = {}
    for name in PIN['files']:
        path = SOURCE/name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('SOURCE_REGULAR_UNALIASED_FILE_REQUIRED')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
        try:
            pieces = []
            while raw := os.read(fd, 1024**2):
                pieces.append(raw)
            wire = b''.join(pieces)
            if os.fstat(fd) != info or path.lstat() != info:
                raise ValueError('SOURCE_CHANGED_DURING_WRAPPER_CAPTURE')
        finally:
            os.close(fd)
        mode = '100'+format(stat.S_IMODE(info.st_mode), '03o')
        blob = hashlib.sha1(b'blob '+str(len(wire)).encode()+b'\0'+wire).hexdigest()
        result[name] = dict(sha256=hashlib.sha256(wire).hexdigest(), git_mode=mode, git_blob=blob)
    if {str(path.relative_to(SOURCE)) for path in SOURCE.rglob('*') if path.is_file()} != set(result):
        raise ValueError('SOURCE_NAMESPACE_MISMATCH')
    if not all(row['sha256'] == PIN['files'][name] and row['git_mode'] == PIN['modes'][name]
               and row['git_blob'] == PIN['blob_ids'][name] for name,row in result.items()):
        raise ValueError('SOURCE_SHA_MODE_OR_GIT_BLOB_MISMATCH')
    return result


checks = preflight()
before = inventory()
if (PIN['source_sha'] != '0b3804388d1071959d16a0f382827aaf018b6429'
        or PIN['source_tree'] != '191ff6b66a69fad2234db6c50ff743c91fa45c6f'
        or type(PIN['overlay_count']) is not int or PIN['overlay_count'] != 0
        or hashlib.sha256((RAW/'source.tar').read_bytes()).hexdigest() != PIN['tar_sha256']):
    raise ValueError('SOURCE_ARCHIVE_PIN_MISMATCH')
network = []
def forbidden(*args, **kwargs):
    network.append('DENIED')
    raise AssertionError('OFFLINE_PACKED_CAPTURE_NETWORK_FORBIDDEN')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.socket.sendto = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
sys.path.insert(0, str(SOURCE))
os.umask(0o022)
import pytest
selection = ['tests/test_rc6_packed_capture_allocations.py', 'tests/test_rc6_packed_storage.py',
    'tests/test_rc6_funnel_storage_codec.py', 'tests/test_issue465_generations.py',
    'tests/test_rc6_shadow_runtime_wiring.py']
arguments = [*selection, '-q', '-o', 'addopts=', '-p', 'no:cacheprovider',
    '--basetemp='+str(FIXTURES), '--junitxml='+str(RAW/'native-focal.xml')]
result = dict(schema='rc6.packed-capture-allocation-whole-source-focal.v1',
    source_sha=PIN['source_sha'], source_tree=PIN['source_tree'], source_files=len(before),
    source_index_sha256=hashlib.sha256((RAW/'source.index.json').read_bytes()).hexdigest(),
    source_tar_sha256=PIN['tar_sha256'], overlay_count=0, interpreter=sys.executable,
    python=platform.python_version(), sqlite=sqlite3.sqlite_version,
    distribution_count=checks['distribution_count'], preflight_exact_names_versions=checks['passed'],
    argv=list(sys.argv), cwd=str(Path.cwd()), pytest_arguments=arguments, umask='022',
    scope='OWN_0b380438_NATIVE_FOCAL_NOT_FINAL_ARTIFACT_OR_BIG_ACCEPTANCE',
    acceptance_complete=False, artifact_validated=False, runtime_validated=False,
    environment={key:os.environ.get(key) for key in ('PYTHONDONTWRITEBYTECODE',
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONPATH', 'PYTHONHOME')})
(RAW/'wrapper-before.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
started = time.monotonic()
code = pytest.main(arguments)
after = inventory()
imports, alien = [], []
for name,module in sorted(sys.modules.items()):
    if not getattr(module, '__file__', None):
        continue
    path = Path(module.__file__).resolve()
    expected = SOURCE.joinpath(*name.split('.'))
    if path.is_relative_to(SOURCE) or expected.with_suffix('.py').is_file() or (expected/'__init__.py').is_file():
        row = dict(module=name, path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        imports.append(row)
        if not path.is_relative_to(SOURCE):
            alien.append(row)
cases = []
for case in ET.parse(RAW/'native-focal.xml').iter('testcase'):
    faults = [item for item in case if item.tag in ('failure', 'error', 'skipped')]
    cases.append(dict(classname=case.attrib['classname'], name=case.attrib['name'],
        seconds=case.attrib['time'], state=faults[0].tag.upper() if faults else 'PASS'))
result.update(returncode=int(code), elapsed_seconds=time.monotonic()-started,
    whole_source_sha_modes_git_blobs_unchanged=before == after, product_imports=imports,
    alien_product_imports=alien, network_attempts=len(network), native_cases=cases,
    junit_sha256=hashlib.sha256((RAW/'native-focal.xml').read_bytes()).hexdigest(),
    runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(RAW/'focal-receipt.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
print(json.dumps({key:value for key,value in result.items()
    if key not in ('native_cases', 'product_imports', 'pytest_arguments')}, sort_keys=True), flush=True)
raise SystemExit(int(code) or int(before != after or bool(alien) or bool(network)))
