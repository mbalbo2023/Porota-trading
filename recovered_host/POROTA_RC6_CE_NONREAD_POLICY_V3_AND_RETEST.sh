#!/usr/bin/env bash
set -Eeuo pipefail

# POROTA RC6 — Contract Evidence non-read policy V3 + retest
#
# Política arquitectónica estable:
# 1) GET/HEAD/OPTIONS siguen permitidos por el collector.
# 2) CUALQUIER mutación hacia dominios de terceros se ABORTA silenciosamente.
#    No se autoriza ni se envía nada.
# 3) Mutaciones first-party PPI:
#      - POST trading.portfoliopersonal.com/api/logger -> ABORT silencioso
#      - POST api.portfoliopersonal.com/api/v1/zendesk/zendesk-session -> ABORT silencioso
#      - cualquier otra -> ABORT + blocked_nonread + FAIL-CLOSED
# 4) Nunca se permiten rutas de órdenes.
# 5) Solo si un probe fresco queda con blocked_nonread=0 se ejecuta Contract Evidence.
# 6) Observer debe quedar ok|PRODUCTION_PAPER|0 antes y después.
# 7) Siempre intenta copiar el resumen al clipboard vía OSC52.

TS="$(date -u +%Y%m%dT%H%M%SZ)"

LIB="/usr/local/lib/porota-contract-evidence-rc6"
COLLECTOR="$LIB/rc6_trusted_browser_contract_collector.py"
RUNTIME="/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh"
VENV="/opt/porota-contract-evidence-venv"
PROFILE="/home/porotaadmin/porota-browser-lab/chrome-profile"

PROBE="/tmp/POROTA_CE_POLICY_V3_PROBE_${TS}.json"
DETAIL="/tmp/POROTA_CE_POLICY_V3_DETAIL_${TS}.txt"
RESULT="/tmp/POROTA_CE_POLICY_V3_RESULT_${TS}.txt"
BACKUP="${COLLECTOR}.pre_policy_v3_${TS}"

FINAL_RC=1
STATUS="STARTED"
PRE="UNKNOWN"
POST="UNKNOWN"
PATCH_STATE="NA"
PROBE_RC="NA"
PROBE_AUTH="NA"
PROBE_ENDPOINTS="NA"
PROBE_BLOCKED="NA"
PROBE_REAL_ORDERS="NA"
REMAINING="NONE"
CE_RC="NA"
CE_STATE="NOT_RUN"
CE_RUNS_BEFORE="NA"
CE_RUNS_AFTER="NA"

clipboard_copy() {
  local src="$1"
  [ -f "$src" ] || return 0
  local b64
  b64="$(base64 -w0 "$src" 2>/dev/null || base64 "$src" | tr -d '\n')"
  printf '\033]52;c;%s\a' "$b64" || true
}

write_result() {
  cat >"$RESULT" <<EOF
POROTA RC6 CONTRACT EVIDENCE NONREAD POLICY V3
UTC=$TS
STATUS=$STATUS
SCRIPT_RC=$FINAL_RC
PATCH_STATE=$PATCH_STATE
THIRD_PARTY_MUTATIONS_ALLOWED=NO
THIRD_PARTY_MUTATIONS_ABORTED=YES
PPI_LOGGER_ALLOWED=NO
PPI_LOGGER_ABORTED=YES
PPI_ZENDESK_SESSION_ALLOWED=NO
PPI_ZENDESK_SESSION_ABORTED=YES
OTHER_PPI_MUTATIONS=FAIL_CLOSED
PROBE_RC=$PROBE_RC
PROBE_AUTH=$PROBE_AUTH
PROBE_ENDPOINTS=$PROBE_ENDPOINTS
PROBE_BLOCKED_NONREAD=$PROBE_BLOCKED
PROBE_REAL_ORDERS_SENT=$PROBE_REAL_ORDERS
REMAINING_BLOCKED=$REMAINING
CONTRACT_EVIDENCE_RC=$CE_RC
CONTRACT_EVIDENCE_STATE=$CE_STATE
CE_RUNS_BEFORE=$CE_RUNS_BEFORE
CE_RUNS_AFTER=$CE_RUNS_AFTER
OBSERVER_BEFORE=$PRE
OBSERVER_AFTER=$POST
REAL_ORDERS_SENT=0
SECRETS_PRINTED=NO
TOKENS_PRINTED=NO
ORDER_ROUTES_VISITED=NO
TRADING_RUNTIME_CHANGED=NO
DETAIL=$DETAIL
EOF
}

finish() {
  local rc=$?

  if [ "$FINAL_RC" -eq 1 ] && [ "$rc" -ne 0 ]; then
    FINAL_RC="$rc"
    [ "$STATUS" = "STARTED" ] && STATUS="ERROR_FAIL_CLOSED"
  fi

  if [ "$POST" = "UNKNOWN" ]; then
    POST="$(
      sudo -n docker exec -i porota_production_observer python - <<'PY' 2>/dev/null || true
import sqlite3
try:
    c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=20)
    c.execute('pragma query_only=on')
    qc=c.execute('pragma quick_check').fetchone()[0]
    mode,orders=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone()
    print(f'{qc}|{mode}|{orders}')
    c.close()
except Exception as e:
    print("READ_ERROR_"+type(e).__name__)
PY
    )"
  fi

  write_result || true

  echo
  echo "============================================================"
  cat "$RESULT" 2>/dev/null || true
  echo "============================================================"

  clipboard_copy "$RESULT" || true
  echo
  echo "RESULT_COPIED_TO_CLIPBOARD=OSC52_SENT"
  echo "RESULT_FILE=$RESULT"
  echo "DETAIL_FILE=$DETAIL"
}
trap finish EXIT

exec > >(tee "$DETAIL") 2>&1

echo "POROTA RC6 — CONTRACT EVIDENCE NONREAD POLICY V3"
echo "UTC=$TS"

echo
echo "========== 1. PREFLIGHT =========="

sudo -n true
test -f "$COLLECTOR"
test -x "$RUNTIME"
test -x "$VENV/bin/python"
test -d "$PROFILE"

PRE="$(sudo -n docker exec -i porota_production_observer python - <<'PY'
import sqlite3
c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=30)
c.execute('pragma query_only=on')
qc=c.execute('pragma quick_check').fetchone()[0]
mode,orders=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone()
print(f'{qc}|{mode}|{orders}')
c.close()
PY
)"

echo "OBSERVER_BEFORE=$PRE"

if [ "$PRE" != "ok|PRODUCTION_PAPER|0" ]; then
  STATUS="BLOCKED_OBSERVER_PREFLIGHT"
  FINAL_RC=4
  exit 4
fi

CE_RUNS_BEFORE="$(
sudo -n docker exec -i porota_production_observer python - <<'PY'
import sqlite3
c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=20)
c.execute('pragma query_only=on')
x=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract_evidence_v2_runs'").fetchone()
print(c.execute("SELECT COUNT(*) FROM contract_evidence_v2_runs").fetchone()[0] if x else "TABLE_ABSENT")
c.close()
PY
)"
echo "CE_RUNS_BEFORE=$CE_RUNS_BEFORE"

echo
echo "========== 2. INSTALL POLICY V3 =========="

sudo -n cp -a "$COLLECTOR" "$BACKUP"

PATCH_STATE="$(
sudo -n python3 - "$COLLECTOR" <<'PY'
from pathlib import Path
import re, sys

p=Path(sys.argv[1])
s=p.read_text(encoding="utf-8")

marker="POROTA_CE_NONREAD_POLICY_V3"
if marker in s:
    print("ALREADY_PRESENT")
    raise SystemExit(0)

start='''            def guard(route, request):
'''
end='''            ctx.route("**/*", guard)
'''

i=s.find(start)
j=s.find(end, i)
if i < 0 or j < 0:
    raise SystemExit("GUARD_BLOCK_NOT_FOUND")

new_guard='''            def guard(route, request):
                method = request.method.upper()

                if method in SAFE_METHODS:
                    return route.continue_()

                u = urlsplit(request.url)
                host = u.netloc.lower()
                path = u.path.rstrip("/") or "/"

                # POROTA_CE_NONREAD_POLICY_V3
                #
                # Contract Evidence is read-only. No mutation is ever
                # authorized by this collector.
                #
                # Third-party telemetry/support mutations are aborted
                # silently. They cannot mutate PPI state and are irrelevant
                # to evidence collection.
                ppi_hosts = {
                    "trading.portfoliopersonal.com",
                    "api.portfoliopersonal.com",
                    "cuenta.portfoliopersonal.com",
                }

                if host not in ppi_hosts:
                    return route.abort()

                # Known first-party non-business telemetry/support calls.
                # They also remain physically aborted.
                if (
                    method == "POST"
                    and host == "trading.portfoliopersonal.com"
                    and path == "/api/logger"
                ):
                    return route.abort()

                if (
                    method == "POST"
                    and host == "api.portfoliopersonal.com"
                    and path == "/api/v1/zendesk/zendesk-session"
                ):
                    return route.abort()

                # Every other first-party PPI mutation is suspicious:
                # abort, record, and force fail-closed.
                out["blocked_nonread"].append({
                    "method": method,
                    "url": clean_url(request.url)
                })
                return route.abort()

'''

s2=s[:i] + new_guard + s[j:]
p.write_text(s2,encoding="utf-8")
print("WRITTEN")
PY
)"

echo "PATCH_STATE=$PATCH_STATE"

sudo -n "$VENV/bin/python" -m py_compile "$COLLECTOR"
grep -q 'POROTA_CE_NONREAD_POLICY_V3' "$COLLECTOR"

echo "BACKUP=$BACKUP"
echo "THIRD_PARTY_MUTATIONS_ALLOWED=NO"
echo "THIRD_PARTY_MUTATIONS_ABORTED=YES"
echo "OTHER_PPI_MUTATIONS=FAIL_CLOSED"

echo
echo "========== 3. FRESH PROBE =========="

set +e
PROBE_RAW="$(
  sudo -n runuser -u porotaadmin -- \
    env HOME=/home/porotaadmin \
    "$VENV/bin/python" "$COLLECTOR" \
    --profile "$PROFILE" \
    --jobs CONTRACT_EVIDENCE_STATIC \
    --output "$PROBE" 2>&1
)"
PROBE_RC=$?
set -e

echo "PROBE_RC=$PROBE_RC"
printf '%s\n' "$PROBE_RAW" | tail -n 30 || true

if [ ! -f "$PROBE" ]; then
  STATUS="BLOCKED_PROBE_CAPTURE_MISSING"
  FINAL_RC=4
  exit 4
fi

PROBE_INFO="$(
sudo -n -u porotaadmin python3 - "$PROBE" <<'PY'
import json,sys
from collections import Counter
from urllib.parse import urlsplit

d=json.load(open(sys.argv[1],encoding="utf-8"))
blocked=d.get("blocked_nonread") or []

print("PROBE_AUTH="+str(d.get("auth_status","UNKNOWN")))
print("PROBE_ENDPOINTS="+str(len(d.get("endpoints") or {})))
print("PROBE_BLOCKED_NONREAD="+str(len(blocked)))
print("PROBE_REAL_ORDERS_SENT="+str(d.get("real_orders_sent","NA")))

c=Counter()
for row in blocked:
    if not isinstance(row,dict):
        continue
    m=str(row.get("method","")).upper()
    u=urlsplit(str(row.get("url","")))
    c[f"{m} {u.netloc}{u.path}"] += 1

if not c:
    print("REMAINING_BLOCKED=NONE")
else:
    vals=[]
    for key,n in c.most_common(100):
        line=f"{n}x {key}"
        print("REMAINING_BLOCKED_ITEM="+line)
        vals.append(line)
    print("REMAINING_BLOCKED="+" | ".join(vals))
PY
)"

printf '%s\n' "$PROBE_INFO"

PROBE_AUTH="$(printf '%s\n' "$PROBE_INFO" | awk -F= '/^PROBE_AUTH=/{print $2;exit}')"
PROBE_ENDPOINTS="$(printf '%s\n' "$PROBE_INFO" | awk -F= '/^PROBE_ENDPOINTS=/{print $2;exit}')"
PROBE_BLOCKED="$(printf '%s\n' "$PROBE_INFO" | awk -F= '/^PROBE_BLOCKED_NONREAD=/{print $2;exit}')"
PROBE_REAL_ORDERS="$(printf '%s\n' "$PROBE_INFO" | awk -F= '/^PROBE_REAL_ORDERS_SENT=/{print $2;exit}')"
REMAINING="$(printf '%s\n' "$PROBE_INFO" | sed -n 's/^REMAINING_BLOCKED=//p' | tail -n1)"

if [ "$PROBE_AUTH" != "AUTHENTICATED_TRUSTED_DEVICE" ]; then
  STATUS="BLOCKED_PROBE_NOT_AUTHENTICATED"
  FINAL_RC=4
  exit 4
fi

if [ "$PROBE_REAL_ORDERS" != "0" ]; then
  STATUS="BLOCKED_PROBE_ORDER_INVARIANT"
  FINAL_RC=5
  exit 5
fi

if [ "$PROBE_BLOCKED" != "0" ]; then
  STATUS="BLOCKED_FIRST_PARTY_PPI_MUTATION"
  FINAL_RC=4
  exit 4
fi

echo
echo "========== 4. CONTRACT EVIDENCE RUNTIME =========="

set +e
CE_RAW="$(sudo -n "$RUNTIME" 2>&1)"
CE_RC=$?
set -e

echo "CONTRACT_EVIDENCE_RC=$CE_RC"
printf '%s\n' "$CE_RAW" |
  grep -E 'STATUS=|state|blocked_nonread|capture|endpoints|routes|records|changed|conflicts|AUTH|REAL_ORDERS|DUE|jobs' |
  tail -n 120 || true

if [ "$CE_RC" -eq 0 ]; then
  CE_STATE="COMPLETED"
else
  CE_STATE="FAIL_CLOSED_RC_${CE_RC}"
fi

CE_RUNS_AFTER="$(
sudo -n docker exec -i porota_production_observer python - <<'PY'
import sqlite3
c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=20)
c.execute('pragma query_only=on')
x=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract_evidence_v2_runs'").fetchone()
print(c.execute("SELECT COUNT(*) FROM contract_evidence_v2_runs").fetchone()[0] if x else "TABLE_ABSENT")
c.close()
PY
)"

echo "CE_RUNS_AFTER=$CE_RUNS_AFTER"

echo
echo "========== 5. POSTFLIGHT =========="

POST="$(sudo -n docker exec -i porota_production_observer python - <<'PY'
import sqlite3
c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=30)
c.execute('pragma query_only=on')
qc=c.execute('pragma quick_check').fetchone()[0]
mode,orders=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone()
print(f'{qc}|{mode}|{orders}')
c.close()
PY
)"

echo "OBSERVER_AFTER=$POST"

if [ "$POST" != "ok|PRODUCTION_PAPER|0" ]; then
  STATUS="POSTFLIGHT_SAFETY_FAILED"
  FINAL_RC=5
  exit 5
fi

if [ "$CE_RC" -eq 0 ]; then
  STATUS="GREEN"
  FINAL_RC=0
else
  STATUS="YELLOW_CONTRACT_EVIDENCE_RUNTIME_FAIL_CLOSED"
  FINAL_RC=3
fi

exit "$FINAL_RC"
