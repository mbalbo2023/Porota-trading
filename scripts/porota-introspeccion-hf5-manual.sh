#!/bin/sh
# El nombre operativo se conserva por accesibilidad/compatibilidad. La imagen
# activa decide la versión del snapshot (HF6 después del corte validado).
set -u

OUT=$(mktemp /tmp/porota-introspeccion-manual-XXXXXX.txt) || exit 1
trap 'rm -f "$OUT"' EXIT HUP INT TERM
docker exec porota_production_observer \
  python /app/ops_introspection_hf4.py --enqueue-critical >"$OUT" 2>&1
RC=$?
cat "$OUT"
if [ -t 1 ]; then
  printf '\033]52;c;%s\a' "$(base64 <"$OUT" | tr -d '\n')"
fi
exit "$RC"
