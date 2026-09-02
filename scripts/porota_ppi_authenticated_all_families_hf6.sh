#!/usr/bin/env bash
set -euo pipefail

OBS="porota_production_observer"
PATCH_ROOT="/opt/porota-runtime-patches/hf6-contract-evidence"
OUT_DIR="/opt/porota-trading/data/contract_evidence/ppi_web_all_families"
OUT="$OUT_DIR/latest.json"
CRED="/etc/credstore.encrypted/porota-ppi-web.cred"

if [ "$(docker inspect --format='{{.State.Status}}' "$OBS" 2>/dev/null || true)" != "running" ]; then
  echo "STATUS=SKIPPED_OBSERVER_NOT_RUNNING"
  exit 0
fi

IMAGE="$(docker inspect --format='{{.Config.Image}}' "$OBS")"
READONLY="$(docker inspect --format='{{.HostConfig.ReadonlyRootfs}}' "$OBS")"
case "$IMAGE" in
  porota-trading-bot:17.0.0-rc3-hf6*) ;;
  *) echo "STATUS=FAIL_CLOSED_UNEXPECTED_IMAGE:$IMAGE"; exit 3 ;;
esac
[ "$READONLY" = "true" ] || { echo "STATUS=FAIL_CLOSED_OBSERVER_NOT_READONLY"; exit 3; }

sudo -n test -s "$CRED" || { echo "STATUS=SKIPPED_ENCRYPTED_CREDENTIAL_NOT_FOUND"; exit 0; }
test -s "$PATCH_ROOT/cn_ppi_authenticated_family_scraper_hf6.py" || { echo "STATUS=FAIL_COLLECTOR_NOT_INSTALLED"; exit 3; }

install -d -m 0750 -o 1000 -g 1000 "$OUT_DIR"
CODE="$(cat "$PATCH_ROOT/cn_ppi_authenticated_family_scraper_hf6.py")"
TMP="$(mktemp /dev/shm/porota-ppi-web-XXXXXX.json)"
trap 'rm -f "$TMP"' EXIT
chmod 600 "$TMP"

# Credential is decrypted only into tmpfs and piped directly to the immutable
# observer's Python process. It is never printed, committed, or written to DB.
sudo -n systemd-creds decrypt "$CRED" - 2>/dev/null \
  | docker exec -i -e PYTHONDONTWRITEBYTECODE=1 "$OBS" python -c "$CODE" > "$TMP"

python3 - "$TMP" <<'PY'
import json,sys
p=sys.argv[1]
d=json.load(open(p,encoding='utf-8'))
# Fail closed if the collector ever reports a mutation/order attempt.
s=d.get('safety') or {}
if int(s.get('order_posts',0) or 0) != 0 or int(s.get('mutation_requests',0) or 0) != 0:
    raise SystemExit('SAFETY_ASSERTION_FAILED')
print('AUTH_STATUS=' + str((d.get('auth') or {}).get('status','UNKNOWN')))
print('LOGIN_POSTS=' + str(s.get('credential_login_posts',0)))
print('ORDER_POSTS=' + str(s.get('order_posts',0)))
print('MUTATION_REQUESTS=' + str(s.get('mutation_requests',0)))
for fam,info in sorted((d.get('families') or {}).items()):
    print(f"FAMILY={fam} STATUS={info.get('status')} SOURCES={len(info.get('sources') or [])}")
PY

install -m 0640 -o root -g porotaadmin "$TMP" "$OUT"
python3 -m json.tool "$OUT" >/dev/null

echo "STATUS=OK"
echo "OUTPUT=$OUT"
echo "SECRETS_PERSISTED=NO"
echo "TOKENS_PERSISTED=NO"
echo "RAW_HTML_PERSISTED=NO"
echo "ORDERS_ATTEMPTED=0"
