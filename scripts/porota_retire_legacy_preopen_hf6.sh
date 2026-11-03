#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

MODE="${1:---check}"
ROOT="${POROTA_ROOT:-/opt/porota-trading}"
BACKUP_ROOT="$ROOT/data/backups/preopen-retire"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_ROOT/$TS"
TIMER="porota-preopen.timer"
SERVICE="porota-preopen.service"

observer_state() {
  sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}' porota_production_observer 2>/dev/null || true
}

PRE="$(observer_state)"
echo "POROTA HF6 - LEGACY PREOPEN RETIREMENT"
echo "MODE=$MODE"
echo "OBSERVER_PRE=${PRE:-UNAVAILABLE}"

for unit in "$TIMER" "$SERVICE"; do
  echo "--- $unit status ---"
  sudo -n systemctl status "$unit" --no-pager 2>&1 || true
  echo "--- $unit definition ---"
  sudo -n systemctl cat "$unit" 2>&1 || true
done

echo "--- latest preopen journal ---"
sudo -n journalctl -u "$SERVICE" -n 120 --no-pager 2>&1 || true

if [ "$MODE" != "--apply" ]; then
  echo "PREOPEN_MUTATION=NO"
  echo "NEXT_ACTION=DEPLOY_APPROVAL_REQUIRED_FOR_--apply"
  echo "REAL_ORDERS_SENT_BY_THIS_SCRIPT=0"
  true
  exit 0
fi

sudo -n install -d -m 0750 -o porotaadmin -g porotaadmin "$BACKUP"

for unit in "$TIMER" "$SERVICE"; do
  sudo -n systemctl cat "$unit" >"$BACKUP/${unit}.cat.txt" 2>&1 || true
  sudo -n systemctl status "$unit" --no-pager >"$BACKUP/${unit}.status.txt" 2>&1 || true
done
sudo -n journalctl -u "$SERVICE" -n 500 --no-pager >"$BACKUP/${SERVICE}.journal.txt" 2>&1 || true

# Copy concrete FragmentPath files when they exist. Do not guess paths.
for unit in "$TIMER" "$SERVICE"; do
  fragment="$(sudo -n systemctl show -p FragmentPath --value "$unit" 2>/dev/null || true)"
  if [ -n "$fragment" ] && [ -f "$fragment" ]; then
    sudo -n cp -a "$fragment" "$BACKUP/$(basename "$fragment")"
  fi
done

sudo -n systemctl disable --now "$TIMER" >/dev/null 2>&1 || true
if sudo -n systemctl is-active --quiet "$SERVICE"; then
  sudo -n systemctl stop "$SERVICE"
fi
sudo -n systemctl daemon-reload

TIMER_ENABLED="$(sudo -n systemctl is-enabled "$TIMER" 2>/dev/null || true)"
TIMER_ACTIVE="$(sudo -n systemctl is-active "$TIMER" 2>/dev/null || true)"
SERVICE_ACTIVE="$(sudo -n systemctl is-active "$SERVICE" 2>/dev/null || true)"
POST="$(observer_state)"

echo "BACKUP=$BACKUP"
echo "PREOPEN_TIMER_ENABLED=${TIMER_ENABLED:-unknown}"
echo "PREOPEN_TIMER_ACTIVE=${TIMER_ACTIVE:-unknown}"
echo "PREOPEN_SERVICE_ACTIVE=${SERVICE_ACTIVE:-unknown}"
echo "OBSERVER_POST=${POST:-UNAVAILABLE}"

if [ -n "$PRE" ] && [ -n "$POST" ] && [ "$PRE" != "$POST" ]; then
  echo "STATUS=RED_OBSERVER_CHANGED"
  exit 2
fi
if [ "$TIMER_ACTIVE" = "active" ]; then
  echo "STATUS=RED_TIMER_STILL_ACTIVE"
  exit 3
fi

echo "STATUS=PREOPEN_LEGACY_RETIRED_REVERSIBLY"
echo "REAL_ORDERS_SENT_BY_THIS_SCRIPT=0"
true
