#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${POROTA_ROOT:-/opt/porota-trading}"
CONTAINER="${POROTA_OBSERVER_CONTAINER:-porota_production_observer}"
DOCKER_BIN="${POROTA_DOCKER_BIN:-/usr/bin/docker}"
EXPECTED_IMAGE="${POROTA_CONTRACT_EVIDENCE_EXPECTED_IMAGE:-}"
SESSION_RUNNER="${POROTA_CONTRACT_EVIDENCE_SESSION_RUNNER:-$ROOT/scripts/porota_contract_evidence_session_runner_rc4.py}"

fail_closed() {
  local code="$1"; shift
  printf 'STATUS=RED_FAIL_CLOSED\n'
  printf 'REASON=%s\n' "$*"
  printf 'REAL_ORDERS_SENT=0\n'
  exit "$code"
}

if [[ -z "$EXPECTED_IMAGE" ]]; then
  fail_closed 3 EXPECTED_IMAGE_NOT_CONFIGURED
fi

RUNNING="$($DOCKER_BIN inspect --format='{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)"
[[ "$RUNNING" == "true" ]] || fail_closed 3 OBSERVER_NOT_RUNNING

IMAGE="$($DOCKER_BIN inspect --format='{{.Config.Image}}' "$CONTAINER" 2>/dev/null || true)"
[[ "$IMAGE" == "$EXPECTED_IMAGE" ]] || fail_closed 3 "UNEXPECTED_IMAGE:${IMAGE:-UNKNOWN}"

READONLY="$($DOCKER_BIN inspect --format='{{.HostConfig.ReadonlyRootfs}}' "$CONTAINER" 2>/dev/null || true)"
[[ "$READONLY" == "true" ]] || fail_closed 3 "OBSERVER_NOT_READONLY:${READONLY:-UNKNOWN}"

preflight="$($DOCKER_BIN exec -i "$CONTAINER" python - <<'PY'
import json, sqlite3
p='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=10)
c.execute('PRAGMA query_only=ON')
quick=c.execute('PRAGMA quick_check').fetchone()[0]
row=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
c.close()
print(json.dumps({'quick_check':quick,'mode':row[0] if row else None,'real_orders_sent':row[1] if row else None},sort_keys=True))
PY
)" || fail_closed 3 OBSERVER_PREFLIGHT_FAILED

PRECHECK="$preflight" python3 - <<'PY' || exit 3
import json, os, sys
try: d=json.loads(os.environ['PRECHECK'])
except Exception: sys.exit(1)
if d.get('quick_check')!='ok' or d.get('mode')!='PRODUCTION_PAPER' or int(d.get('real_orders_sent') or 0)!=0:
    sys.exit(1)
PY
if [[ $? -ne 0 ]]; then fail_closed 3 OBSERVER_SAFETY_INVARIANT_FAILED; fi

DUE_JSON="$($DOCKER_BIN exec -i "$CONTAINER" python /app/rc4_contract_due_job.py 2>/dev/null)" || fail_closed 3 DUE_POLICY_FAILED
export DUE_JSON
JOBS="$(python3 - <<'PY'
import json, os, sys
try: d=json.loads(os.environ['DUE_JSON'])
except Exception: sys.exit(2)
if d.get('state')!='OK': sys.exit(3)
print(','.join(str(x) for x in (d.get('due_jobs') or [])))
PY
)" || fail_closed 3 DUE_POLICY_INVALID_OUTPUT

if [[ -z "$JOBS" ]]; then
  printf 'STATUS=GREEN_NOT_DUE\n'
  printf 'EXPECTED_IMAGE=%s\n' "$EXPECTED_IMAGE"
  printf 'OBSERVER_READONLY=true\n'
  printf 'DUE_JOBS=0\n'
  printf 'AUTH_BROWSER_STARTED=NO\n'
  printf 'PPI_CALLS=0\n'
  printf 'REAL_ORDERS_SENT=0\n'
  exit 0
fi

[[ -f "$SESSION_RUNNER" ]] || fail_closed 3 SESSION_RUNNER_MISSING

set +e
RUN_OUT="$(python3 "$SESSION_RUNNER" 2>&1)"
RUN_RC=$?
set -e
LAST_JSON="$(printf '%s\n' "$RUN_OUT" | tail -n 1)"
export LAST_JSON RUN_RC

COLLECTION_STATE="$(python3 - <<'PY'
import json, os, sys
try: d=json.loads(os.environ['LAST_JSON'])
except Exception: print('INVALID_JSON'); sys.exit(0)
print(str(d.get('state') or 'UNKNOWN'))
PY
)"

if [[ "$RUN_RC" != "0" ]]; then
  fail_closed 4 "SESSION_RUNNER_RC:${RUN_RC}:${COLLECTION_STATE}"
fi

export COLLECTION_STATE
python3 - <<'PY' || fail_closed 4 "COLLECTION_NOT_GREEN:${COLLECTION_STATE}"
import json, os, sys
d=json.loads(os.environ['LAST_JSON'])
if d.get('state')!='AUTHENTICATED_TRUSTED_DEVICE': sys.exit(1)
if int(d.get('real_orders_sent') or 0)!=0: sys.exit(2)
if int(d.get('import_rc') or 0)!=0: sys.exit(3)
PY

postflight="$($DOCKER_BIN exec -i "$CONTAINER" python - <<'PY'
import json, sqlite3
p='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=10)
c.execute('PRAGMA query_only=ON')
quick=c.execute('PRAGMA quick_check').fetchone()[0]
row=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
runs=0
if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract_evidence_v2_runs'").fetchone():
    runs=int(c.execute('SELECT COUNT(*) FROM contract_evidence_v2_runs').fetchone()[0] or 0)
c.close()
print(json.dumps({'quick_check':quick,'mode':row[0] if row else None,'real_orders_sent':row[1] if row else None,'ce_runs':runs},sort_keys=True))
PY
)" || fail_closed 3 OBSERVER_POSTFLIGHT_FAILED
export POSTCHECK="$postflight"
python3 - <<'PY' || fail_closed 3 OBSERVER_POST_SAFETY_INVARIANT_FAILED
import json, os, sys
d=json.loads(os.environ['POSTCHECK'])
if d.get('quick_check')!='ok' or d.get('mode')!='PRODUCTION_PAPER' or int(d.get('real_orders_sent') or 0)!=0:
    sys.exit(1)
PY

printf 'STATUS=GREEN_COLLECTION\n'
printf 'EXPECTED_IMAGE=%s\n' "$EXPECTED_IMAGE"
printf 'DUE_JOBS=%s\n' "$JOBS"
printf 'AUTH_STATUS=AUTHENTICATED_TRUSTED_DEVICE\n'
printf 'IMPORT_RC=0\n'
printf 'OBSERVER_READONLY=true\n'
printf 'DB_QUICK_CHECK=ok\n'
printf 'REAL_ORDERS_SENT=0\n'
