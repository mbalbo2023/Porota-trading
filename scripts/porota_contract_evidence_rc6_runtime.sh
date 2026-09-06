#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${POROTA_ROOT:-/opt/porota-trading}"
CONTAINER="${POROTA_OBSERVER_CONTAINER:-porota_production_observer}"
DOCKER_BIN="${POROTA_DOCKER_BIN:-/usr/bin/docker}"
LIB="${POROTA_RC6_CE_LIB:-/usr/local/lib/porota-contract-evidence-rc6}"
PROFILE="${POROTA_CHROME_PROFILE:-/home/porotaadmin/porota-browser-lab/chrome-profile}"
OUTDIR="$ROOT/data/contract_evidence/rc6_trusted"
STATE="$OUTDIR/runtime_state.json"
EXPECTED_IMAGE="porota-trading-bot:17.0.0-rc6"

fail_closed() {
  printf 'STATUS=RED_FAIL_CLOSED\nREASON=%s\nREAL_ORDERS_SENT=0\n' "$*"
  exit 3
}

RUNNING="$($DOCKER_BIN inspect --format='{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)"
[[ "$RUNNING" == true ]] || fail_closed OBSERVER_NOT_RUNNING
IMAGE="$($DOCKER_BIN inspect --format='{{.Config.Image}}' "$CONTAINER" 2>/dev/null || true)"
[[ "$IMAGE" == "$EXPECTED_IMAGE" ]] || fail_closed "UNEXPECTED_IMAGE:${IMAGE:-UNKNOWN}"
READONLY="$($DOCKER_BIN inspect --format='{{.HostConfig.ReadonlyRootfs}}' "$CONTAINER" 2>/dev/null || true)"
[[ "$READONLY" == true ]] || fail_closed OBSERVER_NOT_READONLY

for f in rc6_contract_due_job.py rc6_contract_schedule.py rc6_ppi_contract_normalizer.py rc6_trusted_browser_contract_collector.py rc6_contract_capture_importer.py; do
  [[ -r "$LIB/$f" ]] || fail_closed "MISSING_NATIVE_COMPONENT:$f"
done

preflight="$($DOCKER_BIN exec -i "$CONTAINER" python - <<'PY'
import json,sqlite3
p='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=15); c.execute('PRAGMA query_only=ON')
qc=c.execute('PRAGMA quick_check').fetchone()[0]
r=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone(); c.close()
print(json.dumps({'quick_check':qc,'mode':r[0] if r else None,'real_orders_sent':r[1] if r else None},sort_keys=True))
PY
)" || fail_closed OBSERVER_PREFLIGHT_FAILED
PRECHECK="$preflight" python3 - <<'PY' || fail_closed OBSERVER_SAFETY_INVARIANT_FAILED
import json,os,sys
d=json.loads(os.environ['PRECHECK'])
sys.exit(0 if d.get('quick_check')=='ok' and d.get('mode')=='PRODUCTION_PAPER' and int(d.get('real_orders_sent') or 0)==0 else 1)
PY

mkdir -p "$OUTDIR"
chmod 0750 "$OUTDIR"

# Avoid hammering an expired trusted session. Auth/2FA is never solved inside this job.
if [[ -s "$STATE" ]]; then
  if python3 - "$STATE" <<'PY'
import json,sys
from datetime import datetime,timezone,timedelta
try:
 d=json.load(open(sys.argv[1],encoding='utf-8')); at=datetime.fromisoformat(d.get('blocked_at','').replace('Z','+00:00'))
 if at.tzinfo is None: at=at.replace(tzinfo=timezone.utc)
 raise SystemExit(0 if datetime.now(timezone.utc)-at < timedelta(hours=1) else 1)
except Exception: raise SystemExit(1)
PY
  then
    printf 'STATUS=AMARILLO_AUTH_BACKOFF\nAUTH_BROWSER_STARTED=NO\nREAL_ORDERS_SENT=0\n'
    exit 0
  fi
fi

DUE_JSON="$(PYTHONPATH="$LIB:$ROOT" PAPER_V17_DB_PATH="$ROOT/data/paper_v17/observer_v17.db" python3 "$LIB/rc6_contract_due_job.py")" || fail_closed DUE_POLICY_FAILED
export DUE_JSON
JOBS="$(python3 - <<'PY'
import json,os,sys
d=json.loads(os.environ['DUE_JSON'])
if d.get('state')!='OK': sys.exit(2)
print(','.join(d.get('due_jobs') or []))
PY
)" || fail_closed DUE_POLICY_INVALID

if [[ -z "$JOBS" ]]; then
  printf 'STATUS=GREEN_NOT_DUE\nAUTH_BROWSER_STARTED=NO\nPPI_CALLS=0\nREAL_ORDERS_SENT=0\n'
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
CAPTURE="$OUTDIR/contract_${stamp}.json"
set +e
PYTHONPATH="$LIB:$ROOT" python3 "$LIB/rc6_trusted_browser_contract_collector.py" \
  --profile "$PROFILE" --jobs "$JOBS" --output "$CAPTURE"
COLLECT_RC=$?
set -e

if [[ "$COLLECT_RC" -ne 0 ]]; then
  AUTH_STATE="$(python3 - "$CAPTURE" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1],encoding='utf-8')).get('auth_status','UNKNOWN'))
except Exception: print('INVALID_CAPTURE')
PY
)"
  printf '{"blocked_at":"%s","state":"%s","retry_after_seconds":3600}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$AUTH_STATE" > "$STATE"
  chmod 0600 "$STATE"
  printf 'STATUS=AMARILLO_AUTH_BLOCKED\nAUTH_STATUS=%s\nAUTH_BROWSER_STARTED=YES\nREAL_ORDERS_SENT=0\n' "$AUTH_STATE"
  exit 0
fi

REL="${CAPTURE#"$ROOT/data/"}"
set +e
IMPORT_OUT="$($DOCKER_BIN exec -i "$CONTAINER" python - "/app/data/$REL" < "$LIB/rc6_contract_capture_importer.py" 2>&1)"
IMPORT_RC=$?
set -e
[[ "$IMPORT_RC" -eq 0 ]] || fail_closed "IMPORT_FAILED_RC_${IMPORT_RC}"
rm -f "$STATE"

postflight="$($DOCKER_BIN exec -i "$CONTAINER" python - <<'PY'
import json,sqlite3
p='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=15); c.execute('PRAGMA query_only=ON')
qc=c.execute('PRAGMA quick_check').fetchone()[0]
r=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
runs=c.execute("SELECT COUNT(*) FROM contract_evidence_v2_runs").fetchone()[0] if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract_evidence_v2_runs'").fetchone() else 0
c.close(); print(json.dumps({'quick_check':qc,'mode':r[0] if r else None,'real_orders_sent':r[1] if r else None,'runs':runs},sort_keys=True))
PY
)" || fail_closed OBSERVER_POSTFLIGHT_FAILED
export POSTCHECK="$postflight"
python3 - <<'PY' || fail_closed OBSERVER_POST_SAFETY_INVARIANT_FAILED
import json,os,sys
d=json.loads(os.environ['POSTCHECK'])
sys.exit(0 if d.get('quick_check')=='ok' and d.get('mode')=='PRODUCTION_PAPER' and int(d.get('real_orders_sent') or 0)==0 else 1)
PY

printf 'STATUS=GREEN_COLLECTION\nDUE_JOBS=%s\nAUTH_STATUS=AUTHENTICATED_TRUSTED_DEVICE\nIMPORT_RC=0\nOBSERVER_READONLY=true\nDB_QUICK_CHECK=ok\nREAL_ORDERS_SENT=0\n' "$JOBS"
printf '%s\n' "$IMPORT_OUT" | tail -n 1
