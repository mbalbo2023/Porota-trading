#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT=/opt/porota-trading
LIB=/usr/local/lib/porota-contract-evidence-rc6
PROFILE=/home/porotaadmin/porota-browser-lab/chrome-profile
CE_PYTHON=/opt/porota-contract-evidence-venv/bin/python
COLLECTOR="$LIB/rc6_trusted_browser_contract_collector.py"
REAUTH="$LIB/rc6_ppi_web_reauth.py"
CONTAINER=porota_production_observer
OUTDIR="$ROOT/data/contract_evidence/rc6_trusted"

printf '%s\n' \
  'POROTA_CONTRACT_EVIDENCE_PURE_COLLECTOR_V6' \
  'MUTATIONS=AUTH_SESSION_AND_CAPTURE_FILE_ONLY' \
  'DB_IMPORT_EXECUTED=NO' \
  'SERVICE_RESTARTED=NO' \
  'REAL_ORDERS_SENT=0' \
  'ORDER_POST_ALLOWED=NO'

exec 9>/run/lock/porota-ppi-web-browser.lock
flock -w 20 9 || { echo 'STATE=SKIPPED_GLOBAL_PPI_BROWSER_LOCK_BUSY'; exit 5; }
echo 'GLOBAL_PPI_BROWSER_LOCK=ACQUIRED'

if systemctl is-active --quiet porota-ppi-web-residual-rc6.service; then
  echo 'STATE=BLOCKED_RESIDUAL_WRITER_ACTIVE'
  exit 5
fi

test -r "$COLLECTOR" || { echo 'STATE=BLOCKED_COLLECTOR_MISSING'; exit 5; }
test -r "$REAUTH" || { echo 'STATE=BLOCKED_REAUTH_HELPER_MISSING'; exit 5; }
test -x "$CE_PYTHON" || { echo 'STATE=BLOCKED_CE_PYTHON_MISSING'; exit 5; }
test -d "$PROFILE" || { echo 'STATE=BLOCKED_PROFILE_MISSING'; exit 5; }

grep -F 'SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}' "$COLLECTOR" >/dev/null || { echo 'STATE=BLOCKED_COLLECTOR_METHOD_GUARD'; exit 5; }
grep -F 'APPROVED_AUTH_POSTS = {' "$REAUTH" >/dev/null || { echo 'STATE=BLOCKED_REAUTH_ALLOWLIST'; exit 5; }
grep -F 'if method == "POST" and key in APPROVED_AUTH_POSTS:' "$REAUTH" >/dev/null || { echo 'STATE=BLOCKED_REAUTH_POST_GUARD'; exit 5; }
grep -F 'ORDER_PATH_HINT = re.compile' "$REAUTH" >/dev/null || { echo 'STATE=BLOCKED_REAUTH_ORDER_GUARD'; exit 5; }
echo 'SAFETY_AUDIT=PASS'

SAFETY="$(docker exec -i "$CONTAINER" python -c "import sqlite3,json; c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True); c.execute('pragma query_only=on'); r=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone(); c.close(); print(json.dumps({'mode':r[0] if r else None,'real_orders_sent':r[1] if r else None},sort_keys=True))")"
echo "OBSERVER_PREFLIGHT=$SAFETY"
SAFETY="$SAFETY" python3 -c "import os,json,sys; d=json.loads(os.environ['SAFETY']); sys.exit(0 if d.get('mode')=='PRODUCTION_PAPER' and int(d.get('real_orders_sent') or 0)==0 else 1)" || { echo 'STATE=BLOCKED_OBSERVER_SAFETY'; exit 5; }

BROWSER_USER="$(stat -c '%U' "$PROFILE")"
BROWSER_HOME="$(getent passwd "$BROWSER_USER" | cut -d: -f6)"
BROWSER_GROUP="$(id -gn "$BROWSER_USER")"
test -d "$BROWSER_HOME" || { echo 'STATE=BLOCKED_BROWSER_HOME'; exit 5; }

STAGE="$BROWSER_HOME/porota-browser-lab/rc6-open-session"
install -d -o "$BROWSER_USER" -g "$BROWSER_GROUP" -m 0700 "$STAGE"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE_CAPTURE="$STAGE/contract_open_session_${stamp}.json"
CAPTURE="$OUTDIR/contract_open_session_${stamp}.json"

collect_once() {
  rm -f "$STAGE_CAPTURE"
  runuser -u "$BROWSER_USER" -- env HOME="$BROWSER_HOME" PYTHONPATH="$LIB:$ROOT" \
    "$CE_PYTHON" "$COLLECTOR" \
    --profile "$PROFILE" \
    --jobs CONTRACT_EVIDENCE_DYNAMIC,CONTRACT_EVIDENCE_CAUCIONES \
    --output "$STAGE_CAPTURE"
}

echo 'JOBS=CONTRACT_EVIDENCE_DYNAMIC,CONTRACT_EVIDENCE_CAUCIONES'
set +e
collect_once
COLLECT_RC=$?
set -e
test -r "$STAGE_CAPTURE" || { echo 'STATE=BLOCKED_FRESH_CAPTURE_MISSING'; exit 6; }
AUTH_STATE="$(python3 - "$STAGE_CAPTURE" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding='utf-8')).get('auth_status','UNKNOWN'))
PY
)"
echo "FIRST_AUTH_STATE=$AUTH_STATE"

if [ "$COLLECT_RC" -ne 0 ] && [ "$AUTH_STATE" = 'BLOCKED_AUTH_SESSION_EXPIRED' ]; then
  SECRET_FILE=''
  CONF=/etc/porota/contract-evidence-rc6.conf
  if [ -r "$CONF" ]; then
    for key in POROTA_PPI_WEB_SECRET_FILE PPI_WEB_SECRET_FILE; do
      line="$(grep -E "^${key}=" "$CONF" 2>/dev/null | tail -1 || true)"
      if [ -n "$line" ]; then
        candidate="${line#*=}"
        candidate="${candidate%\"}"; candidate="${candidate#\"}"
        candidate="${candidate%\'}"; candidate="${candidate#\'}"
        if [ -f "$candidate" ]; then SECRET_FILE="$candidate"; break; fi
      fi
    done
  fi
  if [ -z "$SECRET_FILE" ]; then
    while IFS= read -r -d '' f; do
      if grep -q '^PPI_WEB_PASSWORD=' "$f" 2>/dev/null; then
        SECRET_FILE="$f"
        break
      fi
    done < <(find /etc/porota -maxdepth 2 -type f -print0 2>/dev/null)
  fi
  test -n "$SECRET_FILE" -a -f "$SECRET_FILE" || { echo 'STATE=BLOCKED_AUTH_LOCAL_SECRET_NOT_FOUND'; exit 6; }
  runuser -u "$BROWSER_USER" -- test -r "$SECRET_FILE" || { echo 'STATE=BLOCKED_AUTH_LOCAL_SECRET_NOT_READABLE_BY_BROWSER_USER'; exit 6; }
  echo 'LOCAL_REAUTH_SECRET=FOUND_AND_NOT_PRINTED'

  set +e
  REAUTH_OUT="$(runuser -u "$BROWSER_USER" -- env HOME="$BROWSER_HOME" PYTHONPATH="$LIB:$ROOT" "$CE_PYTHON" "$REAUTH" --profile "$PROFILE" --secret "$SECRET_FILE")"
  REAUTH_RC=$?
  set -e
  REAUTH_STATE="$(REAUTH_OUT="$REAUTH_OUT" python3 - <<'PY'
import json,os
try:
    d=json.loads(os.environ.get('REAUTH_OUT','{}'))
    print(d.get('status','UNKNOWN'))
except Exception:
    print('INVALID_REAUTH_OUTPUT')
PY
)"
  REAUTH_OUT="$REAUTH_OUT" python3 - <<'PY'
import json,os
try:
    d=json.loads(os.environ.get('REAUTH_OUT','{}'))
except Exception:
    d={'status':'INVALID_REAUTH_OUTPUT'}
print('REAUTH_STATUS='+str(d.get('status')))
print('REAUTH_STAGE='+str(d.get('stage') or ''))
print('REAUTH_ATTEMPTS='+str(d.get('attempts') or 0))
print('REAUTH_BLOCKED_POST_PATH='+str(d.get('blocked_post_path') or ''))
print('REAUTH_REAL_ORDERS_SENT='+str(d.get('real_orders_sent',0)))
PY
  if [ "$REAUTH_RC" -ne 0 ] || [ "$REAUTH_STATE" != 'AUTHENTICATED_TRUSTED_DEVICE' ]; then
    echo 'STATE=BLOCKED_REAUTH_NOT_GREEN'
    exit 6
  fi

  echo 'REAUTH=GREEN_RETRYING_READONLY_COLLECTOR'
  set +e
  collect_once
  COLLECT_RC=$?
  set -e
  test -r "$STAGE_CAPTURE" || { echo 'STATE=BLOCKED_POST_REAUTH_CAPTURE_MISSING'; exit 6; }
fi

echo "FINAL_COLLECT_RC=$COLLECT_RC"
install -d -m 0750 "$OUTDIR"
install -o root -g root -m 0600 "$STAGE_CAPTURE" "$CAPTURE"
rm -f "$STAGE_CAPTURE"

python3 - "$CAPTURE" "$COLLECT_RC" <<'PY'
import json,os,sys
from collections import Counter
path=sys.argv[1]
rc=int(sys.argv[2])
doc=json.load(open(path,encoding='utf-8'))
eps=list((doc.get('endpoints') or {}).values())
kinds=Counter(str(e.get('kind') or 'UNKNOWN') for e in eps if isinstance(e,dict))
al30=[]
cauc_rows=0
for e in eps:
    if not isinstance(e,dict):
        continue
    if 'AL30' in json.dumps(e,ensure_ascii=False).upper():
        fields=[]
        for row in (e.get('rows') if isinstance(e.get('rows'),list) else []):
            if isinstance(row,dict) and 'AL30' in json.dumps(row,ensure_ascii=False).upper():
                fields.append(sorted(str(k) for k in row.keys()))
        if isinstance(e.get('row'),dict) and 'AL30' in json.dumps(e['row'],ensure_ascii=False).upper():
            fields.append(sorted(str(k) for k in e['row'].keys()))
        al30.append({'kind':e.get('kind'),'route':e.get('observed_route'),'fieldsets':fields[:8]})
    if e.get('kind')=='CaucionesOperables':
        cauc_rows += len(e.get('rows') if isinstance(e.get('rows'),list) else [])
required={'InstrumentosOperables','CaucionesOperables','DatosTecnicos'}
present=set(kinds)
print('CAPTURE_FILE='+os.path.basename(path))
print('CAPTURE_BYTES='+str(os.path.getsize(path)))
print('AUTH_STATUS='+str(doc.get('auth_status')))
print('ROUTES_REACHED='+str(sum(1 for r in doc.get('routes',[]) if isinstance(r,dict) and r.get('reached')))+'/'+str(len(doc.get('routes',[]))))
print('ENDPOINT_KIND_COUNTS='+json.dumps(dict(sorted(kinds.items())),sort_keys=True))
print('AL30_EXPLICIT='+('YES' if al30 else 'NO'))
print('AL30_FIELD_EVIDENCE='+json.dumps(al30,ensure_ascii=False,sort_keys=True))
print('CAUCIONES_OPERABLES_ROWS='+str(cauc_rows))
print('REQUIRED_ENDPOINT_KINDS_PRESENT='+','.join(sorted(required & present)))
print('REQUIRED_ENDPOINT_KINDS_MISSING='+','.join(sorted(required - present)))
print('STRUCTURALLY_IMPORTABLE_CANDIDATE='+('YES' if required <= present and al30 and cauc_rows else 'NO'))
print('BLOCKED_NONREAD_COUNT='+str(len(doc.get('blocked_nonread') or [])))
print('DB_IMPORT_EXECUTED=NO')
print('SERVICE_RESTARTED=NO')
print('REAL_ORDERS_SENT='+str(doc.get('real_orders_sent',0)))
if rc != 0 or doc.get('auth_status')!='AUTHENTICATED_TRUSTED_DEVICE':
    print('STATE=BLOCKED_COLLECTION_NOT_AUTHENTICATED')
    raise SystemExit(6)
print('STATE=EVIDENCE_CAPTURE_COMPLETE')
PY
