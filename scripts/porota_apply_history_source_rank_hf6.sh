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
if '"A3_CEM": 20,' in text:
    print('A3_CEM_SOURCE_RANK=ALREADY_PRESENT')
    raise SystemExit(0)
anchor='    "BYMA": 20,\n    "IOL": 30,\n'
replacement='    "BYMA": 20,\n    "A3_CEM": 20,\n    "IOL": 30,\n'
if text.count(anchor)!=1:
    raise SystemExit('PATCH_ABORT_A3_CEM_SOURCE_RANK_ANCHOR')
text=text.replace(anchor,replacement,1)
fd,tmp=tempfile.mkstemp(prefix=p.name+'.',dir=str(p.parent)); os.close(fd)
Path(tmp).write_text(text,encoding='utf-8')
os.replace(tmp,p)
print('A3_CEM_SOURCE_RANK=APPLIED')
PY
python3 -m py_compile "$TARGET"
true
