#!/usr/bin/env bash
set -Eeuo pipefail

LOCK=/run/lock/porota-ppi-fullfamily-history.lock
CONTAINER=porota_production_observer
RUNNER=/opt/porota-ingest/ppi_history_runner_rc6.py
PIDFILE=/tmp/porota-ppi-fullfamily-history.pid
CHILD=""

# The lock file is provisioned by the bounded deploy control plane. Open the
# existing inode without O_CREAT: hardened Linux protected_regular may reject
# O_CREAT on a regular file in /run/lock even for a privileged service when the
# inode originated under another user. Never unlink/recreate a live lock inode.
test -e "$LOCK" || { echo "BLOCKED=CANONICAL_LOCK_FILE_MISSING"; exit 76; }
exec 9<>"$LOCK"
if ! flock -n 9; then
  echo "BLOCKED=CANONICAL_HISTORY_LOCK_BUSY"
  exit 75
fi

echo "LOCK_OWNER=$$"

test -r "$RUNNER" || { echo "BLOCKED=RUNNER_MISSING"; exit 70; }
docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx true || {
  echo "BLOCKED=OBSERVER_CONTAINER_NOT_RUNNING"
  exit 71
}

stop_container_runner() {
  set +e
  if docker exec "$CONTAINER" sh -c "test -s '$PIDFILE' && kill -0 \$(cat '$PIDFILE') 2>/dev/null" >/dev/null 2>&1; then
    docker exec "$CONTAINER" sh -c "kill -TERM \$(cat '$PIDFILE')" >/dev/null 2>&1 || true
    for _ in $(seq 1 70); do
      docker exec "$CONTAINER" sh -c "test -s '$PIDFILE' && kill -0 \$(cat '$PIDFILE') 2>/dev/null" >/dev/null 2>&1 || return 0
      sleep 1
    done
    echo "WARN=RUNNER_TERM_TIMEOUT_FORCING_KILL"
    docker exec "$CONTAINER" sh -c "test -s '$PIDFILE' && kill -KILL \$(cat '$PIDFILE')" >/dev/null 2>&1 || true
  fi
}

on_stop() {
  echo "CONTROLLED_STOP=REQUESTED"
  stop_container_runner
  if [[ -n "$CHILD" ]]; then
    wait "$CHILD" 2>/dev/null || true
  fi
  echo "CONTROLLED_STOP=COMPLETE"
  exit 0
}
trap on_stop TERM INT

# Defensive cleanup: there must never be an old exec-owned runner before this
# service starts. Owning the canonical host lock makes killing a stale pid safe.
stop_container_runner

docker exec -i \
  -e PPI_HISTORY_RUN_ID=PPI-HIST-20260912-001 \
  -e PPI_HISTORY_TARGET_DAYS=365 \
  -e PPI_HISTORY_EXPECTED_IDENTITIES=1960 \
  -e PPI_HISTORY_MIN_FREE_GIB=3 \
  -e PPI_HISTORY_HARD_STOP_GIB=2 \
  -e PPI_HISTORY_INTEGRITY_EVERY=5 \
  -e PPI_HISTORY_SLEEP_SECONDS=0.25 \
  -e PPI_HISTORY_PIDFILE="$PIDFILE" \
  "$CONTAINER" python -u - < "$RUNNER" &
CHILD=$!

set +e
wait "$CHILD"
RC=$?
set -e
# If docker exec disconnected unexpectedly, do not release the lock while an
# orphaned writer survives in the container.
stop_container_runner

echo "RUNNER_EXIT=$RC"
exit "$RC"
