#!/usr/bin/env bash
# RC6 Contract Evidence: durable, lock-safe recovery wrapper.
set -Eeuo pipefail
umask 077

STATE_DIR=/var/lib/porota-contract-recovery-rc6
STATE="$STATE_DIR/last_result.txt"
TMP="$STATE_DIR/.result.$$.txt"
RUNNER=/usr/local/sbin/porota-contract-point1-recovery-rc6
LOCK=/run/lock/porota-contract-recovery-rc6.lock
HISTORICAL=porota-ppi-web-residual-rc6.service

mkdir -p "$STATE_DIR"
exec 9<>"$LOCK"
if ! flock -n 9; then
  echo 'RECOVERY_RESULT=YELLOW_RECOVERY_LOCK_BUSY'
  exit 0
fi

# Never race a durable PPI Web historical writer.
if systemctl is-active --quiet "$HISTORICAL"; then
  echo 'RECOVERY_RESULT=YELLOW_HISTORICAL_WRITER_ACTIVE'
  exit 0
fi

# A valid or proven non-materializable capture is terminal until a new collector
# version is deployed. Replaying it would only create redundant evidence.
if [[ -r "$STATE" ]] && grep -Eq 'POINT1_CAPTURE_RESULT=(GREEN_VALID_IMPORTABLE_CAPTURE|YELLOW_IMPORTABLE_BUT_TARGET_ENDPOINTS_INCOMPLETE|RED_CAPTURE_NOT_IMPORTABLE)' "$STATE"; then
  echo 'RECOVERY_RESULT=GREEN_TERMINAL_RESULT_ALREADY_RECORDED'
  exit 0
fi

if [[ ! -x "$RUNNER" ]]; then
  echo 'RECOVERY_RESULT=RED_CANONICAL_RUNNER_MISSING'
  exit 1
fi

set +e
"$RUNNER" >"$TMP" 2>&1
rc=$?
set -e

# Atomic checkpoint: never overwrite a prior complete file with a partial write.
install -m 0600 "$TMP" "$STATE"
rm -f "$TMP"

grep -E '^(PREFLIGHT|REAUTH_STATUS|POINT1_REAUTH_RESULT|POINT1_CAPTURE_RESULT|POSTFLIGHT|DB_IMPORT_EXECUTED|REAL_ORDERS_SENT)=' "$STATE" || true
if [[ "$rc" -ne 0 ]]; then
  echo "RECOVERY_RESULT=RED_RUNNER_EXIT_${rc}"
  exit "$rc"
fi

if grep -q 'POINT1_CAPTURE_RESULT=RED_REAUTH_NOT_GREEN' "$STATE"; then
  echo 'RECOVERY_RESULT=YELLOW_RETRY_AFTER_BACKOFF'
else
  echo 'RECOVERY_RESULT=GREEN_RUNNER_COMPLETED'
fi
