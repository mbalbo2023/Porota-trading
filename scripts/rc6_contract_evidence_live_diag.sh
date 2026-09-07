#!/usr/bin/env bash
set -euo pipefail
ROOT=/opt/porota-trading
UNIT=porota-contract-evidence-rc6.service
TIMER=porota-contract-evidence-rc6.timer
LIB=/usr/local/lib/porota-contract-evidence-rc6
OUT="$ROOT/data/contract_evidence/rc6_trusted"
DB="$ROOT/data/paper_v17/observer_v17.db"
echo '=== RC6 CONTRACT EVIDENCE READONLY DIAGNOSTIC ==='
echo "UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "LOCAL=$(TZ=America/Argentina/Buenos_Aires date +%Y-%m-%dT%H:%M:%S%z)"
echo "TIMER_ENABLED=$(systemctl is-enabled "$TIMER" 2>/dev/null || true)"
echo "TIMER_ACTIVE=$(systemctl is-active "$TIMER" 2>/dev/null || true)"
echo "SERVICE_ACTIVE=$(systemctl is-active "$UNIT" 2>/dev/null || true)"
systemctl show "$TIMER" -p LastTriggerUSec -p NextElapseUSecRealtime --no-pager || true
systemctl show "$UNIT" -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp --no-pager || true
echo "RUNTIME_SCRIPT=$(test -x /usr/local/sbin/porota-contract-evidence-rc6-runtime.sh && echo PRESENT_EXECUTABLE || echo MISSING)"
if [ -r /usr/local/sbin/porota-contract-evidence-rc6-runtime.sh ]; then sha256sum /usr/local/sbin/porota-contract-evidence-rc6-runtime.sh | awk '{print "RUNTIME_SHA256=" $1}'; fi
echo "LIB_PRESENT=$(test -d "$LIB" && echo YES || echo NO)"
for f in rc6_contract_due_job.py rc6_contract_schedule.py rc6_trusted_browser_contract_collector.py rc6_contract_capture_importer.py; do echo "COMPONENT_${f}=$(test -r "$LIB/$f" && echo PRESENT || echo MISSING)"; done
echo "PROFILE_DIR=$(test -d /home/porotaadmin/porota-browser-lab/chrome-profile && echo PRESENT || echo MISSING)"
echo "OUTDIR=$(test -d "$OUT" && echo PRESENT || echo MISSING)"
if [ -d "$OUT" ]; then find "$OUT" -maxdepth 1 -type f -printf '%TY-%Tm-%TdT%TH:%TM:%TSZ|%f|%s\n' 2>/dev/null | sort -r | head -n 10 | sed 's/^/CAPTURE_FILE=/' || true; fi
if [ -s "$OUT/runtime_state.json" ]; then python3 -c "import json;d=json.load(open('$OUT/runtime_state.json'));print('AUTH_BACKOFF_STATE='+str(d.get('state','UNKNOWN')));print('AUTH_BLOCKED_AT='+str(d.get('blocked_at','UNKNOWN')))" || echo AUTH_BACKOFF_STATE=INVALID_STATE_FILE; else echo AUTH_BACKOFF_STATE=NONE; fi
if [ -r "$LIB/rc6_contract_due_job.py" ] && [ -f "$DB" ]; then echo -n 'DUE_POLICY='; PYTHONPATH="$LIB:$ROOT" PAPER_V17_DB_PATH="$DB" python3 "$LIB/rc6_contract_due_job.py" || true; fi
python3 -c "import sqlite3;p='$DB';c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');print('DB_QUICK_CHECK='+str(c.execute('PRAGMA quick_check').fetchone()[0]));names={r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")};print('CE_RUN_TABLE='+('YES' if 'contract_evidence_v2_runs' in names else 'NO'));r=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone();print('OBSERVER_STATE='+('|'.join(map(str,r)) if r else 'MISSING'));print('CE_RUN_COUNT='+str(c.execute('SELECT COUNT(*) FROM contract_evidence_v2_runs').fetchone()[0]) if 'contract_evidence_v2_runs' in names else 'CE_RUN_COUNT=NA');c.close()" || true
echo 'SAFE_JOURNAL_BEGIN'
journalctl -u "$UNIT" --since '2026-09-07 00:00:00' --no-pager -o cat 2>/dev/null \
  | grep -E '^(STATUS=|REASON=|DUE_JOBS=|AUTH_STATUS=|AUTH_BROWSER_STARTED=|PPI_CALLS=|IMPORT_RC=|OBSERVER_READONLY=|DB_QUICK_CHECK=|REAL_ORDERS_SENT=|T1_|CAPTURE_)' \
  | tail -n 80 || true
echo 'SAFE_JOURNAL_END'
echo MUTATION=NO
