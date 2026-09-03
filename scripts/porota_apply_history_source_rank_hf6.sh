#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="${1:-/opt/porota-trading}"
TARGET="$ROOT/cu_history_store_v2_hf6.py"

python3 - "$TARGET" <<'PY'
from pathlib import Path
import os,sys,tempfile
p=Path(sys.argv[1])
text=p.read_text(encoding='utf-8')
expected='    "A3_CEM_CLOSING": 20,\n'
if expected in text:
    print('A3_CEM_CLOSING_SOURCE_RANK=ALREADY_PRESENT')
    raise SystemExit(0)
# Reject the earlier typo explicitly instead of silently preserving it.
if '    "A3_CEM": 20,\n' in text:
    text=text.replace('    "A3_CEM": 20,\n',expected,1)
else:
    anchor='    "BYMA": 20,\n    "IOL": 30,\n'
    replacement='    "BYMA": 20,\n    "A3_CEM_CLOSING": 20,\n    "IOL": 30,\n'
    if text.count(anchor)!=1:
        raise SystemExit('PATCH_ABORT_A3_CEM_CLOSING_SOURCE_RANK_ANCHOR')
    text=text.replace(anchor,replacement,1)
fd,tmp=tempfile.mkstemp(prefix=p.name+'.',dir=str(p.parent)); os.close(fd)
Path(tmp).write_text(text,encoding='utf-8')
os.replace(tmp,p)
print('A3_CEM_CLOSING_SOURCE_RANK=APPLIED')
PY
# Worktree is writable during candidate materialization.
python3 -m py_compile "$TARGET"
python3 - "$TARGET" <<'PY'
import importlib.util,sys
p=sys.argv[1]
spec=importlib.util.spec_from_file_location('history_v2_rank_check',p)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
assert mod.SOURCE_RANK.get('A3_CEM_CLOSING') == 20
assert 'A3_CEM' not in mod.SOURCE_RANK
assert mod.SOURCE_RANK['PPI_PRODUCTION_HISTORY'] < mod.SOURCE_RANK['A3_CEM_CLOSING']
print('A3_CEM_SOURCE_PRECEDENCE_CHECK=OK')
PY
true
