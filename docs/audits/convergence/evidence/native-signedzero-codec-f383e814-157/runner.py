"""Short offline public-codec replay; no financial or archive authority."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import traceback

parser = argparse.ArgumentParser()
parser.add_argument('--source-index', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--repo', type=Path, default=Path('/workspace/porota_rc6_convergence'))
arguments = parser.parse_args()
REPO = arguments.repo.resolve(strict=True)
SOURCE_INDEX = arguments.source_index.resolve(strict=True)
source_index = json.loads(SOURCE_INDEX.read_bytes())
ROOT = Path(source_index['extracted_root']).resolve(strict=True)
PIN, TREE = source_index['source_sha'], source_index['source_tree']
OUTPUT = arguments.output.absolute()
assert not OUTPUT.resolve().is_relative_to(ROOT), 'RECEIPT_MUST_BE_OUTSIDE_ARCHIVE'
assert not OUTPUT.exists(), 'NEW_RECEIPT_REQUIRED'
MODULES = (
    'rc6_shadow_runtime/__init__.py',
    'rc6_shadow_runtime/serialization.py',
    'rc6_shadow_runtime/packed_storage.py',
)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    with os.fdopen(fd, 'rb') as stream:
        return stream.read()

def git(*args):
    result = subprocess.run(['git', '-C', str(REPO), *args], capture_output=True,
                            text=True, check=True, timeout=2)
    return result.stdout.strip()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()

started = time.monotonic()
sys.dont_write_bytecode = True
assert git('rev-parse', PIN + '^{tree}') == TREE
source_index = json.loads(read(SOURCE_INDEX))
assert source_index['source_sha'] == PIN and source_index['source_tree'] == TREE
assert source_index['extracted_root'] == str(ROOT)
def source_inventory():
    return {str(path.relative_to(ROOT)): sha(read(path))
            for path in sorted(ROOT.rglob('*')) if path.is_file()}
def source_modes():
    result = {}
    for path in sorted(ROOT.rglob('*')):
        assert not path.is_symlink(), 'ARCHIVE_SYMLINK_FORBIDDEN'
        if path.is_file():
            info = path.lstat()
            assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            result[str(path.relative_to(ROOT))] = stat.S_IMODE(info.st_mode)
    return result
source_before = source_inventory()
modes_before = source_modes()
assert source_before == source_index['source_file_hashes']
assert modes_before == source_index['source_file_modes']
assert source_index['git_blob_and_modes_all_verified'] is True
assert source_index['overlays_applied'] == []
assert sha(read(Path(source_index['archive_path']))) == source_index['archive_sha256']
before = {path: sha(read(ROOT / path)) for path in MODULES}
expected_blobs = {path: git('rev-parse', PIN + ':' + path) for path in MODULES}
assert all(git('hash-object', '--no-filters', '--', str(ROOT / path)) == expected_blobs[path] for path in MODULES)
attempts = []
def blocked(*args, **kwargs):
    attempts.append('BLOCKED_NETWORK_ATTEMPT')
    raise AssertionError('NETWORK_FORBIDDEN')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.socket.sendto = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
"""Source fragment for a pre-product strict dependency closure check."""


def strict_157_preflight(root):
    from importlib import metadata
    import platform
    import re

    expected_executable = '/workspace/venv_rc6_frozen311/bin/python'
    expected_prefix = '/workspace/venv_rc6_frozen311'
    assert sys.executable == expected_executable, 'FROZEN311_EXECUTABLE_REQUIRED'
    assert sys.prefix == expected_prefix, 'FROZEN311_PREFIX_REQUIRED'
    assert sys.version_info[:2] == (3, 11), 'FROZEN311_MINOR_REQUIRED'
    assert sys.flags.dont_write_bytecode, 'EXPLICIT_PYTHON_B_REQUIRED'
    normalize = lambda name: re.sub(r'[-_.]+', '-', name.lower())
    paths = {
        'runtime_lock': root / 'requirements.lock.txt',
        'build_lock': root / 'requirements.build.lock.txt',
        'supply_chain_policy': root / 'ops/policy/rc6-supply-chain-v1.json',
    }
    raw = {name: read(path) for name, path in paths.items()}
    policy = json.loads(raw['supply_chain_policy'])
    assert policy['schema'] == 'rc6.hashed-distribution-lock.v1'

    def policy_group(rows, expected_count):
        result = {}
        for row in rows:
            name = normalize(row['name'])
            assert name not in result, 'DUPLICATE_POLICY_DISTRIBUTION'
            hashes = [item['sha256'] for item in row['distributions']]
            assert hashes and len(hashes) == len(set(hashes))
            assert all(re.fullmatch(r'[0-9a-f]{64}', value) for value in hashes)
            result[name] = {'version': row['version'], 'hashes': set(hashes)}
        assert len(result) == expected_count, 'EXPECTED_157_LOCK_CLOSURE_REQUIRED'
        return result

    runtime = policy_group(policy['packages'], 154)
    build = policy_group(policy['build_tools'], 3)
    assert not set(runtime) & set(build), 'LOCK_GROUP_OVERLAP'

    def locked_group(body, wanted):
        logical, pending = [], ''
        for original in body.decode().splitlines():
            line = original.strip()
            if not line or line.startswith('#'):
                continue
            pending += (' ' if pending else '') + line.removesuffix('\\').strip()
            if line.endswith('\\'):
                continue
            logical.append(pending)
            pending = ''
        assert not pending, 'INCOMPLETE_LOCK_CONTINUATION'
        observed = {}
        for line in logical:
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^,;\s]+)((?:\s+--hash=sha256:[0-9a-f]{64})+)', line)
            assert match is not None, 'EXACT_HASHED_LOCK_ROW_REQUIRED'
            name, version = normalize(match[1]), match[2]
            hashes = re.findall(r'--hash=sha256:([0-9a-f]{64})', match[3])
            assert name not in observed and len(hashes) == len(set(hashes))
            observed[name] = {'version': version, 'hashes': set(hashes)}
        assert observed == wanted, 'LOCK_AND_POLICY_MISMATCH'
        return len(observed)

    runtime_count = locked_group(raw['runtime_lock'], runtime)
    build_count = locked_group(raw['build_lock'], build)
    expected = {name: row['version'] for name, row in (runtime | build).items()}
    installed, duplicates, rows = {}, [], []
    for distribution in metadata.distributions():
        name, version = distribution.metadata['Name'], distribution.version
        assert isinstance(name, str) and name and isinstance(version, str)
        normalized = normalize(name)
        if normalized in installed:
            duplicates.append(normalized)
        installed[normalized] = version
        rows.append({'name': normalized, 'version': version})
    missing = sorted(set(expected) - set(installed))
    unexpected = sorted(set(installed) - set(expected))
    mismatches = sorted(name for name in set(expected) & set(installed)
                        if expected[name] != installed[name])
    assert len(rows) == len(installed) == len(expected) == 157
    assert not missing and not unexpected and not mismatches and not duplicates, 'INSTALLED_LOCK_CLOSURE_MISMATCH'
    current = {
        'os': sys.platform, 'architecture': platform.machine(),
        'python_minor': f'{sys.version_info.major}.{sys.version_info.minor}',
        'python_implementation': platform.python_implementation(),
    }
    libc_name, libc_version = platform.libc_ver()
    current.update(libc_name=libc_name, libc_version=libc_version)
    approved = policy['platform']
    assert current['os'] == approved['os'] and current['architecture'] == approved['architecture']
    assert current['python_minor'] in approved['python_minors'] and current['python_implementation'] == 'CPython'
    assert libc_name == 'glibc'
    assert tuple(map(int, libc_version.split('.'))) >= tuple(map(int, approved['glibc_minimum'].split('.')))
    return {
        'schema': 'rc6.finance-native-environment-preflight.v1', 'status': 'GREEN',
        'executable': sys.executable, 'prefix': sys.prefix, 'version': sys.version,
        'bytecode_disabled': bool(sys.flags.dont_write_bytecode), 'platform': current,
        'runtime_locked_count': runtime_count, 'build_locked_count': build_count,
        'expected_distribution_count': len(expected), 'installed_count': len(rows),
        'installed_unique_count': len(installed), 'duplicate_names': duplicates,
        'missing': missing, 'unexpected': unexpected, 'version_mismatch': mismatches,
        'locked_inputs_sha256': {name: sha(value) for name, value in raw.items()},
        'installed_distributions': sorted(rows, key=lambda value: value['name']),
        'product_imported_before_preflight': False, 'fixture_started_before_preflight': False,
        'scope': 'HASH_LOCK_INPUTS_AND_EXACT_INSTALLED_METADATA; NOT_INSTALLED_FILE_BYTES_OR_IMAGE_ATTESTATION',
    }

preflight = strict_157_preflight(ROOT)
preflight_path = OUTPUT.with_suffix('.entry-preflight.json')
assert not preflight_path.exists(), 'NEW_PREFLIGHT_RECEIPT_REQUIRED'
with preflight_path.open('x') as stream:
    stream.write(json.dumps(preflight, sort_keys=True, indent=2) + '\n')
preflight_path.chmod(0o600)
sys.path.insert(0, str(ROOT))
from rc6_shadow_runtime import packed_storage, serialization

limits = dict(durable_limit=4 * 1024**2, expansion_limit=8 * 1024**2)
cases = []
for variant in ('negative_then_positive', 'positive_then_negative', 'bool_int_positive_control'):
    signs = [-0.0, 0.0] if variant == 'negative_then_positive' else [0.0, -0.0]
    if variant == 'bool_int_positive_control':
        rows = [{'typed': False}, {'typed': 0}, {'typed': False}, {'typed': 0}]
        nested = [[False, 0], [0, False]]
    else:
        rows = [{'delta': signs[0], 'typed': [False, 0]},
                {'delta': signs[1], 'typed': [False, 0]}] * 2
        nested = [[signs[0], False, 0], [signs[1], False, 0]]
    body = {'rows': rows, 'nested': nested, 'unicode': 'Córdoba / Ñ / Ω / \u0000',
            'observed_at': '2026-10-05T13:35:00.000003+00:00',
            'effective_at': '2026-10-05T13:35:00.000001+00:00',
            'known_at': '2026-10-05T13:35:00.000002+00:00',
            'padding': 'x' * 270000}
    expected = canonical(body)
    assert len(expected) > serialization.THRESHOLD
    for codec, encoder in (('V1', serialization.encode_storage),
                           ('V2', packed_storage.encode_packed_storage)):
        wire = encoder(body, **limits)
        wanted_schema = serialization.SCHEMA if codec == 'V1' else serialization.PACKED_SCHEMA
        assert wire['schema'] == wanted_schema
        proof = serialization.verify_storage_wire(wire, **limits)
        assert proof['payload_digest'] == sha(expected)
        row = {'variant': variant, 'codec': codec, 'storage_schema': wire['schema'],
               'logical_bytes': len(expected), 'logical_sha256': sha(expected),
               'wire_sha256': sha(serialization.canonical_bytes(wire)),
               'canonical_rows_lexemes': canonical(rows).decode(),
               'canonical_nested_lexemes': canonical(nested).decode(),
               'wire_proof': proof, 'decodes': []}
        for share in (False, True):
            decoded = {'share_subtrees': share}
            try:
                restored = serialization.decode_storage(wire, **limits, share_subtrees=share)
                actual = canonical(restored)
                decoded.update(status='EXACT_ORIGINAL_CANONICAL_BYTES',
                               actual_sha256=sha(actual), bytes_equal=actual == expected,
                               canonical_rows_lexemes=canonical(restored['rows']).decode(),
                               canonical_nested_lexemes=canonical(restored['nested']).decode())
                assert actual == expected
            except ValueError as error:
                decoded.update(status='VALID_WIRE_FULL_READER_REJECTED',
                               exception_type=type(error).__name__, reason=str(error),
                               traceback=traceback.format_exc())
            row['decodes'].append(decoded)
        row['expected_green_replay'] = all(
            result['status'] == 'EXACT_ORIGINAL_CANONICAL_BYTES' and result['bytes_equal']
            for result in row['decodes'])
        cases.append(row)

origins = []
repository_module_names = {name[:-3].replace('/', '.').removesuffix('.__init__')
                           for name in source_before if name.endswith('.py')}
all_file_import_origins = {}
for name, module in sorted(sys.modules.items()):
    origin = getattr(module, '__file__', None)
    if origin:
        all_file_import_origins[name] = str(Path(origin).resolve())
    is_product = (name in repository_module_names or name == 'rc6_shadow_runtime'
                  or name.startswith('rc6_shadow_runtime.')
                  or origin is not None and str(Path(origin).resolve()).startswith('/workspace/porota_rc6_'))
    if origin and is_product:
        resolved = Path(origin).resolve()
        assert resolved.is_relative_to(ROOT)
        relative = str(resolved.relative_to(ROOT))
        imported_sha = sha(read(resolved))
        assert relative in source_before and imported_sha == source_before[relative]
        imported_blob = git('rev-parse', PIN + ':' + relative)
        assert git('hash-object', '--no-filters', '--', str(resolved)) == imported_blob
        origins.append({'module': name, 'path': str(resolved), 'sha256': imported_sha,
                        'git_blob': imported_blob})
after = {path: sha(read(ROOT / path)) for path in MODULES}
assert before == after
assert git('rev-parse', PIN + '^{tree}') == TREE
source_after = source_inventory()
modes_after = source_modes()
assert source_before == source_after == source_index['source_file_hashes']
assert modes_before == modes_after == source_index['source_file_modes']
assert not attempts
receipt = {'schema': 'rc6.finance-public-codec-signed-zero-replay.v1',
           'source_sha': PIN, 'source_tree': TREE,
           'source_file_sha256_before': before, 'source_file_sha256_after': after,
           'archive_sha256': source_index['archive_sha256'],
           'source_index_sha256': sha(read(SOURCE_INDEX)),
           'source_modes_before': modes_before, 'source_modes_after': modes_after,
           'source_modes_invariant': modes_before == modes_after,
           'whole_archive_git_blobs_verified_at_preparation': True,
           'overlays_applied': [],
           'interpreter': {'executable': sys.executable, 'version': sys.version},
           'environment_preflight': preflight,
           'environment_preflight_path': str(preflight_path),
           'environment_preflight_sha256': sha(read(preflight_path)),
           'extracted_root': str(ROOT), 'source_file_count': len(source_before),
           'all_archive_files_invariant': True,
           'source_blobs': expected_blobs, 'source_invariance': True,
           'imported_product_modules': origins, 'network_attempts': attempts,
           'all_imported_file_origins': all_file_import_origins,
           'alien_product_imports': [],
           'script_sha256': sha(read(Path(__file__))),
           'case_count': len(cases), 'cases': cases,
           'scope': 'PUBLIC_CODECS_ONLY_NO_TRADING_SOURCE_IO_NO_FINANCIAL_OR_HORIZON_CLAIM',
           'classification': 'EXACT_ARCHIVE_PUBLIC_CODEC_GREEN_REPLAY' if all(row['expected_green_replay'] for row in cases) else 'EXACT_ARCHIVE_PUBLIC_CODEC_GREEN_REPLAY_FAILED',
           'expected_original_canonical_equality': True,
           'final_material_governance_or_image_verified': False,
           'status': 'GREEN' if all(row['expected_green_replay'] for row in cases) else 'RED',
           'original_red_receipt_sha256': '46ce3366f8316ad452f515a30b100a8c19210284fb0df66dfa70bf605573680b',
           'original_red_source_sha': 'dc59c2fb125f1ccefa167333370e350247249030',
           'elapsed_seconds': time.monotonic() - started}
OUTPUT.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
os.chmod(OUTPUT, 0o600)
print(json.dumps({'receipt': str(OUTPUT), 'receipt_sha256': sha(read(OUTPUT)),
                  'cases': len(cases), 'elapsed_seconds': receipt['elapsed_seconds'],
                  'status': receipt['status'],
                  'exact_decodes': sum(result['status'] == 'EXACT_ORIGINAL_CANONICAL_BYTES'
                                       for row in cases for result in row['decodes']),
                  'shared_signed_zero_decodes': 4,
                  'nonsharing_controls': 6, 'shared_bool_int_controls': 2}, sort_keys=True))
raise SystemExit(0 if receipt['status'] == 'GREEN' else 1)
