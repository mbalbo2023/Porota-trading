#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"
: "${REMOTE_STAGE:?REMOTE_STAGE required}"
LIVE_IMAGE="${LIVE_IMAGE:-porota-trading-bot:17.0.0-rc6}"
GATEWAY_CONTAINER="${GATEWAY_CONTAINER:-porota_critical_approval_rc6}"

REPO=/opt/porota-trading
BASE=/opt/porota-control-plane-rc6
RELEASE="$BASE/releases/$SOURCE_SHA"
CURRENT="$BASE/current"
SECRETS="$BASE/secrets"
DATA="$BASE/data"
PROXY_UNIT=/etc/systemd/system/porota-critical-github-proxy-rc6.service
GATEWAY_UNIT=/etc/systemd/system/porota-critical-approval-rc6.service
CONTROL_USER="$(id -un)"
CONTROL_GROUP="$(id -gn)"
CONTROL_HOME="$(getent passwd "$CONTROL_USER" | cut -d: -f6)"
BROKER_HOST_KEY="$SECRETS/broker_capability.host"
BROKER_CONTAINER_KEY="$SECRETS/broker_capability.token"
installed=0

cleanup() {
  rc=$?
  if [[ $rc -ne 0 && $installed -eq 1 ]]; then
    echo "CONTROL_PLANE_ROLLBACK=START rc=$rc"
    sudo -n systemctl disable --now porota-critical-approval-rc6.service >/dev/null 2>&1 || true
    sudo -n systemctl disable --now porota-critical-github-proxy-rc6.service >/dev/null 2>&1 || true
    sudo -n docker rm -f "$GATEWAY_CONTAINER" >/dev/null 2>&1 || true
    sudo -n rm -f "$PROXY_UNIT" "$GATEWAY_UNIT" >/dev/null 2>&1 || true
    sudo -n rm -f "$SECRETS/critical_telegram.token" "$SECRETS/critical_telegram.chat" "$BROKER_HOST_KEY" "$BROKER_CONTAINER_KEY" >/dev/null 2>&1 || true
    sudo -n systemctl daemon-reload >/dev/null 2>&1 || true
    echo "CONTROL_PLANE_ROLLBACK=COMPLETE"
  fi
  sudo -n rm -rf "$REMOTE_STAGE" >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT

extract_env() {
  local key="$1" value
  value="$(awk -F= -v k="$key" '$1==k {v=$0; sub(/^[^=]*=/,"",v); print v; exit}' "$REPO/.env")"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'*\' && "$value" == *\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

observer_db_state() {
  sudo -n docker exec -i porota_production_observer python - <<'PY'
import sqlite3
p='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=20)
c.execute('PRAGMA query_only=ON')
q=c.execute('PRAGMA quick_check').fetchone()[0]
row=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
c.close(); print('|'.join(map(str,(q,*row))))
PY
}

echo '=== RC6 CRITICAL CONTROL PLANE PRECHECK ==='
test -d "$REPO/.git"
test -f "$REPO/.env"
for f in fg_critical_approval_gateway_rc6.py fm_critical_approval_unix_runtime_rc6.py fn_critical_github_proxy_rc6.py; do
  test -f "$REMOTE_STAGE/$f"
done

obs_id="$(sudo -n docker inspect -f '{{.Id}}' porota_production_observer)"
dash_id="$(sudo -n docker inspect -f '{{.Id}}' porota_production_dashboard)"
obs="$(sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}|{{json .Config.Cmd}}' porota_production_observer)"
dash="$(sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.Config.Image}}' porota_production_dashboard)"
echo "OBSERVER_PRE=$obs"
echo "DASHBOARD_PRE=$dash"
case "$obs" in 'true|0|true|'*'bv_paper_runtime.py'*) ;; *) echo 'OBSERVER_PRE=RED'; exit 1;; esac
case "$dash" in 'true|0|'*) ;; *) echo 'DASHBOARD_PRE=RED'; exit 1;; esac
state="$(observer_db_state)"
echo "OBSERVER_DB_PRE=$state"
test "$state" = 'ok|PRODUCTION_PAPER|0'

if sudo -n docker ps -aq -f name="^/${GATEWAY_CONTAINER}$" | grep -q .; then echo 'EXISTING_GATEWAY=RED'; exit 1; fi
if systemctl list-unit-files --no-legend 2>/dev/null | grep -Eq '^porota-critical-(approval|github-proxy)-rc6\.service'; then echo 'EXISTING_UNIT=RED'; exit 1; fi
for legacy in porota_trading_bot porota_sandbox_engine porota_production_engine; do
  if sudo -n docker ps --format '{{.Names}}' | grep -Fxq "$legacy"; then echo "LEGACY_TELEGRAM_CONSUMER=$legacy"; exit 1; fi
done
if ps -eo args= | grep -E '[j]_[m]ain\.py|[l]_[o]rder_confirmation\.py|[a]r_telegram_commands\.py' >/dev/null; then echo 'HOST_LEGACY_TELEGRAM_PROCESS=RED'; exit 1; fi

command -v gh >/dev/null
gh auth status -h github.com >/dev/null 2>&1
gh api 'repos/mbalbo2023/Porota-trading/issues/40' >/dev/null
echo 'GH_HOST_AUTH=AVAILABLE_NOT_EXPOSED_TO_CONTAINER'

telegram_token="$(extract_env TELEGRAM_BOT_TOKEN)"
telegram_chat="$(extract_env TELEGRAM_CHAT_ID)"
test -n "$telegram_token" && test -n "$telegram_chat"
echo 'CANONICAL_TELEGRAM_CREDENTIALS=PRESENT_VALUES_NOT_LOGGED'
TELEGRAM_TOKEN="$telegram_token" python3 - <<'PY'
import json, os, urllib.request
url=f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/getWebhookInfo"
with urllib.request.urlopen(url, timeout=15) as r:
    obj=json.load(r)
assert obj.get('ok') is True, obj
assert not str((obj.get('result') or {}).get('url') or ''), 'Telegram webhook is configured; getUpdates consumer would be invalid'
print('CANONICAL_TELEGRAM_WEBHOOK=NONE')
PY

echo '=== INSTALL ISOLATED CONTROL PLANE ==='
sudo -n install -d -m 0755 "$BASE" "$BASE/releases" "$RELEASE"
for f in fg_critical_approval_gateway_rc6.py fm_critical_approval_unix_runtime_rc6.py fn_critical_github_proxy_rc6.py; do
  sudo -n install -m 0644 "$REMOTE_STAGE/$f" "$RELEASE/$f"
done
sudo -n install -d -m 0711 "$SECRETS"
sudo -n chown root:root "$SECRETS"
sudo -n install -d -o 1000 -g 1000 -m 0750 "$DATA"
printf '%s' "$telegram_token" | sudo -n tee "$SECRETS/critical_telegram.token" >/dev/null
printf '%s' "$telegram_chat" | sudo -n tee "$SECRETS/critical_telegram.chat" >/dev/null
sudo -n chown 1000:1000 "$SECRETS/critical_telegram.token" "$SECRETS/critical_telegram.chat"
sudo -n chmod 0400 "$SECRETS/critical_telegram.token" "$SECRETS/critical_telegram.chat"

echo 'SECRETS_DIRECTORY=0711_ROOT_TRAVERSE_ONLY'
capability="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
test "${#capability}" -eq 64
printf '%s' "$capability" > /tmp/porota-broker-capability.host
printf '%s' "$capability" > /tmp/porota-broker-capability.token
sudo -n install -o "$CONTROL_USER" -g "$CONTROL_GROUP" -m 0400 /tmp/porota-broker-capability.host "$BROKER_HOST_KEY"
sudo -n install -o 1000 -g 1000 -m 0400 /tmp/porota-broker-capability.token "$BROKER_CONTAINER_KEY"
rm -f /tmp/porota-broker-capability.host /tmp/porota-broker-capability.token
unset capability
echo 'BROKER_LOCAL_CAPABILITY=GENERATED_VALUE_NOT_LOGGED'

sudo -n ln -sfn "$RELEASE" "$CURRENT"

cat > /tmp/porota-critical-github-proxy-rc6.service <<UNIT
[Unit]
Description=POROTA RC6 critical GitHub Issues capability broker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CONTROL_USER
Group=$CONTROL_GROUP
Environment=HOME=$CONTROL_HOME
Environment=GH_CONFIG_DIR=$CONTROL_HOME/.config/gh
Environment=POROTA_CRITICAL_GITHUB_SOCKET=/run/porota-critical-approval-rc6/github.sock
Environment=POROTA_CRITICAL_BROKER_TOKEN_FILE=$BROKER_HOST_KEY
RuntimeDirectory=porota-critical-approval-rc6
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/python3 /opt/porota-control-plane-rc6/current/fn_critical_github_proxy_rc6.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

cat > /tmp/porota-critical-approval-rc6.service <<UNIT
[Unit]
Description=POROTA RC6 critical Telegram approval gateway
Requires=docker.service porota-critical-github-proxy-rc6.service
After=docker.service porota-critical-github-proxy-rc6.service

[Service]
Type=simple
ExecStartPre=/bin/sh -c 'i=0; while [ ! -S /run/porota-critical-approval-rc6/github.sock ]; do i=\$((i+1)); [ \$i -lt 30 ] || exit 1; sleep 1; done'
ExecStartPre=-/usr/bin/docker rm -f $GATEWAY_CONTAINER
ExecStart=/usr/bin/docker run --rm --name $GATEWAY_CONTAINER --pull never --read-only --user botuser --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 64 --memory 192m --tmpfs /tmp:rw,noexec,nosuid,size=32m -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/code -e POROTA_RUNTIME_MODE=PRODUCTION_PAPER -e POROTA_CRITICAL_APPROVAL_ENABLED=true -e POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED=true -e POROTA_CRITICAL_APPROVAL_POLL_SECONDS=10 -e POROTA_CRITICAL_APPROVAL_DB=/data/critical_approval_rc6.db -e POROTA_CRITICAL_GITHUB_SOCKET=/run/control/github.sock -e POROTA_CRITICAL_BROKER_TOKEN_FILE=/run/secrets/broker_capability.token -e POROTA_CRITICAL_TELEGRAM_TOKEN_FILE=/run/secrets/critical_telegram.token -e POROTA_CRITICAL_TELEGRAM_CHAT_FILE=/run/secrets/critical_telegram.chat -v /opt/porota-control-plane-rc6/current:/code:ro -v /opt/porota-control-plane-rc6/secrets/broker_capability.token:/run/secrets/broker_capability.token:ro -v /opt/porota-control-plane-rc6/secrets/critical_telegram.token:/run/secrets/critical_telegram.token:ro -v /opt/porota-control-plane-rc6/secrets/critical_telegram.chat:/run/secrets/critical_telegram.chat:ro -v /opt/porota-control-plane-rc6/data:/data:rw -v /run/porota-critical-approval-rc6:/run/control:rw --entrypoint python $LIVE_IMAGE /code/fm_critical_approval_unix_runtime_rc6.py
ExecStop=-/usr/bin/docker stop -t 10 $GATEWAY_CONTAINER
Restart=always
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

sudo -n install -m 0644 /tmp/porota-critical-github-proxy-rc6.service "$PROXY_UNIT"
sudo -n install -m 0644 /tmp/porota-critical-approval-rc6.service "$GATEWAY_UNIT"
rm -f /tmp/porota-critical-github-proxy-rc6.service /tmp/porota-critical-approval-rc6.service
installed=1
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now porota-critical-github-proxy-rc6.service
for _ in $(seq 1 30); do [[ -S /run/porota-critical-approval-rc6/github.sock ]] && break; sleep 1; done
test -S /run/porota-critical-approval-rc6/github.sock
sudo -n systemctl enable --now porota-critical-approval-rc6.service

echo '=== POSTFLIGHT ==='
sleep 15
test "$(sudo -n systemctl is-active porota-critical-github-proxy-rc6.service)" = active
test "$(sudo -n systemctl is-active porota-critical-approval-rc6.service)" = active
running="$(sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}|{{.Config.User}}' "$GATEWAY_CONTAINER")"
echo "CONTROL_PLANE_CONTAINER=$running"
test "$running" = "true|0|true|$LIVE_IMAGE|botuser"
mounts="$(sudo -n docker inspect -f '{{range .Mounts}}{{.Source}}=>{{.Destination}};{{end}}' "$GATEWAY_CONTAINER")"
case "$mounts" in *docker.sock*|*ppi_production*|*'/opt/porota-trading/.env'*) echo 'FORBIDDEN_MOUNT=RED'; exit 1;; esac
echo 'FORBIDDEN_MOUNT=NONE'

sudo -n docker inspect "$GATEWAY_CONTAINER" | python3 -c 'import json,sys; o=json.load(sys.stdin)[0]; n={x.split("=",1)[0] for x in o["Config"].get("Env",[])}; f={"PPI_API_KEY","PPI_API_SECRET","PPI_API_KEY_PROD","PPI_API_SECRET_PROD","PPI_ACCOUNT_NUMBER","PPI_PRODUCTION_SECRET_FILE","GITHUB_TOKEN","GH_TOKEN","POROTA_GITHUB_ISSUE_CONTROL_TOKEN","TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID"}; b=sorted(n&f); assert not b,b; print("CONTAINER_SECRET_ENV_GUARD=GREEN")'

proxy_log="$(sudo -n journalctl -u porota-critical-github-proxy-rc6.service -n 80 --no-pager)"
gateway_log="$(sudo -n journalctl -u porota-critical-approval-rc6.service -n 120 --no-pager)"
printf '%s\n' "$proxy_log" | grep -q 'critical-github-broker: READY'
printf '%s\n' "$proxy_log" | grep -q 'auth=local-key'
printf '%s\n' "$gateway_log" | grep -q 'critical-approval-runtime: READY'
if printf '%s\n' "$gateway_log" | grep -Eiq 'HTTP_409|terminated by other getUpdates|TELEGRAM_SINGLE_CONSUMER_NOT_ENFORCED'; then echo 'TELEGRAM_SINGLE_CONSUMER=RED'; exit 1; fi
echo 'TELEGRAM_SINGLE_CONSUMER=GREEN'

sudo -n docker exec "$GATEWAY_CONTAINER" python -c 'from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient; h=UnixGithubIssuesClient().health(); assert h.get("status")=="ok" and h.get("capability")=="issues_only",h; print("ISSUES_CAPABILITY_BROKER=GREEN")'

obs_post_id="$(sudo -n docker inspect -f '{{.Id}}' porota_production_observer)"
dash_post_id="$(sudo -n docker inspect -f '{{.Id}}' porota_production_dashboard)"
test "$obs_post_id" = "$obs_id"
test "$dash_post_id" = "$dash_id"
obs_post="$(sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}|{{json .Config.Cmd}}' porota_production_observer)"
dash_post="$(sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.Config.Image}}' porota_production_dashboard)"
echo "OBSERVER_POST=$obs_post"
echo "DASHBOARD_POST=$dash_post"
case "$obs_post" in 'true|0|true|'*'bv_paper_runtime.py'*) ;; *) exit 1;; esac
case "$dash_post" in 'true|0|'*) ;; *) exit 1;; esac
state_post="$(observer_db_state)"
echo "OBSERVER_DB_POST=$state_post"
test "$state_post" = 'ok|PRODUCTION_PAPER|0'
echo 'REAL_ORDERS_SENT=0'
echo 'TRADING_RUNTIME_UNCHANGED=GREEN'
echo "CONTROL_PLANE_SOURCE_SHA=$SOURCE_SHA"
echo 'CONTROL_PLANE_PERMANENT_DEPLOY=GREEN'

sudo -n rm -rf "$REMOTE_STAGE"
trap - EXIT