#!/usr/bin/env bash
set -u
umask 077

ROOT="/opt/porota-trading"
SOURCE_BRANCH="patch/hf6-contract-evidence-v2-wip"
SOURCE_SHA_EXPECTED="a03cf47d5db01d8990a99f3f7836879c0606771b"
CANDIDATE_VERSION="17.0.0-rc3-hf6-v2-candidate1"
IMAGE="porota-trading-bot:${CANDIDATE_VERSION}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
WT="/opt/porota-staging/hf6-v2-release-${TS}"
CTX="/opt/porota-staging/hf6-v2-context-${TS}"
REPORT="/home/porotaadmin/porota_hf6_v2_candidate_build_${TS}.txt"
MANIFEST="/home/porotaadmin/porota_hf6_v2_candidate_manifest_${TS}.sha256"
SUMMARY="/tmp/porota_hf6_v2_candidate_summary_${TS}.txt"

clip(){ local f="$1" b64; b64="$(base64 -w0 "$f" 2>/dev/null || base64 "$f" | tr -d '\n')"; printf '\033]52;c;%s\a' "$b64"; }
observer_state(){ sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}' porota_production_observer 2>/dev/null || true; }
PRE="$(observer_state)"
PATCH_RC=99; SYNTAX_RC=99; SOURCE_TEST_RC=99; BUILD_RC=99; IMAGE_TEST_RC=99; SECURITY_RC=99; IMAGE_SECURITY_RC=99; DIFF_RC=99
IMAGE_ID=""; SOURCE_SHA=""; WT_CREATED=0
cleanup(){ if [ "$WT_CREATED" -eq 1 ]; then git -C "$ROOT" worktree remove --force "$WT" >/dev/null 2>&1 || true; fi; rm -rf "$CTX" 2>/dev/null || true; }
trap cleanup EXIT

{
 echo "============================================================"
 echo " POROTA HF6 V2 - RELEASE CANDIDATE BUILDER"
 echo " BUILD ONLY - NO DEPLOY - NO REAL ORDERS"
 echo "============================================================"
 echo "UTC=$(date -u -Is)"
 echo "SOURCE_BRANCH=$SOURCE_BRANCH"
 echo "SOURCE_SHA_EXPECTED=$SOURCE_SHA_EXPECTED"
 echo "CANDIDATE_VERSION=$CANDIDATE_VERSION"
 echo "CANDIDATE_IMAGE=$IMAGE"
 echo "OBSERVER_PRE=${PRE:-UNAVAILABLE}"

 echo "========== ACTIVE RUNTIME SAFETY =========="
 sudo -n python3 - <<'PY'
import sqlite3
p='/opt/porota-trading/data/paper_v17/observer_v17.db'
try:
 c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=10)
 print('DB_QUICK_CHECK='+str(c.execute('PRAGMA quick_check').fetchone()[0]))
 try:
  r=c.execute('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent FROM observer_state WHERE id=1').fetchone()
  print('OBSERVER_STATE='+'|'.join(map(str,r)) if r else 'OBSERVER_STATE=NO_ROW')
 except Exception as e: print('OBSERVER_STATE_READ_ERROR='+type(e).__name__)
 c.close()
except Exception as e: print('DB_READ_ERROR='+type(e).__name__)
PY

 echo "========== FROZEN SOURCE =========="
 if git -C "$ROOT" fetch --quiet origin "$SOURCE_BRANCH"; then SOURCE_SHA="$(git -C "$ROOT" rev-parse FETCH_HEAD 2>/dev/null || true)"; echo SOURCE_FETCH=OK; echo "SOURCE_SHA=$SOURCE_SHA"; else echo SOURCE_FETCH=FAILED; fi
 if [ "$SOURCE_SHA" != "$SOURCE_SHA_EXPECTED" ]; then echo SOURCE_FREEZE=FAILED; else
  echo SOURCE_FREEZE=OK
  sudo -n install -d -m 0755 -o porotaadmin -g porotaadmin /opt/porota-staging
  rm -rf "$WT" "$CTX" 2>/dev/null || true
  if git -C "$ROOT" worktree add --detach "$WT" "$SOURCE_SHA" >/dev/null 2>&1; then WT_CREATED=1; echo WORKTREE=OK; else echo WORKTREE=FAILED; fi
 fi

 if [ "$WT_CREATED" -eq 1 ]; then
  echo "========== MATERIALIZE VALIDATED PATCHES =========="
  PATCH_RC=0
  run_patch(){ local label="$1"; shift; "$@" >/tmp/porota_patch_${label}_${TS}.log 2>&1; local rc=$?; echo "${label}_RC=$rc"; if [ "$rc" -ne 0 ]; then PATCH_RC=1; tail -n 40 /tmp/porota_patch_${label}_${TS}.log || true; fi; rm -f /tmp/porota_patch_${label}_${TS}.log; return 0; }
  run_patch UX_LOGS bash "$WT/scripts/porota_apply_dashboard_ux_logs_hf6.sh" "$WT"
  run_patch SCHEDULER bash "$WT/scripts/porota_apply_scheduler_dashboard_hf6.sh" "$WT"
  run_patch DAILY_RESPONSIVE bash "$WT/scripts/porota_apply_daily_results_responsive_hf6.sh" "$WT" --apply
  run_patch HISTORY_DASHBOARD bash "$WT/scripts/porota_apply_history_dashboard_metrics_hf6.sh" "$WT"
  run_patch DYNAMIC_RISK bash "$WT/scripts/porota_apply_dynamic_risk_gate_hf6.sh" "$WT" --apply

  python3 - "$WT/cu_history_store_v2_hf6.py" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); t=p.read_text(encoding='utf-8')
if '"A3_CEM_CLOSING": 20,' not in t:
 a='    "BYMA": 20,\n    "IOL": 30,\n'
 if t.count(a)!=1: raise SystemExit('A3_CEM_SOURCE_RANK_ANCHOR_FAILED')
 t=t.replace(a,'    "BYMA": 20,\n    "A3_CEM_CLOSING": 20,\n    "IOL": 30,\n',1)
p.write_text(t,encoding='utf-8')
PY
  rc=$?; echo "A3_CEM_SOURCE_RANK_RC=$rc"; [ "$rc" -eq 0 ] || PATCH_RC=1

  python3 - "$WT/.env.example" "$WT/v_config_metadata.py" <<'PY'
from pathlib import Path
import sys
ep,mp=map(Path,sys.argv[1:]); e=ep.read_text(encoding='utf-8'); m=mp.read_text(encoding='utf-8')
for old,new in [
('# Hora de apertura de rueda (0-23, hora Argentina).\nMARKET_OPEN_HOUR=11','# LEGACY: hora global del motor histórico. HF6-v2 usa sesiones por familia/mercado/fecha.\nMARKET_OPEN_HOUR=11'),
('# Hora de cierre de rueda.\nMARKET_CLOSE_HOUR=17','# LEGACY: cierre global del motor histórico. HF6-v2 usa sesiones por familia/mercado/fecha.\nMARKET_CLOSE_HOUR=17'),
('# Operaciones simultáneas permitidas.\nMAX_OPEN_POSITIONS=3','# LEGACY: cap histórico. HF6-v2 usa riesgo concurrente dinámico y cap técnico anti-runaway.\nMAX_OPEN_POSITIONS=3')]:
 if new not in e:
  if e.count(old)!=1: raise SystemExit('ENV_LEGACY_ANCHOR_FAILED')
  e=e.replace(old,new,1)
for old,new in [
('(\"MARKET_OPEN_HOUR\", \"Horarios\", \"Hora de apertura de rueda (0-23, hora Argentina).\", \"11\", False)','(\"MARKET_OPEN_HOUR\", \"Horarios\", \"LEGACY: hora global histórica; HF6-v2 usa sesiones versionadas por familia/mercado/fecha.\", \"11\", False)'),
('(\"MARKET_CLOSE_HOUR\", \"Horarios\", \"Hora de cierre de rueda.\", \"17\", False)','(\"MARKET_CLOSE_HOUR\", \"Horarios\", \"LEGACY: cierre global histórico; HF6-v2 usa sesiones versionadas por familia/mercado/fecha.\", \"17\", False)'),
('(\"MAX_OPEN_POSITIONS\", \"Riesgo\", \"Operaciones simultáneas permitidas.\", \"3\", False)','(\"MAX_OPEN_POSITIONS\", \"Riesgo\", \"LEGACY: cap histórico; HF6-v2 usa riesgo concurrente dinámico y cap técnico anti-runaway.\", \"3\", False)')]:
 if new not in m:
  if m.count(old)!=1: raise SystemExit('META_LEGACY_ANCHOR_FAILED')
  m=m.replace(old,new,1)
ep.write_text(e,encoding='utf-8'); mp.write_text(m,encoding='utf-8')
PY
  rc=$?; echo "LEGACY_CONFIG_LABELS_RC=$rc"; [ "$rc" -eq 0 ] || PATCH_RC=1

  python3 - "$WT/_version.py" "$CANDIDATE_VERSION" "$SOURCE_SHA" <<'PY'
from pathlib import Path
import re,sys
p=Path(sys.argv[1]); v=sys.argv[2]; s=sys.argv[3]; t=p.read_text(encoding='utf-8')
t,n=re.subn(r'^VERSION\s*=\s*"[^"]+"',f'VERSION = "{v}"',t,count=1,flags=re.M)
if n!=1: raise SystemExit('VERSION_ANCHOR_FAILED')
if 'RELEASE_SOURCE_COMMIT' not in t: t+=f'\nRELEASE_SOURCE_COMMIT = "{s}"\n'
p.write_text(t,encoding='utf-8')
PY
  rc=$?; echo "CANDIDATE_VERSION_PATCH_RC=$rc"; [ "$rc" -eq 0 ] || PATCH_RC=1
  echo "PATCHERS_COMBINED_RC=$PATCH_RC"

  echo "========== STATIC RELEASE INVARIANTS =========="
  python3 - "$WT" <<'PY'
from pathlib import Path
import ast,importlib.util,sys
r=Path(sys.argv[1]); be=(r/'be_paper_engine.py').read_text(encoding='utf-8'); bg=(r/'bg_paper_dashboard.py').read_text(encoding='utf-8')
t=ast.parse(be); src=''
for n in ast.walk(t):
 if isinstance(n,ast.FunctionDef) and n.name=='_open': src=ast.get_source_segment(be,n) or ''; break
print('LEGACY_MAX_POSITIONS_GATE_PRESENT='+('YES' if 'self.max_positions' in src else 'NO'))
print('CONCURRENT_RISK_LOCKED_PRESENT='+('YES' if 'concurrent_risk_locked' in src else 'NO'))
print('EMERGENCY_CAP_PRESENT='+('YES' if 'PAPER_EMERGENCY_MAX_OPEN_POSITIONS' in src else 'NO'))
print('DASHBOARD_SCHEDULER_PRESENT='+('YES' if '("scheduler", "Scheduler")' in bg else 'NO'))
print('DASHBOARD_DAILY_RESULTS_PRESENT='+('YES' if 'daily_results_html(days)' in bg else 'NO'))
print('DASHBOARD_HISTORY_DYNAMIC_PRESENT='+('YES' if 'observer_history_metrics' in bg else 'NO'))
print('DASHBOARD_DOCKER_SOCKET_PRESENT='+('YES' if 'docker.sock' in bg else 'NO'))
spec=importlib.util.spec_from_file_location('hist',r/'cu_history_store_v2_hf6.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print('A3_CEM_CLOSING_SOURCE_RANK='+str(m.SOURCE_RANK.get('A3_CEM_CLOSING'))); print('PPI_HISTORY_SOURCE_RANK='+str(m.SOURCE_RANK.get('PPI_PRODUCTION_HISTORY')))
assert m.SOURCE_RANK.get('A3_CEM_CLOSING')==20 and m.SOURCE_RANK['PPI_PRODUCTION_HISTORY']<20
PY
  STATIC_RC=$?; echo "STATIC_RELEASE_INVARIANTS_RC=$STATIC_RC"; [ "$STATIC_RC" -eq 0 ] || PATCH_RC=1

  echo "========== NO-WRITE SYNTAX =========="
  sudo -n docker run --rm --network=none -v "$WT:/app:ro" -w /app porota-trading-bot:17.0.0-rc3-hf6 python -B - <<'PY'
from pathlib import Path
for n in ['be_paper_engine.py','bg_paper_dashboard.py','cu_history_store_v2_hf6.py','de_concurrent_risk_capacity_hf6.py','dh_paper_dynamic_risk_gate_hf6.py','df_caucion_end_of_day_sweep_hf6.py','di_caucion_cash_sweep_runtime_hf6.py','de_scheduler_catalog_hf6.py','df_daily_operation_summary_hf6.py','dg_dashboard_daily_result_ux_hf6.py','dd_history_metrics_hf6.py','cz_a3_cem_public_history_hf6.py','db_a3_cem_normalizer_hf6.py','v_config_metadata.py']:
 compile(Path(n).read_text(encoding='utf-8'),n,'exec',dont_inherit=True)
print('NO_WRITE_SYNTAX=OK')
PY
  SYNTAX_RC=$?; echo "NO_WRITE_SYNTAX_RC=$SYNTAX_RC"

  echo "========== SOURCE RELEASE REGRESSION =========="
  sudo -n docker run --rm --network=none -e PYTHONDONTWRITEBYTECODE=1 -v "$WT:/app:ro" -w /app porota-trading-bot:17.0.0-rc3-hf6 python -m pytest -q -p no:cacheprovider test_hf6_history_data912_v2.py tests/test_a3_cem_public_history_hf6.py tests/test_scheduler_dashboard_hf6.py tests/test_dynamic_risk_and_caucion_sweep_hf6.py tests/test_cash_sweep_runtime_hf6.py tests/test_dashboard_daily_responsive_hf6.py tests/test_dashboard_ux_logs_cem_hf6.py
  SOURCE_TEST_RC=$?; echo "SOURCE_RELEASE_TEST_RC=$SOURCE_TEST_RC"

  echo "========== CLEAN BUILD CONTEXT =========="
  rm -rf "$CTX"; mkdir -p "$CTX"
  tar -C "$WT" --exclude='.git' --exclude='*.pre-*' --exclude='*.bak' --exclude='__pycache__' --exclude='.pytest_cache' -cf - . | tar -C "$CTX" -xf -
  BAD="$(find "$CTX" -type f \( -name '.env' -o -name '.env.*' -o -name 'Secret.txt' -o -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.log' -o -name '*.cred' \) ! -name '.env.example' -print | head -n 20)"
  if [ -n "$BAD" ]; then echo BUILD_CONTEXT_SECURITY=FAILED; SECURITY_RC=1; else echo BUILD_CONTEXT_SECURITY=OK; SECURITY_RC=0; fi
  (cd "$CTX" && find . -type f ! -path './.git/*' -print0 | sort -z | xargs -0 sha256sum) >"$MANIFEST"
  echo "CANDIDATE_MANIFEST=$MANIFEST"; echo "CANDIDATE_MANIFEST_SHA256=$(sha256sum "$MANIFEST"|awk '{print $1}')"

  echo "========== BUILD CANDIDATE IMAGE =========="
  if [ "$PATCH_RC" -eq 0 ] && [ "$SYNTAX_RC" -eq 0 ] && [ "$SOURCE_TEST_RC" -eq 0 ] && [ "$SECURITY_RC" -eq 0 ]; then
   sudo -n docker build --label "org.opencontainers.image.version=$CANDIDATE_VERSION" --label "org.opencontainers.image.revision=$SOURCE_SHA" --label "io.porota.release-candidate=true" -t "$IMAGE" "$CTX"; BUILD_RC=$?
  else BUILD_RC=90; echo IMAGE_BUILD=SKIPPED_PRECONDITION_FAILED; fi
  echo "IMAGE_BUILD_RC=$BUILD_RC"

  if [ "$BUILD_RC" -eq 0 ]; then
   IMAGE_ID="$(sudo -n docker image inspect "$IMAGE" -f '{{.Id}}' 2>/dev/null || true)"; echo "CANDIDATE_IMAGE_ID=$IMAGE_ID"
   sudo -n docker run --rm --network=none -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python "$IMAGE" -m pytest -q -p no:cacheprovider test_hf6_history_data912_v2.py tests/test_a3_cem_public_history_hf6.py tests/test_scheduler_dashboard_hf6.py tests/test_dynamic_risk_and_caucion_sweep_hf6.py tests/test_cash_sweep_runtime_hf6.py tests/test_dashboard_daily_responsive_hf6.py tests/test_dashboard_ux_logs_cem_hf6.py
   IMAGE_TEST_RC=$?; echo "CANDIDATE_IMAGE_TEST_RC=$IMAGE_TEST_RC"
   sudo -n docker run --rm --network=none --entrypoint sh "$IMAGE" -c 'set -eu; test ! -e /app/.env; test ! -e /app/Secret.txt; if find /app -type f \( -name "*.db" -o -name "*.db-wal" -o -name "*.db-shm" -o -name "*.cred" \) | grep -q .; then exit 1; fi; python -B -c "import _version; print(\"IMAGE_INTERNAL_VERSION=\"+_version.VERSION); print(\"IMAGE_RELEASE_SOURCE=\"+getattr(_version,\"RELEASE_SOURCE_COMMIT\",\"MISSING\"))"; echo IMAGE_SECRET_DATA_CHECK=OK'
   IMAGE_SECURITY_RC=$?; echo "IMAGE_SECURITY_CHECK_RC=$IMAGE_SECURITY_RC"
  fi

  git -C "$WT" diff --check; DIFF_RC=$?; echo "GIT_DIFF_CHECK_RC=$DIFF_RC"
  echo MATERIALIZED_CHANGED_FILES_BEGIN; git -C "$WT" diff --name-only | sed 's/^/FILE=/'; echo MATERIALIZED_CHANGED_FILES_END
 fi

 POST="$(observer_state)"
 echo "OBSERVER_POST=${POST:-UNAVAILABLE}"
 echo POROTA_RUNTIME_MUTATION=NO
 echo ACTIVE_CONTAINER_REPLACED=NO
 echo REAL_ORDERS_SENT_BY_THIS_BUILDER=0
 echo DEPLOY_EXECUTED=NO
 echo DOCKER_PRUNE_EXECUTED=NO
 if [ "$SOURCE_SHA" = "$SOURCE_SHA_EXPECTED" ] && [ "$WT_CREATED" -eq 1 ] && [ "$PATCH_RC" -eq 0 ] && [ "$SYNTAX_RC" -eq 0 ] && [ "$SOURCE_TEST_RC" -eq 0 ] && [ "$SECURITY_RC" -eq 0 ] && [ "$BUILD_RC" -eq 0 ] && [ "$IMAGE_TEST_RC" -eq 0 ] && [ "$IMAGE_SECURITY_RC" -eq 0 ] && [ "$DIFF_RC" -eq 0 ] && [ "$PRE" = "$POST" ]; then echo RELEASE_CANDIDATE_BUILD=GREEN; else echo RELEASE_CANDIDATE_BUILD=REVIEW; fi
} >"$REPORT" 2>&1

{
 echo "POROTA HF6 V2 - RELEASE CANDIDATE SUMMARY"
 grep -E '^(OBSERVER_PRE|DB_QUICK_CHECK|OBSERVER_STATE|SOURCE_FETCH|SOURCE_SHA|SOURCE_FREEZE|WORKTREE|UX_LOGS_RC|SCHEDULER_RC|DAILY_RESPONSIVE_RC|HISTORY_DASHBOARD_RC|DYNAMIC_RISK_RC|A3_CEM_SOURCE_RANK_RC|LEGACY_CONFIG_LABELS_RC|CANDIDATE_VERSION_PATCH_RC|PATCHERS_COMBINED_RC|LEGACY_MAX_POSITIONS_GATE_PRESENT|CONCURRENT_RISK_LOCKED_PRESENT|EMERGENCY_CAP_PRESENT|DASHBOARD_SCHEDULER_PRESENT|DASHBOARD_DAILY_RESULTS_PRESENT|DASHBOARD_HISTORY_DYNAMIC_PRESENT|DASHBOARD_DOCKER_SOCKET_PRESENT|A3_CEM_CLOSING_SOURCE_RANK|PPI_HISTORY_SOURCE_RANK|STATIC_RELEASE_INVARIANTS_RC|NO_WRITE_SYNTAX_RC|SOURCE_RELEASE_TEST_RC|BUILD_CONTEXT_SECURITY|CANDIDATE_MANIFEST|CANDIDATE_MANIFEST_SHA256|IMAGE_BUILD_RC|CANDIDATE_IMAGE_ID|CANDIDATE_IMAGE_TEST_RC|IMAGE_INTERNAL_VERSION|IMAGE_RELEASE_SOURCE|IMAGE_SECRET_DATA_CHECK|IMAGE_SECURITY_CHECK_RC|GIT_DIFF_CHECK_RC|OBSERVER_POST|POROTA_RUNTIME_MUTATION|ACTIVE_CONTAINER_REPLACED|REAL_ORDERS_SENT_BY_THIS_BUILDER|DEPLOY_EXECUTED|DOCKER_PRUNE_EXECUTED|RELEASE_CANDIDATE_BUILD)=' "$REPORT" || true
 echo "FULL_REPORT=$REPORT"
} >"$SUMMARY"
cat "$SUMMARY"; clip "$SUMMARY"; rm -f "$SUMMARY"; true
