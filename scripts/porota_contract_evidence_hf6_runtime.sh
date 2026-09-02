#!/usr/bin/env bash
set -euo pipefail

PATCH_ROOT="/opt/porota-runtime-patches/hf6-contract-evidence"
CONTAINER="porota_production_observer"
RUNTIME_DIR="/tmp/porota_contract_evidence_hf6"

if [ "$(docker inspect --format='{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)" != "running" ]; then
  echo "STATUS=SKIPPED_OBSERVER_NOT_RUNNING"
  exit 0
fi

IMAGE="$(docker inspect --format='{{.Config.Image}}' "$CONTAINER")"
case "$IMAGE" in
  porota-trading-bot:17.0.0-rc3-hf6*) ;;
  *) echo "STATUS=FAIL_CLOSED_UNEXPECTED_IMAGE:$IMAGE"; exit 3 ;;
esac

docker exec "$CONTAINER" rm -rf "$RUNTIME_DIR"
docker exec "$CONTAINER" mkdir -p "$RUNTIME_DIR"

for file in \
  ci_ppi_bond_estimate_patch_hf6.py \
  ch_contract_evidence_hf6.py \
  cm_special_family_discovery_hf6.py \
  ck_contract_evidence_runner_hf6.py
do
  test -f "$PATCH_ROOT/$file"
  docker cp "$PATCH_ROOT/$file" "$CONTAINER:$RUNTIME_DIR/$file"
done

docker exec \
  -e PYTHONPATH="$RUNTIME_DIR:/app" \
  "$CONTAINER" \
  python "$RUNTIME_DIR/ck_contract_evidence_runner_hf6.py"
