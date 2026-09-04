#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="${POROTA_ROOT:-/opt/porota-trading}"
PROFILE="${POROTA_CHROME_PROFILE:-}"
OBSERVER="${POROTA_OBSERVER_CONTAINER:-porota_production_observer}"
OUTDIR="$ROOT/data/contract_evidence/rc4_trusted"
mkdir -p "$OUTDIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUTDIR/contract_${STAMP}.json"

# RC4-HF1: materializar el schema v2 antes de consultar cadencias. Esto hace
# que dashboard/scheduler puedan mostrar BLOCKED_AUTH de forma explícita y
# evita depender de que la primera captura autenticada sea también quien cree
# tablas bajo carga de rueda.
/usr/bin/docker exec "$OBSERVER" python -c \
  'from rc4_contract_import_job import Store,DB; from cp_contract_evidence_v2_hf6 import init_schema; init_schema(Store(DB)); print("CONTRACT_V2_SCHEMA=READY")'

DUE_JSON="$(/usr/bin/docker exec "$OBSERVER" python /app/rc4_contract_due_job.py 2>/dev/null || printf '%s' '{"state":"ERROR","due_jobs":[]}')"
JOBS="$(printf '%s' "$DUE_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(",".join(d.get("due_jobs") or []))' 2>/dev/null || true)"
if [[ -z "$JOBS" ]]; then echo 'STATUS=CACHED_NOT_DUE'; exit 0; fi

write_blocked(){
  local state="$1"
  python3 - "$OUT" "$JOBS" "$state" <<'PY'
import json,sys,os
from datetime import datetime,timezone
p,jobs,state=sys.argv[1:]
d={'schema':'POROTA_RC4_PPI_TRUSTED_CONTRACT_1','generated_at':datetime.now(timezone.utc).isoformat(),
   'auth_status':state,'jobs':[x for x in jobs.split(',') if x],'routes':[],'endpoints':{},'blocked_nonread':[],
   'continue_clicked':False,'amount_filled':False,'price_filled':False,'real_orders_sent':0}
open(p,'w',encoding='utf-8').write(json.dumps(d,ensure_ascii=False,indent=2)); os.chmod(p,0o600)
PY
}

if [[ -z "$PROFILE" || ! -d "$PROFILE" ]]; then
  write_blocked BLOCKED_AUTH_PROFILE_MISSING
else
  PY="${POROTA_BROWSER_PYTHON:-}"
  if [[ -z "$PY" ]]; then
    for c in "$ROOT/.browser-venv/bin/python" /opt/porota-browser-venv/bin/python /usr/bin/python3; do
      if [[ -x "$c" ]] && "$c" -c 'import playwright' >/dev/null 2>&1; then PY="$c"; break; fi
    done
  fi
  if [[ -z "$PY" ]]; then
    write_blocked BLOCKED_PLAYWRIGHT_UNAVAILABLE
  else
    "$PY" "$ROOT/rc4_trusted_browser_contract_collector.py" --profile "$PROFILE" --jobs "$JOBS" --output "$OUT" || write_blocked BLOCKED_BROWSER_ERROR
  fi
fi

# La captura ya está sanitizada y no contiene cookies/OTP/tokens. El servicio
# host corre como root, mientras el observer corre como botuser uid 1000. Si
# el archivo queda root:root 0600, el importador dentro del observer no puede
# leerlo y Contract Evidence v2 queda vacío aunque el collector haya corrido.
# Dar lectura al uid del runtime es una frontera de permisos, no una exposición
# de credenciales.
chown 1000:1000 "$OUT" 2>/dev/null || true
chmod 0640 "$OUT"

REL="${OUT#$ROOT/data/}"
IMPORT_JSON="$(/usr/bin/docker exec "$OBSERVER" python /app/rc4_contract_import_job.py --input "/app/data/$REL")"
printf '%s\n' "$IMPORT_JSON"
printf 'STATUS=COMPLETE\nOUTPUT=%s\nJOBS=%s\nPROFILE_CONFIGURED=%s\n' \
  "$OUT" "$JOBS" "$([[ -n "$PROFILE" && -d "$PROFILE" ]] && echo YES || echo NO)"
