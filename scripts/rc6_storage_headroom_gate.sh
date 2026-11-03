#!/usr/bin/env bash
set -Eeuo pipefail

# POROTA RC6 dynamic storage headroom gate.
# Read-only: this script NEVER deletes files/images/cache and never mutates data.
# Exit codes: 0=ALLOW/REPORT, 20=PAUSE ingestion, 30=BLOCK deploy.

MODE="${1:-report}"
REPO="${REPO:-/opt/porota-trading}"
IMAGE_REF="${IMAGE_REF:-}"
PROJECTED_WRITE_BYTES="${PROJECTED_WRITE_BYTES:-536870912}"  # 512 MiB default next batch

case "$MODE" in
  deploy-pre|deploy-post|ingest-start|ingest-continue|report) ;;
  *) echo "STORAGE_GATE=ERROR|unsupported_mode=$MODE"; exit 64 ;;
esac

if [[ -z "$IMAGE_REF" ]]; then
  if command -v docker >/dev/null 2>&1 && sudo -n docker inspect porota_production_observer >/dev/null 2>&1; then
    IMAGE_REF="$(sudo -n docker inspect -f '{{.Config.Image}}' porota_production_observer)"
  else
    IMAGE_REF="porota-trading-bot:17.0.0-rc6"
  fi
fi

read -r TOTAL FREE INODES_TOTAL INODES_FREE < <(python3 - "$REPO" <<'PY'
import os,sys
st=os.statvfs(sys.argv[1])
print(st.f_frsize*st.f_blocks, st.f_frsize*st.f_bavail, st.f_files, st.f_favail)
PY
)

IMAGE_BYTES=0
if command -v docker >/dev/null 2>&1 && sudo -n docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
  IMAGE_BYTES="$(sudo -n docker image inspect -f '{{.Size}}' "$IMAGE_REF")"
fi
# Fail conservative when the reference image cannot be measured.
if [[ "$IMAGE_BYTES" -le 0 ]]; then
  IMAGE_BYTES=$((2 * 1024 * 1024 * 1024))
  echo "STORAGE_IMAGE_ESTIMATE=FALLBACK_2GIB|ref=$IMAGE_REF"
else
  echo "STORAGE_IMAGE_ESTIMATE=MEASURED|ref=$IMAGE_REF|bytes=$IMAGE_BYTES"
fi

OUT="$(python3 - "$TOTAL" "$FREE" "$IMAGE_BYTES" "$MODE" "$PROJECTED_WRITE_BYTES" <<'PY'
import sys
from rc6_storage_policy import calculate_storage_decision

total,free,image=map(int,sys.argv[1:4])
mode=sys.argv[4]
projected=int(sys.argv[5])
d=calculate_storage_decision(total_bytes=total,free_bytes=free,image_bytes=image,mode=mode,projected_write_bytes=projected)
for k,v in d.as_dict().items():
    print(f"{k.upper()}={v}")
PY
)"
printf '%s\n' "$OUT"

if [[ "$INODES_TOTAL" -gt 0 ]]; then
  INODE_FREE_PCT=$(( INODES_FREE * 100 / INODES_TOTAL ))
else
  INODE_FREE_PCT=0
fi
echo "INODES_TOTAL=$INODES_TOTAL"
echo "INODES_FREE=$INODES_FREE"
echo "INODES_FREE_PCT=$INODE_FREE_PCT"
if [[ "$INODE_FREE_PCT" -lt 10 ]]; then
  if [[ "$MODE" == ingest-* ]]; then
    echo 'STORAGE_GATE=PAUSE|reason=inode_headroom_below_10pct'
    exit 20
  elif [[ "$MODE" == report ]]; then
    echo 'STORAGE_GATE=REPORT|warning=inode_headroom_below_10pct'
    exit 0
  else
    echo 'STORAGE_GATE=BLOCK|reason=inode_headroom_below_10pct'
    exit 30
  fi
fi

DECISION="$(printf '%s\n' "$OUT" | awk -F= '$1=="DECISION"{print $2}')"
REASON="$(printf '%s\n' "$OUT" | awk -F= '$1=="REASON"{print $2}')"
case "$DECISION" in
  ALLOW)
    echo "STORAGE_GATE=ALLOW|mode=$MODE|reason=$REASON"
    exit 0
    ;;
  REPORT)
    echo "STORAGE_GATE=REPORT|mode=$MODE|reason=$REASON"
    exit 0
    ;;
  PAUSE)
    echo "STORAGE_GATE=PAUSE|mode=$MODE|reason=$REASON"
    exit 20
    ;;
  BLOCK)
    echo "STORAGE_GATE=BLOCK|mode=$MODE|reason=$REASON"
    exit 30
    ;;
  *)
    echo "STORAGE_GATE=ERROR|decision=$DECISION"
    exit 65
    ;;
esac
