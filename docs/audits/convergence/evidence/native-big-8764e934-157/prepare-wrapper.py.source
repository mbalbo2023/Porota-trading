import argparse
import ast
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--index', type=Path, required=True)
parser.add_argument('--raw', type=Path, required=True)
parser.add_argument('--data', type=Path, required=True)
parser.add_argument('--sha', required=True)
parser.add_argument('--tree', required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
assert re.fullmatch('[0-9a-f]{40}', args.sha)
assert re.fullmatch('[0-9a-f]{40}', args.tree)
assert not args.out.exists() and not args.raw.exists() and not args.data.exists()
assert args.source.is_dir() and args.index.is_file()
pin = json.loads(args.index.read_bytes())
assert pin['source_sha'] == args.sha and pin['source_tree'] == args.tree
assert type(pin['overlay_count']) is int and pin['overlay_count'] == 0
assert args.raw.is_absolute() and args.data.is_absolute()
assert not args.raw.is_relative_to(args.source) and not args.data.is_relative_to(args.source)
assert not args.raw.is_relative_to(args.data) and not args.data.is_relative_to(args.raw)
wire = Path('/tmp/rc6-canonical-d9e7fb7d-big-gc-wrapper.py').read_text()
changes = {
    "SOURCE = Path('/workspace/rc6-whole-source-20261005-capture-budget')": f'SOURCE = Path({str(args.source)!r})',
    "RAW = Path('/tmp/rc6-canonical-d9e7fb7d-big-gc-raw')": f'RAW = Path({str(args.raw)!r})',
    "DATA = Path('/workspace/rc6-canonical-d9e7fb7d-big-gc')": f'DATA = Path({str(args.data)!r})',
    "PIN_PATH = Path('/tmp/rc6-whole-source-20261005-capture-budget-raw/source.index.json')": f'PIN_PATH = Path({str(args.index)!r})',
    "'d9e7fb7d567ff745193c125dc9cd8df4e91ec5d6'": repr(args.sha),
    "'95a95d65fe18f1fbe8f108d582d984ee3e7355ac'": repr(args.tree),
    "Path('/tmp/rc6-whole-source-20261005-capture-budget-raw/source.tar')": f'Path({str(args.index.parent / "source.tar")!r})',
    "if (RAW / 'child-stacks.log').exists():\n    raise ValueError('NEW_DIAGNOSTIC_STACK_FILE_REQUIRED')\n": '',
    "'--slow-disk', '--canonical-runtime', '--diagnostic-stacks', str(RAW / 'child-stacks.log')": "'--slow-disk', '--canonical-runtime'",
    "'DIAGNOSTIC_ONLY_NATIVE157_NO_ACCEPTANCE_OR_ARTIFACT_OR_RUNTIME_VALIDATION'": "'SOURCE_ONLY_UNINSTRUMENTED_CANONICAL_BIG_NATIVE157_NOT_GOV_ARTIFACT_RUNTIME_VALIDATION'",
    "'diagnostic_only': True": "'diagnostic_only': False",
    "'native-big-gc-result.json'": "'native-big-result.json'",
    "'big-gc.log'": "'big.log'",
}
for old, new in changes.items():
    assert old in wire, old
    wire = wire.replace(old, new)
assert '--diagnostic-stacks' not in wire
ast.parse(wire)
args.raw.mkdir(mode=0o700)
with args.out.open('x') as target:
    target.write(wire)
args.out.chmod(0o600)
print(json.dumps({'wrapper': str(args.out), 'source_sha': args.sha, 'source_tree': args.tree,
                  'source_files': len(pin['files']), 'raw': str(args.raw), 'data': str(args.data),
                  'native_diagnostic_gc_observer_enabled': False, 'overlay_count': 0}))
