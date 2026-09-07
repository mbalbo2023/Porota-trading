#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"
: "${HEALTH_PROBE_PATH:?HEALTH_PROBE_PATH required}"

C="${GATEWAY_CONTAINER:-porota_critical_approval_rc6}"
BASE=/opt/porota-control-plane-rc6
CURRENT="$BASE/current"
RELEASE="$BASE/releases/$SOURCE_SHA"
GW_UNIT=/etc/systemd/system/porota-critical-approval-rc6.service
DB=/opt/porota-trading/data/paper_v17/observer_v17.db
TMP_UNIT=/tmp/porota-critical-approval-rc6-health.service
ORIGINAL_UNIT=/tmp/porota-critical-approval-rc6-health-original.service
changed=0

cleanup() {
  rc=$?
  if [[ $rc -ne 0 && $changed -eq 1 ]]; then
    echo "CRITICAL_HEALTHCHECK_RESTORE=START rc=$rc"
    if [[ -f "$ORIGINAL_UNIT" ]]; then
      sudo -n install -m 0644 "$ORIGINAL_UNIT" "$GW_UNIT" || true
      sudo -n systemctl daemon-reload || true
      sudo -n systemctl restart porota-critical-approval-rc6.service || true
    fi
    echo 'CRITICAL_HEALTHCHECK_RESTORE=COMPLETE'
  fi
  sudo -n rm -f "$TMP_UNIT" "$ORIGINAL_UNIT" "$HEALTH_PROBE_PATH" >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT

db_state() {
  sudo -n python3 - "$DB" <<'PY'
import sqlite3, sys
p=sys.argv[1]
c=sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=30)
c.execute('PRAGMA query_only=ON')
q=c.execute('PRAGMA quick_check').fetchone()[0]
r=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
c.close()
print('|'.join(map(str,(q,*r))))
PY
}

broker_probe() {
  sudo -n docker exec "$C" python -c "from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient; h=UnixGithubIssuesClient().health(); assert h.get('status')=='ok' and h.get('capability')=='issues_only',h; print('CRITICAL_BROKER_HEALTH=GREEN')"
}

echo '=== RC6 CRITICAL CONTROL-PLANE DEDICATED HEALTHCHECK ==='
test -f "$HEALTH_PROBE_PATH"
test -d "$RELEASE"
test "$(readlink -f "$CURRENT")" = "$RELEASE"
test -f "$GW_UNIT"
test "$(sudo -n systemctl is-active porota-critical-approval-rc6.service)" = active
test "$(sudo -n systemctl is-active porota-critical-github-proxy-rc6.service)" = active

db_pre="$(db_state)"
echo "OBSERVER_DB_PRE=$db_pre"
test "$db_pre" = 'ok|PRODUCTION_PAPER|0'
obs_id="$(sudo -n docker inspect -f '{{.Id}}' porota_production_observer)"
dash_id="$(sudo -n docker inspect -f '{{.Id}}' porota_production_dashboard)"
broker_probe

sudo -n install -m 0644 "$HEALTH_PROBE_PATH" "$RELEASE/fo_critical_approval_health_rc6.py"
sudo -n cat "$GW_UNIT" > "$ORIGINAL_UNIT"
python3 - "$ORIGINAL_UNIT" "$TMP_UNIT" <<'PY'
import pathlib, sys
src=pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
health='--health-cmd="python /code/fo_critical_approval_health_rc6.py" --health-interval=10s --health-timeout=5s --health-start-period=10s --health-retries=3'
if health in src:
    out=src
else:
    needle='--memory 192m --tmpfs /tmp:rw,noexec,nosuid,size=32m'
    if src.count(needle) != 1:
        raise SystemExit('CRITICAL_HEALTHCHECK_PATCH_NEEDLE_INVALID')
    out=src.replace(needle, f'--memory 192m {health} --tmpfs /tmp:rw,noexec,nosuid,size=32m')
pathlib.Path(sys.argv[2]).write_text(out, encoding='utf-8')
PY

grep -Fq -- '--health-cmd="python /code/fo_critical_approval_health_rc6.py"' "$TMP_UNIT"
sudo -n systemd-analyze verify "$TMP_UNIT" >/dev/null
echo 'CRITICAL_HEALTHCHECK_UNIT_VERIFY=GREEN'

if ! cmp -s "$ORIGINAL_UNIT" "$TMP_UNIT"; then
  changed=1
  sudo -n install -m 0644 "$TMP_UNIT" "$GW_UNIT"
  sudo -n systemctl daemon-reload
  sudo -n systemctl restart porota-critical-approval-rc6.service
fi

for _ in $(seq 1 18); do
  health_state="$(sudo -n docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$C" 2>/dev/null || echo missing)"
  [[ "$health_state" == healthy ]] && break
  sleep 5
done

health_state="$(sudo -n docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$C")"
echo "CRITICAL_DOCKER_HEALTH=$health_state"
test "$health_state" = healthy
broker_probe

test "$(sudo -n docker inspect -f '{{.Id}}' porota_production_observer)" = "$obs_id"
test "$(sudo -n docker inspect -f '{{.Id}}' porota_production_dashboard)" = "$dash_id"
db_post="$(db_state)"
echo "OBSERVER_DB_POST=$db_post"
test "$db_post" = 'ok|PRODUCTION_PAPER|0'

echo 'OBSERVER_RESTARTED=NO'
echo 'DASHBOARD_RESTARTED=NO'
echo 'REAL_ORDERS_SENT=0'
echo 'CRITICAL_HEALTHCHECK_PERSISTENCE=GREEN'
changed=0
sudo -n rm -f "$TMP_UNIT" "$ORIGINAL_UNIT" "$HEALTH_PROBE_PATH"
trap - EXIT
