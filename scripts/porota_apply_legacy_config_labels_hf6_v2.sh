#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="${1:-/opt/porota-trading}"
ENV_EXAMPLE="$ROOT/.env.example"
META="$ROOT/v_config_metadata.py"
MODE="${2:---check}"

if [[ "$MODE" != "--check" && "$MODE" != "--apply" ]]; then
  echo "USAGE=$0 [repo_root] [--check|--apply]"
  exit 2
fi

python3 - "$ENV_EXAMPLE" "$META" "$MODE" <<'PY'
from pathlib import Path
import sys,tempfile,os

env_path=Path(sys.argv[1]); meta_path=Path(sys.argv[2]); mode=sys.argv[3]
env=env_path.read_text(encoding='utf-8')
meta=meta_path.read_text(encoding='utf-8')

repls_env={
"# Hora de apertura de rueda (0-23, hora Argentina).\nMARKET_OPEN_HOUR=11":
"# LEGACY: hora global del motor histórico. HF6-v2 NO la usa como sesión autoritativa;\n# las sesiones se resuelven por familia/mercado/fecha con co_market_sessions_hf6.py.\nMARKET_OPEN_HOUR=11",
"# Hora de cierre de rueda.\nMARKET_CLOSE_HOUR=17":
"# LEGACY: cierre global del motor histórico. HF6-v2 NO lo usa como sesión autoritativa;\n# las sesiones se resuelven por familia/mercado/fecha.\nMARKET_CLOSE_HOUR=17",
"# Operaciones simultáneas permitidas.\nMAX_OPEN_POSITIONS=3":
"# LEGACY: cap del motor histórico. HF6-v2 NO lo usa como gate financiero normal;\n# la admisión PAPER usa riesgo concurrente dinámico y un cap técnico anti-runaway separado.\nMAX_OPEN_POSITIONS=3",
}
repls_meta={
'(\"MARKET_OPEN_HOUR\", \"Horarios\", \"Hora de apertura de rueda (0-23, hora Argentina).\", \"11\", False)':
'(\"MARKET_OPEN_HOUR\", \"Horarios\", \"LEGACY: hora global histórica; HF6-v2 usa sesiones versionadas por familia/mercado/fecha.\", \"11\", False)',
'(\"MARKET_CLOSE_HOUR\", \"Horarios\", \"Hora de cierre de rueda.\", \"17\", False)':
'(\"MARKET_CLOSE_HOUR\", \"Horarios\", \"LEGACY: cierre global histórico; HF6-v2 usa sesiones versionadas por familia/mercado/fecha.\", \"17\", False)',
'(\"MAX_OPEN_POSITIONS\", \"Riesgo\", \"Operaciones simultáneas permitidas.\", \"3\", False)':
'(\"MAX_OPEN_POSITIONS\", \"Riesgo\", \"LEGACY: cap histórico; HF6-v2 usa presupuesto de riesgo concurrente dinámico y cap técnico anti-runaway.\", \"3\", False)',
}

def apply(text,repls,label):
    changed=False
    for old,new in repls.items():
        if new in text:
            continue
        if text.count(old)!=1:
            raise SystemExit(f'PATCH_ABORT_{label}_ANCHOR')
        text=text.replace(old,new,1); changed=True
    return text,changed

env2,env_changed=apply(env,repls_env,'ENV')
meta2,meta_changed=apply(meta,repls_meta,'META')
print('LEGACY_CONFIG_LABELS_STATUS=' + ('READY_TO_APPLY' if mode=='--check' else 'APPLYING'))
print('ENV_EXAMPLE_CHANGE=' + ('YES' if env_changed else 'NO'))
print('CONFIG_METADATA_CHANGE=' + ('YES' if meta_changed else 'NO'))
if mode=='--apply':
    for path,text in ((env_path,env2),(meta_path,meta2)):
        fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=str(path.parent)); os.close(fd)
        Path(tmp).write_text(text,encoding='utf-8'); os.replace(tmp,path)
    print('LEGACY_CONFIG_LABELS=APPLIED')
else:
    print('FILE_MUTATION=NO')
PY

if [[ "$MODE" == "--apply" ]]; then
  python3 -m py_compile "$META"
fi
true
