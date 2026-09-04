#!/bin/sh
set -eu

LOCK=/run/porota-introspection-publish/lock
SOURCE_ROOT=/opt/porota-trading
PUBLISH_ROOT="$SOURCE_ROOT/data/introspection_publish"
REPOSITORY=/var/lib/porota-observability/repo
BRANCH=runtime-observability
RETENTION_DAYS=90
STATUS_RECORDED=0

record_failure() {
  RC=$?
  trap - EXIT HUP INT TERM
  if [ "$STATUS_RECORDED" -eq 0 ] && [ "$RC" -ne 0 ]; then
    set +e
    docker exec porota_production_observer python /app/ops_publish_introspection_hf6.py \
      --output-dir /app/data/introspection_publish --record-status FAILED \
      --detail "PUBLISHER_EXIT_$RC" --retention-days "$RETENTION_DAYS" >/dev/null 2>&1
  fi
  exit "$RC"
}

exec 9>"$LOCK"
flock -n 9 || exit 0
trap record_failure EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

docker exec porota_production_observer python /app/ops_publish_introspection_hf6.py \
  --input-dir /app/data/introspection --output-dir /app/data/introspection_publish

test -f "$PUBLISH_ROOT/latest.json"
test -d "$REPOSITORY/.git"
test "$(git -C "$REPOSITORY" branch --show-current)" = "$BRANCH"
git -C "$REPOSITORY" pull --ff-only origin "$BRANCH"

DAY=$(python3 -c 'import json; print(str(json.load(open("/opt/porota-trading/data/introspection_publish/latest.json", encoding="utf-8"))["timestamp"])[:10])')
case "$DAY" in
  ????-??-??) ;;
  *) echo 'INVALID_INTROSPECTION_DAY' >&2; exit 1 ;;
esac

mkdir -p "$REPOSITORY/runtime/introspection/daily"
install -m 0644 "$PUBLISH_ROOT/latest.json" "$REPOSITORY/runtime/introspection/latest.json"
install -m 0644 "$PUBLISH_ROOT/daily/$DAY.json" "$REPOSITORY/runtime/introspection/daily/$DAY.json"
git -C "$REPOSITORY" add -- runtime/introspection/latest.json "runtime/introspection/daily/$DAY.json"
if git -C "$REPOSITORY" diff --cached --quiet; then
  PUBLISH_STATUS=UNCHANGED
else
  git -C "$REPOSITORY" -c user.name='Porota Runtime' -c user.email='runtime@porota.invalid' \
    commit -m "observability: introspection $DAY"
  git -C "$REPOSITORY" push origin "$BRANCH"
  PUBLISH_STATUS=PUBLISHED
fi

# Sólo después de confirmar GitHub (o confirmar que no había diferencias remotas)
# se permite retirar horarios locales respaldados por un diario válido.
docker exec porota_production_observer python /app/ops_publish_introspection_hf6.py \
  --input-dir /app/data/introspection --output-dir /app/data/introspection_publish \
  --prune-only --retention-days "$RETENTION_DAYS"
docker exec porota_production_observer python /app/ops_publish_introspection_hf6.py \
  --output-dir /app/data/introspection_publish --record-status "$PUBLISH_STATUS" \
  --day "$DAY" --retention-days "$RETENTION_DAYS"
STATUS_RECORDED=1
trap - EXIT HUP INT TERM
printf '{"status":"%s","retention_days":%s}\n' "$PUBLISH_STATUS" "$RETENTION_DAYS"
