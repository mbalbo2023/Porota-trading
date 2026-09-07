#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=/opt/porota-trading
DB=$ROOT/data/paper_v17/observer_v17.db
TARGET_IMAGE=porota-trading-bot:17.0.0-rc6
PAYLOAD=/tmp/porota-sre-rc6.tgz
TMP=''
cleanup(){ [ -z "$TMP" ] || rm -rf "$TMP"; rm -f "$PAYLOAD"; }
trap cleanup EXIT

echo SRE_PREDEPLOY=START
for _ in $(seq 1 30); do
  obs="$(sudo -n docker inspect -f '{{.State.Running}}|{{.Config.Image}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}' porota_production_observer 2>/dev/null || true)"
  [ "$obs" = "true|$TARGET_IMAGE|0|true" ] && break
  sleep 4
done
test "$obs" = "true|$TARGET_IMAGE|0|true"
BEFORE_ID="$(sudo -n docker inspect -f '{{.Id}}' porota_production_observer)"
BEFORE_LABEL="$(sudo -n docker image inspect "$TARGET_IMAGE" --format '{{index .Config.Labels "porota.commit"}}')"
BEFORE_BRANCH="$(git -C "$ROOT" branch --show-current)"
BEFORE_HEAD="$(git -C "$ROOT" rev-parse HEAD)"
python3 -c "import sqlite3;c=sqlite3.connect('file:$DB?mode=ro',uri=True,timeout=30);c.execute('PRAGMA query_only=ON');assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok';m,o=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone();c.close();assert m=='PRODUCTION_PAPER' and int(o)==0;print('SRE_PRE_DB=GREEN')"

test -f "$PAYLOAD"
TMP="$(mktemp -d /tmp/porota-sre.XXXXXX)"
tar -xzf "$PAYLOAD" -C "$TMP"
sudo -n install -d -m 0755 /usr/local/lib/porota-sre-rc6
sudo -n install -m 0644 "$TMP/rc6_fast_functional_health.py" /usr/local/lib/porota-sre-rc6/rc6_fast_functional_health.py
sudo -n install -m 0644 "$TMP/rc6_full_db_integrity.py" /usr/local/lib/porota-sre-rc6/rc6_full_db_integrity.py
sudo -n install -m 0644 "$TMP/rc6_preopen_host_hardened.py" /usr/local/lib/porota-sre-rc6/rc6_preopen_host_hardened.py

sudo -n tee /etc/systemd/system/porota-fast-functional-health-rc6.service >/dev/null <<'UNIT'
[Unit]
Description=Porota RC6 fast functional health (read-only, no full DB scan)
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
WorkingDirectory=/opt/porota-trading
Environment=PYTHONPATH=/opt/porota-trading
Environment=PAPER_V17_DB_PATH=/opt/porota-trading/data/paper_v17/observer_v17.db
ExecStart=/usr/bin/python3 /usr/local/lib/porota-sre-rc6/rc6_fast_functional_health.py
TimeoutStartSec=30s
UNIT

sudo -n tee /etc/systemd/system/porota-fast-functional-health-rc6.timer >/dev/null <<'UNIT'
[Unit]
Description=Porota RC6 fast functional health every 5 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s
Unit=porota-fast-functional-health-rc6.service
[Install]
WantedBy=timers.target
UNIT

sudo -n tee /etc/systemd/system/porota-full-db-integrity-rc6.service >/dev/null <<'UNIT'
[Unit]
Description=Porota RC6 full SQLite quick_check (read-only, postclose)
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
WorkingDirectory=/opt/porota-trading
Environment=PAPER_V17_DB_PATH=/opt/porota-trading/data/paper_v17/observer_v17.db
ExecStart=/usr/bin/python3 /usr/local/lib/porota-sre-rc6/rc6_full_db_integrity.py
TimeoutStartSec=3min
Nice=10
UNIT

sudo -n tee /etc/systemd/system/porota-full-db-integrity-rc6.timer >/dev/null <<'UNIT'
[Unit]
Description=Porota RC6 full DB integrity once postclose on weekdays
[Timer]
OnCalendar=Mon..Fri *-*-* 17:20:00 America/Argentina/Buenos_Aires
Persistent=true
RandomizedDelaySec=120
Unit=porota-full-db-integrity-rc6.service
[Install]
WantedBy=timers.target
UNIT

sudo -n tee /etc/systemd/system/porota-preopen-rc6.service >/dev/null <<'UNIT'
[Unit]
Description=Porota RC6 10:15 preopen fail-closed readiness
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
WorkingDirectory=/opt/porota-trading
Environment=PYTHONPATH=/opt/porota-trading
ExecStart=/usr/bin/python3 /usr/local/lib/porota-sre-rc6/rc6_preopen_host_hardened.py
TimeoutStartSec=5min
UNIT

sudo -n systemctl daemon-reload
FAST_START=$(date +%s%N)
sudo -n systemctl start porota-fast-functional-health-rc6.service
FAST_END=$(date +%s%N)
FAST_MS=$(( (FAST_END-FAST_START)/1000000 ))
echo "FAST_HEALTH_MS=$FAST_MS"
sudo -n systemctl start porota-full-db-integrity-rc6.service
sudo -n systemctl enable --now porota-fast-functional-health-rc6.timer porota-full-db-integrity-rc6.timer >/dev/null
sudo -n systemctl disable --now porota-functional-health-rc6.timer >/dev/null 2>&1 || true

test "$(sudo -n systemctl is-enabled porota-fast-functional-health-rc6.timer)" = enabled
test "$(sudo -n systemctl is-active porota-fast-functional-health-rc6.timer)" = active
test "$(sudo -n systemctl is-enabled porota-full-db-integrity-rc6.timer)" = enabled
test "$(sudo -n systemctl is-active porota-full-db-integrity-rc6.timer)" = active

# A concurrent observer activation can briefly leave observer_state at STARTING/CHECKING.
# Wait for the persisted postclose session to settle before proving preopen policy.
READY=0
for _ in $(seq 1 36); do
  if python3 - "$DB" <<'PY'
import sqlite3,sys
p=sys.argv[1]
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=15); c.execute('PRAGMA query_only=ON')
r=c.execute('SELECT mode,session_state,ppi_auth,real_orders_sent FROM observer_state WHERE id=1').fetchone(); c.close()
if not r: raise SystemExit(1)
mode,session,auth,orders=r
ok=(mode=='PRODUCTION_PAPER' and str(session).upper()=='MARKET_CLOSED' and
    str(auth).upper() in {'OK','AUTHENTICATED','NOT_ATTEMPTED'} and int(orders or 0)==0)
raise SystemExit(0 if ok else 1)
PY
  then READY=1; break; fi
  sleep 5
done
test "$READY" -eq 1
echo OBSERVER_SESSION_READY=GREEN

# Re-evaluate today's preopen contract with the same 8 GiB disk floor.
set +e
sudo -n systemctl start porota-preopen-rc6.service
PRE_RC=$?
set -e
sudo -n journalctl -u porota-preopen-rc6.service -n 100 --no-pager -o cat | tail -n 100
PRE_RESULT="$(sudo -n systemctl show porota-preopen-rc6.service -p Result --value)"
test "$PRE_RC" -eq 0
test "$PRE_RESULT" = success

# Clear only stale failure state; legacy timers were already disabled.
sudo -n systemctl reset-failed porota-preopen-rc6.service porota-contract-evidence-hf6.service porota-rc4-auto-check.service 2>/dev/null || true
test "$(sudo -n systemctl is-active porota-contract-evidence-hf6.timer 2>/dev/null || true)" != active
test "$(sudo -n systemctl is-active porota-rc4-auto-check.timer 2>/dev/null || true)" != active

# Host-only deployment must not replace observer or repository identity during this run.
test "$(sudo -n docker inspect -f '{{.Id}}' porota_production_observer)" = "$BEFORE_ID"
test "$(sudo -n docker image inspect "$TARGET_IMAGE" --format '{{index .Config.Labels "porota.commit"}}')" = "$BEFORE_LABEL"
test "$(git -C "$ROOT" branch --show-current)" = "$BEFORE_BRANCH"
test "$(git -C "$ROOT" rev-parse HEAD)" = "$BEFORE_HEAD"
python3 -c "import sqlite3;c=sqlite3.connect('file:$DB?mode=ro',uri=True,timeout=30);c.execute('PRAGMA query_only=ON');assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok';m,o=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone();c.close();assert m=='PRODUCTION_PAPER' and int(o)==0;print('SRE_POST_DB=GREEN')"

echo SRE_FAST_FULL_SPLIT=GREEN_LIVE
echo PREOPEN_HOST_CONTRACT=GREEN_LIVE
echo LEGACY_FAILED_STATE_CLEANED=YES
echo REAL_ORDERS_SENT=0
