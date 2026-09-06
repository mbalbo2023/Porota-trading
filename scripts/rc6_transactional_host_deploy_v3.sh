#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO="${REPO:-/opt/porota-trading}"
TARGET_SHA="${TARGET_SHA:?TARGET_SHA required}"
ARCHIVE_DIGEST="${ARCHIVE_DIGEST:?ARCHIVE_DIGEST required}"
TARGET_BRANCH="${TARGET_BRANCH:-release-candidate/v17.0.0-rc6-deploy3-20260906}"
TARGET_IMAGE='porota-trading-bot:17.0.0-rc6'
CURRENT_BRANCH="$(git -C "$REPO" branch --show-current)"
CURRENT_SHA="$(git -C "$REPO" rev-parse HEAD)"
CURRENT_IMAGE="$(sudo -n docker inspect -f '{{.Config.Image}}' porota_production_observer)"
TMP=''
WT=''
ACTIVATED=0
MIGRATED=0
LEGACY_TIMERS_FILE="$(mktemp "$HOME/porota-legacy-timers.XXXXXX")"

EXPORTERS=(
  scripts/porota_export_runtime_logs_hf6.sh
  scripts/porota_export_scheduler_state_hf6.sh
)
RC6_CORE_TIMERS=(
  porota-functional-health-rc6.timer
  porota-history-postclose-rc6.timer
  porota-host-general-backup-rc6.timer
  porota-preopen-rc6.timer
  porota-candle-integrity-rc6.timer
)
RC6_A3_TIMERS=(
  porota-a3-history-daily-rc6.timer
  porota-a3-history-reconcile-rc6.timer
  porota-a3-history-weekend-rc6.timer
)

wait_health() {
  local ok=0
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health 2>/dev/null \
      | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin).get('status')=='ok' else 1)" 2>/dev/null; then
      ok=1
      break
    fi
    sleep 4
  done
  test "$ok" -eq 1
}

verify_runtime() {
  local image="$1" obs dash state
  obs="$(sudo -n docker inspect -f '{{.State.Running}}|{{.Config.Image}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}' porota_production_observer)"
  dash="$(sudo -n docker inspect -f '{{.State.Running}}|{{.Config.Image}}|{{.RestartCount}}' porota_production_dashboard)"
  echo "VERIFY_OBSERVER=$obs"
  echo "VERIFY_DASHBOARD=$dash"
  test "$obs" = "true|$image|0|true"
  test "$dash" = "true|$image|0"
  state="$(sudo -n docker exec -i porota_production_observer python - <<'PY'
import sqlite3
vals=[]
for p in ('/app/data/paper_v17/observer_v17.db','/app/data/market_history.db'):
    c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=20)
    c.execute('PRAGMA query_only=ON')
    vals.append(c.execute('PRAGMA quick_check').fetchone()[0])
    if p.endswith('observer_v17.db'):
        row=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
    c.close()
print('|'.join(map(str,(*vals,*row))))
PY
)"
  echo "VERIFY_DB=$state"
  test "$state" = 'ok|ok|PRODUCTION_PAPER|0'
}

cleanup() {
  if [ -n "$WT" ] && [ -d "$WT" ]; then
    git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true
  fi
  [ -z "$TMP" ] || rmdir "$TMP" >/dev/null 2>&1 || true
  rm -f "$LEGACY_TIMERS_FILE" /tmp/porota_schema_before.json /tmp/porota_schema_after.json
}

restore_exporter_exec_bits() {
  chmod 0755 "${EXPORTERS[@]/#/$REPO/}" 2>/dev/null || true
}

restore_legacy_timers() {
  [ -s "$LEGACY_TIMERS_FILE" ] || return 0
  sudo -n systemctl daemon-reload
  while IFS= read -r unit; do
    [ -n "$unit" ] || continue
    sudo -n systemctl enable --now "$unit" >/dev/null 2>&1 || true
  done < "$LEGACY_TIMERS_FILE"
}

rollback_runtime() {
  echo 'ROLLBACK_RUNTIME=START'
  if [ "$MIGRATED" -eq 1 ]; then
    for unit in "${RC6_CORE_TIMERS[@]}" "${RC6_A3_TIMERS[@]}"; do
      sudo -n systemctl disable --now "$unit" >/dev/null 2>&1 || true
    done
    restore_legacy_timers
  fi
  git -C "$REPO" checkout -f -B "$CURRENT_BRANCH" "$CURRENT_SHA"
  restore_exporter_exec_bits
  git -C "$REPO" branch --set-upstream-to="origin/$CURRENT_BRANCH" "$CURRENT_BRANCH" >/dev/null 2>&1 || true
  sudo -n python3 "$REPO/porota_mode_manager.py" simulation
  wait_health
  verify_runtime "$CURRENT_IMAGE"
  echo 'ROLLBACK_RUNTIME=GREEN'
}

on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$ACTIVATED" -eq 1 ]; then
    rollback_runtime || true
  fi
  cleanup
  exit "$rc"
}
trap on_exit EXIT

echo '=== POROTA RC6 DEPLOY3 PREDEPLOY ==='
echo "CURRENT_BRANCH=$CURRENT_BRANCH"
echo "CURRENT_SHA=$CURRENT_SHA"
echo "CURRENT_IMAGE=$CURRENT_IMAGE"
echo "TARGET_BRANCH=$TARGET_BRANCH"
echo "TARGET_SHA=$TARGET_SHA"
echo "TARGET_IMAGE=$TARGET_IMAGE"
echo "GHCR_ARCHIVE_DIGEST=$ARCHIVE_DIGEST"

test "$CURRENT_BRANCH" = 'release/v17.0.0-rc5'
test "$CURRENT_SHA" = '852b812d610860b57b579212978443f7450e8855'
test "$CURRENT_IMAGE" = 'porota-trading-bot:17.0.0-rc5'
test -z "$(git -C "$REPO" diff --cached --name-only)"

python3 - "$REPO" <<'PY'
import pathlib, subprocess, sys
repo=sys.argv[1]
expected={
 'scripts/porota_export_runtime_logs_hf6.sh':'458116779ac1e068ad35e9ffa0a3a7341bf27dbd',
 'scripts/porota_export_scheduler_state_hf6.sh':'c6af79fad6a74772e8f10dac999459dddfe49492',
}
names=subprocess.check_output(['git','-C',repo,'diff','--name-only'],text=True).splitlines()
assert sorted(names)==sorted(expected), (names,sorted(expected))
raw=subprocess.check_output(['git','-C',repo,'diff','--raw','--no-abbrev','--',*expected],text=True).splitlines()
assert len(raw)==2, raw
for line in raw:
    meta,path=line.split('\t',1)
    parts=meta.split()
    assert path in expected
    assert parts[0]==':100644' and parts[1]=='100755', line
    assert parts[2]==expected[path], line
    work_hash=subprocess.check_output(['git','-C',repo,'hash-object',str(pathlib.Path(repo)/path)],text=True).strip()
    assert work_hash==expected[path], (path,work_hash,expected[path])
print('KNOWN_HOST_MODE_ONLY_DIFF=GREEN')
PY

AVAIL="$(df -PB1 "$REPO" | awk 'NR==2{print $4}')"
echo "DISK_FREE_PRE=$AVAIL"
test "$AVAIL" -ge 8589934592
verify_runtime "$CURRENT_IMAGE"

echo 'LOCAL_ROLLBACK_ARTIFACT_PROOF=START'
sudo -n docker image inspect "$CURRENT_IMAGE" >/dev/null
sudo -n docker run --rm --pull never --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --entrypoint python "$CURRENT_IMAGE" -c "import _version; assert _version.VERSION=='17.0.0-rc5'; assert _version.REAL_ORDER_CAPABILITY=='BLOCKED'; print('RC5_LOCAL_OFFLINE_IMAGE=GREEN')"
grep -q -- '--pull' "$REPO/porota_mode_manager.py"
grep -q -- 'never' "$REPO/porota_mode_manager.py"
echo 'LOCAL_ROLLBACK_ARTIFACT_PROOF=GREEN'

git -C "$REPO" fetch --no-tags origin "$TARGET_BRANCH"
test "$(git -C "$REPO" rev-parse "origin/$TARGET_BRANCH")" = "$TARGET_SHA"
git -C "$REPO" merge-base --is-ancestor 852b812d610860b57b579212978443f7450e8855 "$TARGET_SHA"

python3 - "$REPO" "$TARGET_SHA" <<'PY'
import subprocess,sys
repo,sha=sys.argv[1:]
untracked=set(subprocess.check_output(['git','-C',repo,'ls-files','--others','--exclude-standard'],text=True).splitlines())
tracked=set(subprocess.check_output(['git','-C',repo,'ls-tree','-r','--name-only',sha],text=True).splitlines())
collisions=sorted(untracked & tracked)
assert not collisions, 'UNTRACKED_TARGET_COLLISION:'+','.join(collisions[:20])
print(f'UNTRACKED_COLLISION_GATE=GREEN count={len(untracked)}')
PY

TMP="$(mktemp -d "$HOME/porota-rc6-deploy3.XXXXXX")"
WT="$TMP/source"
git -C "$REPO" worktree add --detach "$WT" "$TARGET_SHA" >/dev/null
python3 - "$WT" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
import _version
assert _version.VERSION=='17.0.0-rc6'
assert _version.MODE=='PRODUCTION_PAPER'
assert _version.EXECUTION=='SIMULATED'
assert _version.REAL_ORDER_CAPABILITY=='BLOCKED'
PY

echo 'HOST_BUILD=START'
sudo -n docker build --pull=false --label "porota.commit=$TARGET_SHA" --label 'porota.version=17.0.0-rc6' -t "$TARGET_IMAGE" "$WT"
test "$(sudo -n docker image inspect "$TARGET_IMAGE" --format '{{index .Config.Labels "porota.commit"}}')" = "$TARGET_SHA"
AVAIL="$(df -PB1 "$REPO" | awk 'NR==2{print $4}')"
echo "DISK_FREE_POST_BUILD=$AVAIL"
test "$AVAIL" -ge 8589934592

sudo -n docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=1777 \
  --tmpfs /app/data:rw,nosuid,nodev,size=64m,uid=1000,gid=1000,mode=0770 \
  --entrypoint bash "$TARGET_IMAGE" -lc \
  'python -m pytest -q -p no:cacheprovider tests/test_rc6_sunday_host_policy.py tests/test_rc6_no_rc4_runtime_dependency.py tests/test_a3_history_rc6.py tests/test_byma_schedule_rc5.py'
echo 'HOST_OFFLINE_TESTS=GREEN'

sudo -n docker exec -i porota_production_observer python - <<'PY' > /tmp/porota_schema_before.json
import json,sqlite3
out={}
for p in ('/app/data/paper_v17/observer_v17.db','/app/data/market_history.db'):
    c=sqlite3.connect(f'file:{p}?mode=ro',uri=True); c.execute('pragma query_only=on')
    out[p]=dict(c.execute("select name,coalesce(sql,'') from sqlite_master where type in ('table','index','trigger','view') and name not like 'sqlite_%'"))
    c.close()
print(json.dumps(out,sort_keys=True))
PY

echo 'RC6_ACTIVATION=START'
# The only tracked host drift has already been proven above as content-identical
# exporter chmod 0644->0755, and target RC6 tracks those same blobs as 0755.
# -f is therefore scoped to the proven transition; untracked files are preserved
# and were separately checked for target collisions before this point.
git -C "$REPO" checkout -f -B "$TARGET_BRANCH" "$TARGET_SHA"
ACTIVATED=1
git -C "$REPO" branch --set-upstream-to="origin/$TARGET_BRANCH" "$TARGET_BRANCH"
test -z "$(git -C "$REPO" diff --name-only)"
sudo -n python3 "$REPO/porota_mode_manager.py" simulation
wait_health
verify_runtime "$TARGET_IMAGE"
echo 'RC6_ACTIVATION=GREEN'

echo 'CONTROLLED_LOCAL_ROLLBACK_REHEARSAL=START'
git -C "$REPO" checkout -f -B "$CURRENT_BRANCH" "$CURRENT_SHA"
restore_exporter_exec_bits
sudo -n python3 "$REPO/porota_mode_manager.py" simulation
wait_health
verify_runtime "$CURRENT_IMAGE"
sudo -n docker run --rm --pull never --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --entrypoint python "$CURRENT_IMAGE" -c "import _version; assert _version.VERSION=='17.0.0-rc5'; print('ROLLBACK_PULL_NEVER=GREEN')"
echo 'CONTROLLED_LOCAL_ROLLBACK_REHEARSAL_RC5=GREEN'

# Returning to RC6 repeats the same already-proven mode-only transition.
git -C "$REPO" checkout -f -B "$TARGET_BRANCH" "$TARGET_SHA"
sudo -n python3 "$REPO/porota_mode_manager.py" simulation
wait_health
verify_runtime "$TARGET_IMAGE"
echo 'CONTROLLED_LOCAL_ROLLBACK_REHEARSAL=GREEN'

echo 'SCHEMA_COMPATIBILITY=START'
sudo -n docker exec -i porota_production_observer python - <<'PY' > /tmp/porota_schema_after.json
import json,sqlite3
out={}
for p in ('/app/data/paper_v17/observer_v17.db','/app/data/market_history.db'):
    c=sqlite3.connect(f'file:{p}?mode=ro',uri=True); c.execute('pragma query_only=on')
    out[p]=dict(c.execute("select name,coalesce(sql,'') from sqlite_master where type in ('table','index','trigger','view') and name not like 'sqlite_%'"))
    c.close()
print(json.dumps(out,sort_keys=True))
PY
python3 - <<'PY'
import json
before=json.load(open('/tmp/porota_schema_before.json'))
after=json.load(open('/tmp/porota_schema_after.json'))
for db,objects in before.items():
    missing=[k for k in objects if k not in after[db]]
    changed=[k for k,v in objects.items() if after[db].get(k)!=v]
    assert not missing and not changed, (db,missing,changed)
print('SCHEMA_COMPATIBILITY=GREEN_ADDITIVE_ONLY')
PY

echo 'SYSTEMD_RC6_MIGRATION=START'
{
  # Snapshot the ACTUAL active legacy timers before mutation so a failed
  # RC6 migration can restore the exact scheduler state, independent of
  # UnitFileState reporting.
  sudo -n systemctl list-timers --all --no-legend --no-pager \
    | grep -oE 'porota-[[:alnum:]_.@-]*rc4\.timer' || true
  for unit in porota-contract-evidence-rc5.timer porota-preopen.timer; do
    [ "$(sudo -n systemctl is-active "$unit" 2>/dev/null || true)" = active ] && echo "$unit"
  done
} | sort -u > "$LEGACY_TIMERS_FILE"

mapfile -t RC4_UNITS < <(sudo -n systemctl list-unit-files --no-legend --no-pager | awk '$1 ~ /^porota-.*rc4\.(service|timer)$/ {print $1}')
for unit in "${RC4_UNITS[@]}"; do
  sudo -n systemctl disable --now "$unit" >/dev/null 2>&1 || true
  sudo -n systemctl reset-failed "$unit" >/dev/null 2>&1 || true
done
for unit in porota-contract-evidence-rc5.timer porota-contract-evidence-rc5.service porota-preopen.timer porota-preopen.service; do
  sudo -n systemctl disable --now "$unit" >/dev/null 2>&1 || true
  sudo -n systemctl reset-failed "$unit" >/dev/null 2>&1 || true
done

CORE_FILES=(
  porota-functional-health-rc6.service porota-functional-health-rc6.timer
  porota-history-postclose-rc6.service porota-history-postclose-rc6.timer
  porota-host-general-backup-rc6.service porota-host-general-backup-rc6.timer
  porota-preopen-rc6.service porota-preopen-rc6.timer
  porota-candle-integrity-rc6.service porota-candle-integrity-rc6.timer
)
A3_FILES=(
  porota-a3-history-rc6@.service
  porota-a3-history-daily-rc6.timer
  porota-a3-history-reconcile-rc6.timer
  porota-a3-history-weekend-rc6.timer
)
for f in "${CORE_FILES[@]}" "${A3_FILES[@]}"; do
  sudo -n install -m 0644 "$REPO/systemd/$f" "/etc/systemd/system/$f"
done
sudo -n systemd-analyze calendar 'Mon..Fri *-*-* 10:15:00 America/Argentina/Buenos_Aires' >/dev/null
sudo -n systemctl daemon-reload
MIGRATED=1
for unit in "${RC6_CORE_TIMERS[@]}"; do
  sudo -n systemctl enable --now "$unit" >/dev/null
  test "$(sudo -n systemctl is-enabled "$unit")" = enabled
  test "$(sudo -n systemctl is-active "$unit")" = active
done

# Some legacy timers may still be loaded/active even after disable.
# `porota-rc4-auto-check.timer` was proven by read-only forensics to be
# enabled/active, RefuseManualStop=no, with a normal timers.target link.
# Stop that exact unit first, then sweep the scheduler's actual timer list.
sudo -n systemctl disable porota-rc4-auto-check.timer >/dev/null 2>&1 || true
sudo -n systemctl stop porota-rc4-auto-check.timer
test "$(sudo -n systemctl is-active porota-rc4-auto-check.timer 2>/dev/null || true)" != active

mapfile -t RC4_ACTIVE_TIMERS < <(
  sudo -n systemctl list-timers --all --no-legend --no-pager \
    | grep -oE 'porota-[[:alnum:]_.@-]*rc4\.timer' \
    | sort -u || true
)
for unit in "${RC4_ACTIVE_TIMERS[@]}"; do
  [ -n "$unit" ] || continue
  echo "RETIRE_ACTIVE_RC4_TIMER=$unit"
  sudo -n systemctl disable "$unit" >/dev/null 2>&1 || true
  sudo -n systemctl stop "$unit"
  test "$(sudo -n systemctl is-active "$unit" 2>/dev/null || true)" != active
  sudo -n systemctl reset-failed "$unit" >/dev/null 2>&1 || true
done
sudo -n systemctl daemon-reload
sleep 2
REMAINING_RC4_TIMERS="$(sudo -n systemctl list-timers --all --no-legend --no-pager | grep -oE 'porota-[[:alnum:]_.@-]*rc4\.timer' | sort -u || true)"
if [ -n "$REMAINING_RC4_TIMERS" ]; then
  echo 'RC4_ACTIVE_TIMERS_REMAIN'
  printf '%s\n' "$REMAINING_RC4_TIMERS"
  exit 1
fi
echo 'SYSTEMD_RC6_MIGRATION=GREEN'

sudo -n systemctl start porota-functional-health-rc6.service
sudo -n systemctl start porota-candle-integrity-rc6.service
sudo -n systemctl start porota-history-postclose-rc6.service
sudo -n python3 "$REPO/rc6_preopen.py"
verify_runtime "$TARGET_IMAGE"
echo 'RC6_HOST_PROBES=GREEN'

echo 'SOAK=START'
sleep 90
verify_runtime "$TARGET_IMAGE"
test "$(git -C "$REPO" rev-parse HEAD)" = "$TARGET_SHA"
test "$(git -C "$REPO" branch --show-current)" = "$TARGET_BRANCH"
AVAIL="$(df -PB1 "$REPO" | awk 'NR==2{print $4}')"
echo "DISK_FREE_FINAL=$AVAIL"
test "$AVAIL" -ge 8589934592
echo 'SOAK=GREEN'

A3_STATUS=GREEN
for unit in "${RC6_A3_TIMERS[@]}"; do
  if ! sudo -n systemctl enable --now "$unit" >/dev/null; then
    A3_STATUS=DEGRADED_FAIL_CLOSED
  fi
done
sleep 5
verify_runtime "$TARGET_IMAGE"

echo "A3_BACKGROUND=$A3_STATUS"
echo 'CONTRACT_EVIDENCE=BLOCKED_PENDING_RC6_NATIVE_WIRING'
echo 'REAL_ORDERS_SENT=0'
echo 'NETWORK_ORDER_TEST=NOT_PERFORMED'
echo "RC6_DEPLOYED_SHA=$TARGET_SHA"
echo "RC6_ARCHIVE_DIGEST=$ARCHIVE_DIGEST"
echo 'RC6_DEPLOY=GREEN'
trap - EXIT
cleanup