#!/usr/bin/env bash
set -euo pipefail

PATCH_ROOT="/opt/porota-runtime-patches/hf6-contract-evidence"
HOST_RUNTIME_DIR="/opt/porota-trading/data/runtime_patches/hf6_contract_evidence"
CONTAINER_RUNTIME_DIR="/app/data/runtime_patches/hf6_contract_evidence"
CONTAINER="porota_production_observer"

if [ "$(docker inspect --format='{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)" != "running" ]; then
  echo "STATUS=SKIPPED_OBSERVER_NOT_RUNNING"
  exit 0
fi

IMAGE="$(docker inspect --format='{{.Config.Image}}' "$CONTAINER")"
READONLY="$(docker inspect --format='{{.HostConfig.ReadonlyRootfs}}' "$CONTAINER")"
case "$IMAGE" in
  porota-trading-bot:17.0.0-rc3-hf6*) ;;
  *) echo "STATUS=FAIL_CLOSED_UNEXPECTED_IMAGE:$IMAGE"; exit 3 ;;
esac

if [ "$READONLY" != "true" ]; then
  echo "STATUS=FAIL_CLOSED_OBSERVER_NOT_READONLY:$READONLY"
  exit 3
fi

# The HF6 observer rootfs is intentionally immutable. Never docker-cp code to
# /tmp or /app inside that rootfs. Stage the additive evidence modules on the
# existing writable /app/data bind mount and execute them from there.
install -d -m 0750 -o 1000 -g 1000 "$HOST_RUNTIME_DIR"

for file in \
  ci_ppi_bond_estimate_patch_hf6.py \
  ch_contract_evidence_hf6.py \
  cm_special_family_discovery_hf6.py \
  ck_contract_evidence_runner_hf6.py
do
  test -f "$PATCH_ROOT/$file"
  install -m 0640 -o 1000 -g 1000 "$PATCH_ROOT/$file" "$HOST_RUNTIME_DIR/$file"
done

# Prove the host staging directory is the same volume visible as /app/data.
docker exec "$CONTAINER" test -r "$CONTAINER_RUNTIME_DIR/ck_contract_evidence_runner_hf6.py"

docker exec \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH="$CONTAINER_RUNTIME_DIR:/app" \
  "$CONTAINER" \
  python "$CONTAINER_RUNTIME_DIR/ck_contract_evidence_runner_hf6.py"
