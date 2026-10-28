#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_VERSION="17.0.0-rc3-hf6"
EXPECTED_IMAGE_PREFIX="porota-trading-bot:17.0.0-rc3-hf6"
PATCH_BRANCH="patch/hf6-contract-evidence"
REQUIRED_ANCESTOR="b8f3992da559cf3e21339fc1eaac688d5b455c68"
REPO="/opt/porota-trading"
PATCH_ROOT="/opt/porota-runtime-patches/hf6-contract-evidence"
DB="$REPO/data/paper_v17/observer_v17.db"
WEB_JSON="$REPO/data/contract_evidence/ppi_web_all_families/latest.json"
OBS="porota_production_observer"
DASH="porota_production_dashboard"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/home/porotaadmin/porota_hf6_contract_patch_FINALIZER_${TS}.txt"
STAGE="/opt/porota-staging/hf6-contract-finalizer-$TS"

exec > >(tee "$OUT.tmp") 2>&1
cleanup(){ set +e; sudo -n rm -rf "$STAGE" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "================================================================"
echo " POROTA HF6 - CONTRACT PATCH FINALIZER"
echo "================================================================"
date -u

echo
echo "========== 1. PRECHECK =========="
HEALTH="$(curl -fsS http://127.0.0.1:8000/health)"
echo "$HEALTH"
echo "$HEALTH" | grep -q "\"version\":\"$EXPECTED_VERSION\"" || { echo "ABORTADO=VERSION_MISMATCH"; exit 20; }
OBS_STATE="$(sudo -n docker inspect --format='{{.State.Status}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}|{{.State.StartedAt}}' "$OBS")"
DASH_STATE="$(sudo -n docker inspect --format='{{.State.Status}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}|{{.State.StartedAt}}' "$DASH")"
echo "OBSERVER_PRE=$OBS_STATE"
echo "DASHBOARD_PRE=$DASH_STATE"
IFS='|' read -r OBS_STATUS OBS_RO OBS_IMAGE OBS_STARTED <<<"$OBS_STATE"
[ "$OBS_STATUS" = "running" ] || { echo "ABORTADO=OBSERVER_NOT_RUNNING"; exit 21; }
[ "$OBS_RO" = "true" ] || { echo "ABORTADO=OBSERVER_NOT_READONLY"; exit 22; }
case "$OBS_IMAGE" in "$EXPECTED_IMAGE_PREFIX"*) ;; *) echo "ABORTADO=IMAGE_MISMATCH"; exit 23;; esac
python3 - "$DB" <<'PY'
import sqlite3,sys
c=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro",uri=True)
print("QUICK_CHECK_PRE="+str(c.execute("PRAGMA quick_check").fetchone()[0]))
r=c.execute("SELECT mode,process_state,session_state,ppi_auth,real_orders_sent FROM observer_state WHERE id=1").fetchone()
print("OBSERVER_STATE_PRE="+repr(r))
if not r or r[0] != "PRODUCTION_PAPER" or int(r[-1] or 0) != 0: raise SystemExit(3)
c.close()
PY

echo
echo "========== 2. FETCH CORRECCIONES GITHUB =========="
cd "$REPO"
git fetch --quiet origin "$PATCH_BRANCH"
PATCH_HEAD="$(git rev-parse "origin/$PATCH_BRANCH")"
echo "PATCH_HEAD=$PATCH_HEAD"
git merge-base --is-ancestor "$REQUIRED_ANCESTOR" "$PATCH_HEAD" || { echo "ABORTADO=PATCH_BEHIND_REQUIRED_FIX"; exit 30; }
sudo -n install -d -m 0755 -o porotaadmin -g porotaadmin /opt/porota-staging
mkdir -p "$STAGE"
git archive "origin/$PATCH_BRANCH" | tar -x -C "$STAGE"
for f in scripts/porota_ppi_authenticated_all_families_hf6.sh deploy/systemd/porota-contract-evidence-dashboard-hf6.service scripts/porota_contract_evidence_dashboard_overlay_hf6.sh cn_ppi_authenticated_family_scraper_hf6.py; do
  test -s "$STAGE/$f" || { echo "ABORTADO=MISSING:$f"; exit 31; }
done

echo
echo "========== 3. INSTALAR CORRECCIONES =========="
sudo -n install -d -m 0755 "$PATCH_ROOT"
sudo -n install -m 0755 "$STAGE/scripts/porota_ppi_authenticated_all_families_hf6.sh" /usr/local/sbin/porota-ppi-authenticated-all-families-hf6.sh
sudo -n install -m 0755 "$STAGE/scripts/porota_contract_evidence_dashboard_overlay_hf6.sh" /usr/local/sbin/porota-contract-evidence-dashboard-overlay-hf6.sh
sudo -n install -m 0644 "$STAGE/cn_ppi_authenticated_family_scraper_hf6.py" "$PATCH_ROOT/cn_ppi_authenticated_family_scraper_hf6.py"
sudo -n install -m 0644 "$STAGE/deploy/systemd/porota-contract-evidence-dashboard-hf6.service" /etc/systemd/system/porota-contract-evidence-dashboard-hf6.service
sudo -n systemctl daemon-reload
echo "FIX_CREDENTIAL_BOUND_NAME=INSTALLED"
echo "FIX_DASHBOARD_EXECSTART_PATH=INSTALLED"

echo
echo "========== 4. AUTH READ-ONLY TODAS LAS FAMILIAS =========="
set +e
sudo -n /usr/local/sbin/porota-ppi-authenticated-all-families-hf6.sh
WEB_RC=$?
set -e
echo "AUTH_COLLECTOR_RC=$WEB_RC"
AUTH_STATUS="NO_EVIDENCE_FILE"
if sudo -n test -s "$WEB_JSON"; then
  AUTH_STATUS="$(sudo -n python3 - "$WEB_JSON" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
s=d.get('safety') or {}
if int(s.get('order_posts',0) or 0)!=0: raise SystemExit(41)
if int(s.get('mutation_requests',0) or 0)!=0: raise SystemExit(42)
print((d.get('auth') or {}).get('status','UNKNOWN'))
PY
)"
fi
echo "AUTH_STATUS=$AUTH_STATUS"

echo
echo "========== 5. MERGE EVIDENCIA WEB SANITIZADA =========="
if sudo -n test -s "$WEB_JSON"; then
sudo -n python3 - "$DB" "$WEB_JSON" <<'PY'
import json,sqlite3,sys
from datetime import datetime,timezone
db,web=sys.argv[1:3]
d=json.load(open(web,encoding='utf-8'))
checked=d.get('observed_at') or datetime.now(timezone.utc).isoformat()
auth=str((d.get('auth') or {}).get('status','UNKNOWN'))
families=d.get('families') or {}
c=sqlite3.connect(db,timeout=30); c.execute('PRAGMA busy_timeout=30000')
c.execute('''CREATE TABLE IF NOT EXISTS contract_evidence(
 instrument_type TEXT NOT NULL,ticker TEXT NOT NULL,market TEXT NOT NULL,status TEXT NOT NULL,
 owner TEXT NOT NULL,source TEXT NOT NULL,checked_at TEXT NOT NULL,missing_fields_json TEXT NOT NULL,
 evidence_json TEXT NOT NULL,detail TEXT NOT NULL,PRIMARY KEY(instrument_type,ticker,market))''')
merged=0
for fam,info in sorted(families.items()):
    status=str(info.get('status') or 'UNKNOWN')
    sources=[]
    for x in (info.get('sources') or [])[:20]:
        sources.append({k:x.get(k) for k in ('route','http','final_url','authenticated_target_reached','title','table_count','detected_fields','tables','error')})
    if status=='AUTHENTICATED_WEB_EVIDENCE_COLLECTED_REVIEW_REQUIRED':
        owner='POROTA_CONTRACT_VALIDATION'; missing=['provider_field_semantics_validation','family_specific_paper_executor_validation']; detail='Authenticated PPI read-only evidence collected. No automatic READY_PAPER promotion.'
    elif auth=='TWO_FACTOR_REQUIRED_FAIL_CLOSED':
        owner='PPI_AUTH_2FA_OR_SUPPORT'; missing=['authenticated_provider_evidence']; detail='PPI required 2FA; collector stopped fail-closed. No bypass and no mutation/order requests.'
    else:
        owner='PPI_SUPPORT_OR_POROTA_DISCOVERY'; missing=['provider_backed_contract_evidence']; detail='No complete authenticated contract evidence obtained; family remains fail-closed.'
    evidence={'schema':d.get('schema'),'auth_status':auth,'automatic_ready_paper':False,'sources':sources}
    c.execute('''INSERT INTO contract_evidence VALUES(?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(instrument_type,ticker,market) DO UPDATE SET status=excluded.status,owner=excluded.owner,
      source=excluded.source,checked_at=excluded.checked_at,missing_fields_json=excluded.missing_fields_json,
      evidence_json=excluded.evidence_json,detail=excluded.detail''',
      (str(fam).upper(),'*','WEB',status,owner,'PPI_AUTHENTICATED_WEB_READ_ONLY',checked,
       json.dumps(missing,ensure_ascii=False,sort_keys=True),json.dumps(evidence,ensure_ascii=False,sort_keys=True,default=str),detail))
    merged += 1
c.commit(); print('WEB_EVIDENCE_ROWS_MERGED='+str(merged)); print('QUICK_CHECK_AFTER_MERGE='+c.execute('PRAGMA quick_check').fetchone()[0]); c.close()
PY
else
  echo "WEB_EVIDENCE_ROWS_MERGED=0"
fi

echo
echo "========== 6. APLICAR OVERLAY DASHBOARD =========="
sudo -n systemctl reset-failed porota-contract-evidence-dashboard-hf6.service 2>/dev/null || true
set +e
sudo -n systemctl start porota-contract-evidence-dashboard-hf6.service
DASH_RC=$?
set -e
echo "DASHBOARD_OVERLAY_RC=$DASH_RC"
sudo -n systemctl --no-pager --full status porota-contract-evidence-dashboard-hf6.service 2>/dev/null | tail -n 20 || true
for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 1; done
if sudo -n docker exec "$DASH" grep -q 'import cl_contract_evidence_dashboard_hf6' /app/o_dashboard.py; then echo "DASHBOARD_CONTRACT_IMPORT=OK"; else echo "DASHBOARD_CONTRACT_IMPORT=MISSING"; fi

echo
echo "========== 7. VALIDACION FINAL =========="
FINAL_HEALTH="$(curl -fsS http://127.0.0.1:8000/health)"
echo "$FINAL_HEALTH"
FINAL_OBS="$(sudo -n docker inspect --format='{{.State.Status}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}|{{.State.StartedAt}}' "$OBS")"
FINAL_DASH="$(sudo -n docker inspect --format='{{.State.Status}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}|{{.State.StartedAt}}' "$DASH")"
echo "OBSERVER_FINAL=$FINAL_OBS"
echo "DASHBOARD_FINAL=$FINAL_DASH"
IFS='|' read -r FOS FOR FOI FOSTART <<<"$FINAL_OBS"
[ "$FOS" = "running" ] || { echo "FINAL_STATUS=FAIL_OBSERVER"; exit 50; }
[ "$FOR" = "true" ] || { echo "FINAL_STATUS=FAIL_READONLY"; exit 51; }
[ "$FOSTART" = "$OBS_STARTED" ] || { echo "FINAL_STATUS=FAIL_OBSERVER_RESTARTED"; exit 52; }
python3 - "$DB" <<'PY'
import sqlite3,sys
c=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro",uri=True)
print('QUICK_CHECK_FINAL='+c.execute('PRAGMA quick_check').fetchone()[0])
r=c.execute('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent FROM observer_state WHERE id=1').fetchone()
print('OBSERVER_STATE_FINAL='+repr(r))
if not r or r[0] != 'PRODUCTION_PAPER' or int(r[-1] or 0)!=0: raise SystemExit(4)
for fam,status,owner in c.execute('SELECT instrument_type,status,owner FROM contract_evidence WHERE market="WEB" ORDER BY instrument_type'):
    print(f'WEB_FAMILY={fam}|{status}|{owner}')
c.close()
PY

FINAL="PATCH_COMPLETED"
[ "$DASH_RC" -eq 0 ] || FINAL="PATCH_COMPLETED_DASHBOARD_OVERLAY_FAILED"
[ "$WEB_RC" -eq 0 ] || FINAL="PATCH_COMPLETED_AUTH_COLLECTOR_FAILED"
if [ "$WEB_RC" -ne 0 ] && [ "$DASH_RC" -ne 0 ]; then FINAL="PATCH_CORE_SAFE_BUT_AUXILIARY_FAILURES_REMAIN"; fi

echo
echo "========== 8. RESUMEN =========="
echo "FINAL_STATUS=$FINAL"
echo "AUTH_STATUS=$AUTH_STATUS"
echo "REAL_ORDERS_SENT=0"
echo "OBSERVER_RESTARTED=NO"
echo "OBSERVER_READONLY_PRESERVED=SI"
echo "HF6_TAG_MOVED=NO"
echo "MAIN_MERGED=NO"
echo "UNRESOLVED_FAMILIES_FAIL_CLOSED=SI"
echo "FULL_LOG=$OUT"
echo "WEB_EVIDENCE=$WEB_JSON"

exec 1>&-
exec 2>&-
mv -f "$OUT.tmp" "$OUT"
chmod 600 "$OUT"
exec >/dev/tty 2>/dev/tty || true
printf '\nFINAL_STATUS=%s\nAUTH_STATUS=%s\nREAL_ORDERS_SENT=0\nOBSERVER_RESTARTED=NO\nFULL_LOG=%s\n' "$FINAL" "$AUTH_STATUS" "$OUT"
SUMMARY="FINAL_STATUS=$FINAL
AUTH_STATUS=$AUTH_STATUS
REAL_ORDERS_SENT=0
OBSERVER_RESTARTED=NO
FULL_LOG=$OUT"
printf '\033]52;c;%s\007' "$(printf '%s' "$SUMMARY" | base64 -w0)" || true
echo "RESUMEN_COPIADO_AL_PORTAPAPELES=SI"
echo "TERMINAL_PERMANECE_ABIERTA=SI"
true
