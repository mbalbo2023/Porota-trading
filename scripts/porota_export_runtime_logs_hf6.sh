#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

ROOT="${POROTA_ROOT:-/opt/porota-trading}"
OUTDIR="${POROTA_SHARED_LOG_DIR:-$ROOT/data/logs}"
TAIL_LINES="${POROTA_LOG_EXPORT_LINES:-5000}"
LOCK="/run/lock/porota-log-export-hf6.lock"

mkdir -p "$OUTDIR"
exec 9>"$LOCK"
flock -n 9 || exit 0

sanitize() {
  python3 -c '
import re,sys
patterns=[
 (re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"), r"\1[REDACTED]"),
 (re.compile(r"(?i)((?:api[_-]?key|api[_-]?secret|client[_-]?key|client[_-]?secret|password|passwd|token|cookie|session|chat[_-]?id|account(?:[_-]?number)?)\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
 (re.compile(r"(?i)(X-(?:Auth-Token|Username|Password)\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
 (re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b"), "[REDACTED_JWT]"),
]
for raw in sys.stdin:
    line=raw.rstrip("\n")[:12000]
    for rx,repl in patterns:
        line=rx.sub(repl,line)
    print(line)
'
}

export_one() {
  local container="$1" filename="$2" tmp
  tmp="$(mktemp "$OUTDIR/.${filename}.XXXXXX")"
  if sudo -n docker inspect "$container" >/dev/null 2>&1; then
    sudo -n docker logs --tail "$TAIL_LINES" "$container" 2>&1 | sanitize >"$tmp" || true
  else
    printf 'Container %s no disponible al %s\n' "$container" "$(date -Is)" >"$tmp"
  fi
  chmod 0640 "$tmp"
  chown 1000:1000 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$OUTDIR/$filename"
}

export_one porota_production_observer observer_runtime.log
export_one porota_production_dashboard dashboard_runtime.log

# Bot/application log: prefer the rotated application file inside the observer;
# if unavailable, export the observer process log as an explicitly labelled
# runtime fallback. Both paths are sanitized before publishing to the dashboard.
BOT_TMP="$(mktemp "$OUTDIR/.bot_runtime.log.XXXXXX")"
if sudo -n docker inspect porota_production_observer >/dev/null 2>&1; then
  if sudo -n docker exec porota_production_observer sh -c 'test -f /app/data/logs/trading_bot.log' >/dev/null 2>&1; then
    sudo -n docker exec porota_production_observer sh -c 'tail -n '"$TAIL_LINES"' /app/data/logs/trading_bot.log' 2>&1 | sanitize >"$BOT_TMP" || true
  else
    sudo -n docker logs --tail "$TAIL_LINES" porota_production_observer 2>&1 | sanitize >"$BOT_TMP" || true
  fi
else
  printf 'Bot/observer no disponible al %s\n' "$(date -Is)" >"$BOT_TMP"
fi
chmod 0640 "$BOT_TMP"
chown 1000:1000 "$BOT_TMP" 2>/dev/null || true
mv -f "$BOT_TMP" "$OUTDIR/bot_runtime.log"

# Manifest contains metadata only, never log contents or secrets.
python3 - "$OUTDIR" <<'PY'
import json,sys
from pathlib import Path
from datetime import datetime,timezone
root=Path(sys.argv[1])
items=[]
for name in ("observer_runtime.log","bot_runtime.log","dashboard_runtime.log"):
    p=root/name
    if p.exists():
        st=p.stat()
        items.append({"file":name,"bytes":st.st_size,"mtime_ns":st.st_mtime_ns})
(root/"runtime_log_export_status.json").write_text(json.dumps({
    "recorded_at":datetime.now(timezone.utc).isoformat(),
    "state":"OK",
    "files":items,
},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
chmod 0640 "$OUTDIR/runtime_log_export_status.json"
chown 1000:1000 "$OUTDIR/runtime_log_export_status.json" 2>/dev/null || true

true
