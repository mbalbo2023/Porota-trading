import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path('/workspace/porota_rc6_convergence')
OUT = Path('/tmp/rc6_root_source512_iterative_prebuild_b26f4d93')
OUT.mkdir(mode=0o700, exist_ok=False)

def git(*args):
    return subprocess.check_output(['git', '--no-replace-objects', '-C', str(ROOT), *args]).decode().strip()

def snapshot():
    result = {}
    for row in subprocess.check_output(['git', '--no-replace-objects', '-C', str(ROOT), 'ls-files', '--stage', '-z']).split(b'\0'):
        if not row:
            continue
        meta, name = row.split(b'\t', 1)
        mode, blob, stage = meta.decode().split()
        assert stage == '0'
        path = ROOT / os.fsdecode(name)
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
        try:
            digest = hashlib.sha256()
            while block := os.read(fd, 1024 * 1024):
                digest.update(block)
        finally:
            os.close(fd)
        result[os.fsdecode(name)] = {'sha256': digest.hexdigest(), 'git_mode': mode, 'physical_mode': oct(stat.S_IMODE(info.st_mode)), 'bytes': info.st_size}
    return result

assert not git('status', '--porcelain')
sha, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
before = snapshot()
nodes = [
    'tests/test_rc6_archive_v3_image_smoke.py',
    'tests/test_rc6_convergence_sre_archive.py',
    'tests/test_rc6_convergence_sre_binding.py',
    'tests/test_rc6_convergence_sre_published_evidence.py',
    'tests/test_rc6_disk_deploy_guard.py',
    'tests/test_rc6_disk_housekeeping_policy.py',
    'tests/test_rc6_convergence_provenance.py::test_final_prebuild_binds_one_governed_capture_and_original_exclusion_authority',
    'tests/test_rc6_convergence_provenance.py::test_final_prebuild_rejects_changed_raw_receipts_and_typed_metadata',
    'tests/test_rc6_convergence_provenance.py::test_predeploy_inventory_fetches_before_governed_tests_and_the_only_build',
    'tests/test_issue465_stress.py::test_canonical_factory_stress_uses_private_native_roots_and_matching_fingerprint',
]
junit = OUT / 'native.xml'
argv = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *nodes, '--junitxml=' + str(junit)]
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
started = time.monotonic()
with (OUT / 'native.log').open('wb') as log:
    completed = subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
after = snapshot()
data = junit.read_bytes() if junit.exists() else b''
cases = ET.fromstring(data).findall('.//testcase') if data else []
names = [case.get('classname', '') + '::' + case.get('name', '') for case in cases]
counts = {'executed': len(cases), 'failures': sum(case.find('failure') is not None for case in cases), 'errors': sum(case.find('error') is not None for case in cases), 'skipped': sum(case.find('skipped') is not None for case in cases), 'duplicate_nodes': len(names) - len(set(names))}
receipt = {'schema': 'rc6.root-integrated-native-focal.v1', 'scope': 'SOURCE_ONLY_FOCAL_NOT_FULL_GOVERNED_NOT_IMAGE_NOT_HORIZON', 'candidate_sha': sha, 'candidate_tree': tree, 'command_argv': argv, 'python': platform.python_version(), 'installed_distribution_count': len(list(importlib.metadata.distributions())), 'elapsed_wall_seconds': time.monotonic() - started, 'pytest_exit_code': completed.returncode, 'junit_path': str(junit), 'junit_sha256': hashlib.sha256(data).hexdigest(), 'junit_bytes': len(data), 'counts': counts, 'tracked_files': len(before), 'source_unchanged': before == after and sha == git('rev-parse', 'HEAD') and tree == git('rev-parse', 'HEAD^{tree}') and not git('status', '--porcelain'), 'changed_paths': sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p)), 'acceptance_complete': False, 'eligible_to_deploy': False}
(OUT / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
print(json.dumps(receipt, sort_keys=True), flush=True)
sys.exit(completed.returncode or int(not receipt['source_unchanged']))
