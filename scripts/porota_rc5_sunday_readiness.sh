#!/usr/bin/env bash
set -euo pipefail

# RC5 P0-5 — Sunday Readiness. Read-only/fail-closed.
# No restart, no cleanup, no chmod/chown, no DB mutation, no network order test.

ROOT="${POROTA_ROOT:-/opt/porota-trading}"
OBS="${POROTA_OBSERVER_CONTAINER:-porota_production_observer}"
DASH="${POROTA_DASHBOARD_CONTAINER:-porota_production_dashboard}"
EXPECTED_VERSION="${POROTA_EXPECTED_VERSION:-17.0.0-rc5}"
EXPECTED_IMAGE="${POROTA_EXPECTED_IMAGE:-porota-trading-bot:17.0.0-rc5}"
FAIL=0
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

ok(){ printf 'GREEN|%s|%s\n' "$1" "$2"; }
bad(){ printf 'RED|%s|%s\n' "$1" "$2"; FAIL=1; }
info(){ printf 'INFO|%s|%s\n' "$1" "$2"; }

printf 'POROTA_SUNDAY_READINESS_V1\n'
printf 'CHECKED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ -d "$ROOT/.git" ]; then
  HEAD="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
  BRANCH="$(git -C "$ROOT" branch --show-current 2>/dev/null || true)"
  info GIT "branch=${BRANCH:-UNKNOWN}|head=${HEAD:-UNKNOWN}"
else
  bad GIT "repo_missing:$ROOT"
fi

for c in "$OBS" "$DASH"; do
  if sudo -n docker inspect "$c" >"$TMP" 2>/dev/null; then
    RUNNING="$(sudo -n docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || true)"
    RESTARTS="$(sudo -n docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null || true)"
    IMAGE="$(sudo -n docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null || true)"
    READONLY="$(sudo -n docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$c" 2>/dev/null || true)"
    if [ "$RUNNING" = true ]; then ok "CONTAINER:$c" "running=true|restarts=$RESTARTS|image=$IMAGE|readonly=$READONLY"; else bad "CONTAINER:$c" "running=$RUNNING"; fi
    if [ "$IMAGE" != "$EXPECTED_IMAGE" ]; then bad "IMAGE:$c" "expected=$EXPECTED_IMAGE|actual=$IMAGE"; fi
    if [ "$c" = "$OBS" ] && [ "$READONLY" != true ]; then bad "READONLY:$c" "readonly=$READONLY"; fi
  else
    bad "CONTAINER:$c" "missing_or_uninspectable"
  fi
done

# Runtime state through the versioned read-only inventory when available.
if sudo -n docker exec "$OBS" test -f /app/scripts/porota_rc5_paper_state_inventory.py 2>/dev/null; then
  STATE="$(sudo -n docker exec "$OBS" python /app/scripts/porota_rc5_paper_state_inventory.py 2>/dev/null || true)"
  printf 'PAPER_STATE=%s\n' "$STATE"
  echo "$STATE" | grep -q '"status":"GREEN"' || bad PAPER_STATE "inventory_not_green"
  echo "$STATE" | grep -q '"real_orders_sent":0' || bad REAL_ORDERS "not_empirically_zero"
else
  bad PAPER_STATE "inventory_script_missing_from_image"
fi

# Host capacity. Keep a conservative minimum of 4 GiB free for this gate.
AVAIL_KB="$(df -Pk "$ROOT" | awk 'NR==2{print $4}')"
if [ "${AVAIL_KB:-0}" -ge 4194304 ]; then ok DISK "available_kb=$AVAIL_KB"; else bad DISK "available_kb=${AVAIL_KB:-UNKNOWN}"; fi

# Scheduler truth: capture host truth, and reject failed Porota units.
FAILED_UNITS="$(sudo -n systemctl --failed --no-legend 2>/dev/null | awk '{print $1}' | grep '^porota-' || true)"
if [ -z "$FAILED_UNITS" ]; then ok SYSTEMD_FAILED "none"; else bad SYSTEMD_FAILED "$(echo "$FAILED_UNITS" | tr '\n' ',')"; fi
sudo -n systemctl list-timers --all --no-pager 2>/dev/null | grep -E 'porota-|NEXT|LEFT' | head -80 | sed 's/^/TIMER|/' || true

# Contract Evidence/browser must not be forced on a weekend. Timer existence is informational.
for u in porota-contract-evidence-rc5.timer porota-introspection-publish.timer; do
  S="$(sudo -n systemctl is-enabled "$u" 2>/dev/null || true)"
  A="$(sudo -n systemctl is-active "$u" 2>/dev/null || true)"
  info "UNIT:$u" "enabled=${S:-UNKNOWN}|active=${A:-UNKNOWN}"
done

# Market/session static preflight. No broker/order call.
if sudo -n docker exec "$OBS" python /app/rc5_release_preflight.py >"$TMP" 2>&1; then
  grep -E '^(GREEN|RC5_PREFLIGHT|CHECKS=)' "$TMP" | sed 's/^/PREFLIGHT|/'
  grep -q '^RC5_PREFLIGHT=GREEN$' "$TMP" && ok PREFLIGHT RC5_GREEN || bad PREFLIGHT RC5_NOT_GREEN
else
  tail -40 "$TMP" | sed 's/^/PREFLIGHT_ERR|/'
  bad PREFLIGHT "execution_failed"
fi

if [ "$FAIL" -eq 0 ]; then
  printf 'SUNDAY_READINESS=GREEN\n'
  exit 0
fi
printf 'SUNDAY_READINESS=RED\n'
exit 2
