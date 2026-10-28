#!/bin/sh
set -eu

# Dedicated publisher for WS-INFRA-12.  It does not invoke Docker, systemd
# mutation, the legacy introspector, DB writers, order clients or retention.
LOCK=/run/porota-observability-publish/lock
SOURCE_ROOT=/opt/porota-trading
DB="$SOURCE_ROOT/data/paper_v17/observer_v17.db"
STAGING=/var/lib/porota-runtime-evidence/staging
REPOSITORY=/var/lib/porota-observability/repo
BRANCH=runtime-observability
LATEST=runtime/evidence/latest.json

exec 9>"$LOCK"
flock -n 9 || exit 0

test -f "$DB"
test -d "$REPOSITORY/.git"
test "$(git -C "$REPOSITORY" branch --show-current)" = "$BRANCH"
test -z "$(git -C "$REPOSITORY" status --porcelain)"

git -C "$REPOSITORY" fetch origin "$BRANCH"
LOCAL_BEFORE="$(git -C "$REPOSITORY" rev-parse HEAD)"
REMOTE_BEFORE="$(git -C "$REPOSITORY" rev-parse "origin/$BRANCH")"
test "$LOCAL_BEFORE" = "$REMOTE_BEFORE"

mkdir -p "$STAGING" "$REPOSITORY/runtime/evidence/daily"
PREVIOUS=
if [ -f "$REPOSITORY/$LATEST" ]; then
  PREVIOUS="--previous=$REPOSITORY/$LATEST"
fi

python3 "$SOURCE_ROOT/ops_runtime_evidence_rc6.py" \
  --db "$DB" \
  --operation-mode "$SOURCE_ROOT/data/operation_mode.json" \
  --deploy-state "$SOURCE_ROOT/data/deploy/CURRENT_STATE_V2.json" \
  --frozen-candidate "$SOURCE_ROOT/data/deploy/porota-frozen-candidate.json" \
  --artifact-manifest "$SOURCE_ROOT/data/deploy/porota-deploy-bundle-v2-manifest.json" \
  --staging-root "$STAGING" \
  --output "$STAGING/latest.json" \
  ${PREVIOUS:+"$PREVIOUS"}

python3 "$SOURCE_ROOT/ops_runtime_evidence_rc6.py" \
  --db "$DB" --staging-root "$STAGING" --output "$STAGING/unused.json" \
  --validate "$STAGING/latest.json"

DAY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["generated_at"][:10])' "$STAGING/latest.json")"
case "$DAY" in
  ????-??-??) ;;
  *) echo 'RUNTIME_EVIDENCE_DAY_INVALID' >&2; exit 1 ;;
esac

install -m 0644 "$STAGING/latest.json" "$REPOSITORY/$LATEST"
install -m 0644 "$STAGING/latest.json" "$REPOSITORY/runtime/evidence/daily/$DAY.json"
git -C "$REPOSITORY" add -- "$LATEST" "runtime/evidence/daily/$DAY.json"

if git -C "$REPOSITORY" diff --cached --quiet; then
  PUBLISHED_COMMIT="$(git -C "$REPOSITORY" rev-parse HEAD)"
  PUBLISH_STATUS=UNCHANGED
else
  git -C "$REPOSITORY" -c user.name='Porota Runtime Evidence' \
    -c user.email='runtime-evidence@porota.invalid' \
    commit -m "observability: runtime evidence $DAY"
  PUBLISHED_COMMIT="$(git -C "$REPOSITORY" rev-parse HEAD)"
  git -C "$REPOSITORY" push origin "HEAD:$BRANCH"
  PUBLISH_STATUS=PUBLISHED
fi

REMOTE_AFTER="$(git -C "$REPOSITORY" ls-remote origin "refs/heads/$BRANCH" | awk '{print $1}')"
test "$REMOTE_AFTER" = "$PUBLISHED_COMMIT"
PUBLISHED_BLOB="$(git -C "$REPOSITORY" rev-parse "$PUBLISHED_COMMIT:$LATEST")"
test -n "$PUBLISHED_BLOB"

printf '{"status":"%s","branch":"%s","commit_sha":"%s","blob_sha":"%s","path":"%s"}\n' \
  "$PUBLISH_STATUS" "$BRANCH" "$PUBLISHED_COMMIT" "$PUBLISHED_BLOB" "$LATEST"
