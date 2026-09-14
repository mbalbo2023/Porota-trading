#!/usr/bin/env bash
# Canonical Point-1 recovery for PPI Web Contract Evidence.
# Known path: BLOCKED_AUTH_SESSION_EXPIRED -> isolated reauth -> one GET-only retry.
# This script NEVER imports contract evidence into DB.
set -Eeuo pipefail

ROOT="${POROTA_ROOT:-/opt/porota-trading}"
CONTAINER="${POROTA_OBSERVER_CONTAINER:-porota_production_observer}"
DOCKER="${POROTA_DOCKER_BIN:-/usr/bin/docker}"
LIB="${POROTA_RC6_CE_LIB:-/usr/local/lib/porota-contract-evidence-rc6}"
PROFILE="${POROTA_CHROME_PROFILE:-/home/porotaadmin/porota-browser-lab/chrome-profile}"
CE_PYTHON="${POROTA_CE_PYTHON:-/opt/porota-contract-evidence-venv/bin/python}"
PROBE_TICKER="${POROTA_TECHNICAL_PROBE_TICKER:-}"
PROBE_ROUTE="${POROTA_TECHNICAL_PROBE_ROUTE:-}"
BROWSER_USER="${POROTA_CE_BROWSER_USER:-porotaadmin}"
JOBS="${POROTA_POINT1_JOBS:-CONTRACT_EVIDENCE_STATIC,CONTRACT_EVIDENCE_DYNAMIC,CONTRACT_EVIDENCE_CAUCIONES}"
OUTDIR="$ROOT/data/contract_evidence/rc6_trusted"
HELPER="$LIB/rc6_ppi_web_reauth.py"
COLLECTOR="$LIB/rc6_trusted_browser_contract_collector.py"

printf 'PROBE=RC6_CONTRACT_NARROW_CAPTURE_REAUTH_V3\n'
printf 'NOW_UTC=%s\n' "$(date -u +%FT%TZ)"
printf 'JOBS=%s\n' "$JOBS"
printf 'KNOWN_WORKAROUND=BLOCKED_AUTH_SESSION_EXPIRED__REAUTH__ONE_GET_ONLY_RETRY\n'

postflight() {
  local post
  post="$($DOCKER exec -i "$CONTAINER" python - <<'PY'
import sqlite3,json
p='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=10)
c.execute('pragma query_only=on')
r=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone()
qc=c.execute('pragma quick_check').fetchone()[0]
c.close()
print(json.dumps({'mode':r[0] if r else None,'orders':r[1] if r else None,'quick_check':qc}))
PY
  )"
  POST="$post" python3 - <<'PY'
import json,os
d=json.loads(os.environ['POST'])
assert d.get('mode')=='PRODUCTION_PAPER'
assert int(d.get('orders') or 0)==0
assert d.get('quick_check')=='ok'
PY
  printf 'POSTFLIGHT=GREEN_PRODUCTION_PAPER_ORDERS0_DBOK\n'
  printf 'DB_IMPORT_EXECUTED=NO\n'
  printf 'SERVICE_RESTARTED=NO\n'
}

stop_safe() {
  printf '%s\n' "$1"
  postflight
  exit 0
}

# Serialize every browser-based PPI activity.
exec 9>/run/lock/porota-ppi-web-browser.lock
if ! flock -n 9; then
  printf 'POINT1_CAPTURE_RESULT=YELLOW_BROWSER_LOCK_BUSY\n'
  printf 'REAL_ORDERS_SENT=0\n'
  exit 0
fi

pre="$($DOCKER exec -i "$CONTAINER" python - <<'PY'
import sqlite3,json
p='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=10)
c.execute('pragma query_only=on')
qc=c.execute('pragma quick_check').fetchone()[0]
r=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone()
c.close()
print(json.dumps({'quick_check':qc,'mode':r[0] if r else None,'orders':r[1] if r else None}))
PY
)"
PRE="$pre" python3 - <<'PY'
import json,os
d=json.loads(os.environ['PRE'])
assert d.get('quick_check')=='ok'
assert d.get('mode')=='PRODUCTION_PAPER'
assert int(d.get('orders') or 0)==0
PY
printf 'PREFLIGHT=GREEN_PRODUCTION_PAPER_ORDERS0_DBOK\n'

test -r "$HELPER" || stop_safe 'POINT1_REAUTH_RESULT=RED_HELPER_MISSING'
test -r "$COLLECTOR" || stop_safe 'POINT1_REAUTH_RESULT=RED_COLLECTOR_MISSING'
test -d "$PROFILE" || stop_safe 'POINT1_REAUTH_RESULT=RED_PROFILE_MISSING'
test -x "$CE_PYTHON" || stop_safe 'POINT1_REAUTH_RESULT=RED_CE_PYTHON_MISSING'
[[ "$(stat -c '%U' "$PROFILE")" == "$BROWSER_USER" ]] || stop_safe 'POINT1_REAUTH_RESULT=RED_PROFILE_OWNER'

# Guard against regression to the pre-fix helper. These markers prove the
# known PPI Login/SSO route support and the fix for accessory non-PPI telemetry.
if ! grep -q '/api/Seguridad/Auth/Login' "$HELPER" \
   || ! grep -q '/api/logInSSO' "$HELPER" \
   || ! grep -q 'u.netloc not in ALLOWED_PAGE_HOSTS' "$HELPER"; then
  printf 'REAUTH_ATTEMPTED=NO\n'
  stop_safe 'POINT1_REAUTH_RESULT=RED_INSTALLED_HELPER_STALE'
fi
printf 'INSTALLED_REAUTH_HELPER=GREEN_KNOWN_FIX_PRESENT\n'

secret=/etc/porota/contract-evidence-web.env
conf=/etc/porota/contract-evidence-rc6.conf
if [[ -r "$conf" ]]; then
  cfg_secret="$(sed -n 's/^[[:space:]]*POROTA_PPI_WEB_SECRET_FILE[[:space:]]*=[[:space:]]*//p' "$conf" | tail -1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
  [[ -z "$cfg_secret" ]] || secret="$cfg_secret"
fi
if [[ ! -f "$secret" ]]; then
  printf 'REAUTH_ATTEMPTED=NO\n'
  stop_safe 'POINT1_REAUTH_RESULT=RED_LOCAL_SECRET_MISSING'
fi
if [[ "$(stat -c '%U' "$secret")" != "$BROWSER_USER" || "$(stat -c '%a' "$secret")" != 600 ]]; then
  printf 'REAUTH_ATTEMPTED=NO\n'
  stop_safe 'POINT1_REAUTH_RESULT=RED_LOCAL_SECRET_PERMISSIONS'
fi
printf 'LOCAL_SECRET=GREEN_PRESENT_OWNER_MODE600\n'

browser_home="$(getent passwd "$BROWSER_USER" | cut -d: -f6)"
[[ -n "$browser_home" && -d "$browser_home" ]] || stop_safe 'POINT1_REAUTH_RESULT=RED_BROWSER_HOME'
runuser -u "$BROWSER_USER" -- env HOME="$browser_home" "$CE_PYTHON" -c 'from playwright.sync_api import sync_playwright' \
  || stop_safe 'POINT1_REAUTH_RESULT=RED_PLAYWRIGHT'

# Canonical workaround. Never echo raw helper output; only allowlisted fields.
set +e
reauth_out="$(runuser -u "$BROWSER_USER" -- env HOME="$browser_home" PYTHONPATH="$LIB:$ROOT" \
  "$CE_PYTHON" "$HELPER" --profile "$PROFILE" --secret "$secret" 2>&1)"
reauth_rc=$?
set -e
printf 'REAUTH_RC=%s\n' "$reauth_rc"
reauth_safe="$(REAUTH_OUT="$reauth_out" python3 - <<'PY'
import json,os
raw=os.environ.get('REAUTH_OUT','').strip().splitlines()
d={}
for line in reversed(raw):
    try:
        x=json.loads(line)
        if isinstance(x,dict):
            d=x; break
    except Exception:
        pass
allow=('status','attempts','stage','page_url','blocked_post_path','auth_http_status','auth_json_object','auth_has_token_shape','auth_twofa_shape','auth_change_password_shape','auth_safe_keys','credentials_exposed','orders_visited','real_orders_sent')
safe={k:d.get(k) for k in allow if k in d}
print(json.dumps(safe,sort_keys=True,separators=(',',':')))
PY
)"
printf 'REAUTH_SAFE=%s\n' "$reauth_safe"
reauth_status="$(REAUTH_SAFE="$reauth_safe" python3 - <<'PY'
import json,os
try: print(json.loads(os.environ['REAUTH_SAFE']).get('status','INVALID_REAUTH_OUTPUT'))
except Exception: print('INVALID_REAUTH_OUTPUT')
PY
)"
printf 'REAUTH_STATUS=%s\n' "$reauth_status"
printf 'REAUTH_ATTEMPTED=YES\n'

if [[ "$reauth_rc" -ne 0 || "$reauth_status" != AUTHENTICATED_TRUSTED_DEVICE ]]; then
  printf 'POINT1_REAUTH_RESULT=RED_%s\n' "$reauth_status"
  stop_safe 'POINT1_CAPTURE_RESULT=RED_REAUTH_NOT_GREEN'
fi
printf 'POINT1_REAUTH_RESULT=GREEN_AUTHENTICATED_TRUSTED_DEVICE\n'

# One and only one GET-only collector retry after successful reauth.
stage_dir="$browser_home/porota-browser-lab/rc6-point1"
install -d -o "$BROWSER_USER" -g "$(id -gn "$BROWSER_USER")" -m 0700 "$stage_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage="$stage_dir/contract_point1_${stamp}.json"
final="$OUTDIR/contract_point1_${stamp}.json"
rm -f "$stage"

set +e
runuser -u "$BROWSER_USER" -- env HOME="$browser_home" PYTHONPATH="$LIB:$ROOT" \
  POROTA_TECHNICAL_PROBE_TICKER="$PROBE_TICKER" POROTA_TECHNICAL_PROBE_ROUTE="$PROBE_ROUTE" \
  "$CE_PYTHON" "$COLLECTOR" --profile "$PROFILE" --jobs "$JOBS" --output "$stage" \
  >/tmp/porota-point1-collector.$$ 2>&1
collector_rc=$?
set -e
printf 'COLLECTOR_RC=%s\n' "$collector_rc"
if [[ ! -f "$stage" ]]; then
  rm -f /tmp/porota-point1-collector.$$
  stop_safe 'POINT1_CAPTURE_RESULT=RED_CAPTURE_FILE_MISSING'
fi

install -d -m 0750 "$OUTDIR"
install -o root -g root -m 0644 "$stage" "$final"
rm -f "$stage" /tmp/porota-point1-collector.$$

python3 - "$final" <<'PY'
import json,sys
p=sys.argv[1]
d=json.load(open(p,encoding='utf-8'))
ep=d.get('endpoints') or {}
kinds={}; sources=[]
for x in ep.values():
    if not isinstance(x,dict): continue
    k=str(x.get('kind') or '')
    kinds[k]=kinds.get(k,0)+1
    if x.get('source_url'): sources.append(str(x.get('source_url')))
flags={
    'InstrumentosOperables':kinds.get('InstrumentosOperables',0)>0,
    'DatosTecnicos':kinds.get('DatosTecnicos',0)>0,
    'CaucionesOperables':kinds.get('CaucionesOperables',0)>0,
    'ConfiguracionOperatoriaSimplificada':any('ConfiguracionOperatoriaSimplificada' in s for s in sources),
}
blocked=d.get('blocked_nonread') or []
print('CAPTURE_PATH='+p)
print('SCHEMA='+str(d.get('schema')))
print('AUTH_STATUS='+str(d.get('auth_status')))
print('REAL_ORDERS_SENT='+str(d.get('real_orders_sent')))
print('ROUTES_COUNT='+str(len(d.get('routes') or [])))
print('ENDPOINTS_COUNT='+str(len(ep)))
print('ENDPOINT_KINDS='+json.dumps(kinds,sort_keys=True,separators=(',',':')))
print('TARGET_FLAGS='+json.dumps(flags,sort_keys=True,separators=(',',':')))
print('BLOCKED_NONREAD_COUNT='+str(len(blocked)))
print('AMOUNT_FILLED='+str(bool(d.get('amount_filled'))))
print('PRICE_FILLED='+str(bool(d.get('price_filled'))))
structural=(
    d.get('schema')=='POROTA_RC6_PPI_TRUSTED_CONTRACT_V1'
    and d.get('auth_status')=='AUTHENTICATED_TRUSTED_DEVICE'
    and int(d.get('real_orders_sent') or 0)==0
    and isinstance(d.get('jobs'),list) and len(d.get('jobs') or [])>0
    and isinstance(ep,dict) and len(ep)>0
    and len(blocked)==0
    and not d.get('amount_filled') and not d.get('price_filled')
)
target=structural and flags['InstrumentosOperables'] and flags['DatosTecnicos'] and (flags['CaucionesOperables'] or flags['ConfiguracionOperatoriaSimplificada'])
print('STRUCTURALLY_IMPORTABLE='+('YES' if structural else 'NO'))
print('REUSABLE_TARGET='+('YES' if target else 'NO'))
if target:
    print('POINT1_CAPTURE_RESULT=GREEN_VALID_IMPORTABLE_CAPTURE')
elif structural:
    print('POINT1_CAPTURE_RESULT=YELLOW_IMPORTABLE_BUT_TARGET_ENDPOINTS_INCOMPLETE')
elif d.get('auth_status')!='AUTHENTICATED_TRUSTED_DEVICE':
    print('POINT1_CAPTURE_RESULT=RED_AUTH_NOT_TRUSTED_AFTER_REAUTH')
else:
    print('POINT1_CAPTURE_RESULT=RED_CAPTURE_NOT_IMPORTABLE')
PY

postflight
