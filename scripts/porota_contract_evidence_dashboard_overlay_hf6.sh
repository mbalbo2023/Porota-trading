#!/usr/bin/env bash
set -euo pipefail

PATCH_ROOT="/opt/porota-runtime-patches/hf6-contract-evidence"
DASH="porota_production_dashboard"
TMP="$(mktemp -d /tmp/porota-contract-dashboard-XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

for _ in $(seq 1 60); do
  if [ "$(docker inspect --format='{{.State.Status}}' "$DASH" 2>/dev/null || true)" = "running" ]; then
    break
  fi
  sleep 2
done

if [ "$(docker inspect --format='{{.State.Status}}' "$DASH" 2>/dev/null || true)" != "running" ]; then
  echo "STATUS=DASHBOARD_NOT_RUNNING"
  exit 3
fi

IMAGE="$(docker inspect --format='{{.Config.Image}}' "$DASH")"
case "$IMAGE" in
  porota-trading-bot:17.0.0-rc3-hf6*) ;;
  *) echo "STATUS=FAIL_CLOSED_UNEXPECTED_IMAGE:$IMAGE"; exit 3 ;;
esac

test -f "$PATCH_ROOT/cl_contract_evidence_dashboard_hf6.py"
docker cp "$DASH:/app/o_dashboard.py" "$TMP/o_dashboard.py"

python3 - "$TMP/o_dashboard.py" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
text=p.read_text(encoding='utf-8')
anchor="""# HF6 dashboard-only: vista unificada de universo, capacidades y operaciones.\nimport bh_universe_dashboard_hf6\nbh_universe_dashboard_hf6.install(app, _check_auth)\n"""
addition=anchor+"""\n# HF6 contract-evidence overlay: provider/source ownership on the same universe page.\nimport cl_contract_evidence_dashboard_hf6\n"""
if "import cl_contract_evidence_dashboard_hf6" not in text:
    if anchor not in text:
        raise SystemExit("UNIVERSE_DASHBOARD_ANCHOR_NOT_FOUND")
    text=text.replace(anchor,addition,1)
p.write_text(text,encoding='utf-8')
PY

python3 -m py_compile "$TMP/o_dashboard.py" "$PATCH_ROOT/cl_contract_evidence_dashboard_hf6.py"
docker cp "$PATCH_ROOT/cl_contract_evidence_dashboard_hf6.py" "$DASH:/app/cl_contract_evidence_dashboard_hf6.py"
docker cp "$TMP/o_dashboard.py" "$DASH:/app/o_dashboard.py"
docker restart "$DASH" >/dev/null

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "STATUS=OK"
    exit 0
  fi
  sleep 1
done

echo "STATUS=DASHBOARD_HEALTH_TIMEOUT"
exit 4
