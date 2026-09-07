#!/usr/bin/env bash
set -euo pipefail

PROFILE=/home/porotaadmin/porota-browser-lab/chrome-profile
LAB=/home/porotaadmin/porota-browser-lab
STATE=/opt/porota-trading/data/contract_evidence/rc6_trusted/runtime_state.json
VENV=/opt/porota-contract-evidence-venv
RUNTIME=/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh

safe_stat() {
  local label="$1" path="$2"
  if sudo -n test -e "$path" || sudo -n test -L "$path"; then
    sudo -n stat -c "${label}=PRESENT|owner=%U|group=%G|mode=%a|mtime=%y|type=%F" "$path" 2>/dev/null || true
  else
    echo "${label}=MISSING"
  fi
}

echo '=== RC6 CONTRACT EVIDENCE REAUTH PATH READONLY DIAGNOSTIC ==='
echo "UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "LOCAL=$(TZ=America/Argentina/Buenos_Aires date +%Y-%m-%dT%H:%M:%S%z)"

echo '--- SAFETY ---'
OBS="$(sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}' porota_production_observer 2>/dev/null || true)"
DASH="$(sudo -n docker inspect -f '{{.State.Running}}|{{.RestartCount}}|{{.Config.Image}}' porota_production_dashboard 2>/dev/null || true)"
echo "OBSERVER=$OBS"
echo "DASHBOARD=$DASH"
sudo -n docker exec -i porota_production_observer python - <<'PY' 2>/dev/null || true
import sqlite3
p='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=20); c.execute('pragma query_only=on')
qc=c.execute('pragma quick_check').fetchone()[0]
r=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone(); c.close()
print('OBSERVER_DB='+'|'.join(map(str,(qc,*r))))
PY

echo '--- CONTRACT EVIDENCE RUNTIME ---'
echo "TIMER_ENABLED=$(systemctl is-enabled porota-contract-evidence-rc6.timer 2>/dev/null || true)"
echo "TIMER_ACTIVE=$(systemctl is-active porota-contract-evidence-rc6.timer 2>/dev/null || true)"
echo "SERVICE_ACTIVE=$(systemctl is-active porota-contract-evidence-rc6.service 2>/dev/null || true)"
echo "VENV_PYTHON=$(test -x "$VENV/bin/python" && echo PRESENT || echo MISSING)"
if test -x "$VENV/bin/python"; then
  sudo -n "$VENV/bin/python" - <<'PY' 2>/dev/null || true
try:
 import importlib.metadata
 print('PLAYWRIGHT_VERSION='+importlib.metadata.version('playwright'))
except Exception as e:
 print('PLAYWRIGHT_VERSION=UNAVAILABLE:'+type(e).__name__)
PY
fi
echo "CHROME=$(command -v google-chrome-stable 2>/dev/null || true)"
echo "RUNTIME=$(test -x "$RUNTIME" && echo PRESENT_EXECUTABLE || echo MISSING)"
if sudo -n test -s "$STATE"; then
  sudo -n python3 - "$STATE" <<'PY' 2>/dev/null || true
import json,sys
try:
 d=json.load(open(sys.argv[1],encoding='utf-8'))
 print('BACKOFF_STATE='+str(d.get('state','UNKNOWN')))
 print('BACKOFF_BLOCKED_AT='+str(d.get('blocked_at','UNKNOWN')))
except Exception as e:
 print('BACKOFF_STATE=INVALID:'+type(e).__name__)
PY
else
  echo 'BACKOFF_STATE=NONE'
fi

echo '--- TRUSTED PROFILE METADATA ONLY ---'
safe_stat PROFILE "$PROFILE"
safe_stat PROFILE_DEFAULT "$PROFILE/Default"
safe_stat PROFILE_PREFERENCES "$PROFILE/Default/Preferences"
safe_stat PROFILE_SECURE_PREFERENCES "$PROFILE/Default/Secure Preferences"
safe_stat PROFILE_LOCAL_STATE "$PROFILE/Local State"
for lock in SingletonLock SingletonSocket SingletonCookie; do
  if sudo -n test -e "$PROFILE/$lock" || sudo -n test -L "$PROFILE/$lock"; then
    echo "PROFILE_LOCK_${lock}=PRESENT"
  else
    echo "PROFILE_LOCK_${lock}=ABSENT"
  fi
done
if sudo -n test -d "$PROFILE"; then
  sudo -n du -sh "$PROFILE" 2>/dev/null | awk '{print "PROFILE_SIZE=" $1}' || true
fi

echo '--- BROWSER LAB HELPER NAMES ONLY ---'
if sudo -n test -d "$LAB"; then
  sudo -n find "$LAB" -maxdepth 1 -mindepth 1 \
    ! -name 'chrome-profile' \
    -printf 'LAB_ENTRY=%f|type=%y|mode=%m|mtime=%TY-%Tm-%TdT%TH:%TM:%TS\n' 2>/dev/null \
    | sort | head -n 100 || true
else
  echo 'LAB_DIR=MISSING'
fi

echo '--- INTERACTIVE DISPLAY CAPABILITIES ---'
for bin in Xvfb x11vnc Xtigervnc vncserver websockify novnc_proxy openbox fluxbox xterm; do
  p="$(command -v "$bin" 2>/dev/null || true)"
  echo "BIN_${bin}=${p:-MISSING}"
done
for d in /usr/share/novnc /opt/novnc /usr/share/novnc/utils; do
  if test -d "$d"; then echo "NOVNC_DIR=$d"; fi
done

echo '--- RELEVANT SYSTEMD UNIT NAMES / STATES ---'
sudo -n systemctl list-unit-files --no-pager --no-legend 2>/dev/null \
  | awk 'BEGIN{IGNORECASE=1} $1 ~ /(vnc|xvfb|novnc|browser|chrome)/ {print "UNIT_FILE=" $1 "|" $2}' \
  | head -n 100 || true
sudo -n systemctl list-units --all --no-pager --no-legend 2>/dev/null \
  | awk 'BEGIN{IGNORECASE=1} $1 ~ /(vnc|xvfb|novnc|browser|chrome)/ {print "UNIT_STATE=" $1 "|load=" $2 "|active=" $3 "|sub=" $4}' \
  | head -n 100 || true

echo '--- RELEVANT RUNNING PROCESS NAMES ONLY ---'
ps -eo pid=,user=,comm= 2>/dev/null \
  | awk 'BEGIN{IGNORECASE=1} $3 ~ /(Xvfb|vnc|websockify|chrome|chromium)/ {print "PROCESS=pid=" $1 "|user=" $2 "|comm=" $3}' \
  | head -n 100 || true

echo '--- LISTENERS FOR INTERACTIVE TOOLS ONLY ---'
sudo -n ss -ltnpH 2>/dev/null \
  | awk 'BEGIN{IGNORECASE=1} /websockify|vnc|Xvnc|chrome/ {
      split($4,a,":"); port=a[length(a)]; bind=$4;
      if (bind ~ /^127\.0\.0\.1:/ || bind ~ /^\[::1\]:/) scope="LOOPBACK";
      else scope="NON_LOOPBACK";
      proc="UNKNOWN";
      if (match($0,/users:\(\(\"[^\"]+/)) {proc=substr($0,RSTART+9,RLENGTH-9)}
      print "LISTENER=scope=" scope "|port=" port "|process=" proc
    }' \
  | sort -u | head -n 50 || true

echo '--- PACKAGES RELEVANT TO INTERACTIVE DISPLAY ---'
dpkg-query -W -f='${binary:Package}|${Version}|${Status}\n' 2>/dev/null \
  | awk 'BEGIN{IGNORECASE=1} $1 ~ /(xvfb|x11vnc|tigervnc|novnc|websockify|openbox|fluxbox)/ && /install ok installed/ {print "PACKAGE=" $1 "|version=" $2}' \
  | head -n 100 || true

echo 'SECRETS_READ=NO'
echo 'COOKIE_VALUES_READ=NO'
echo 'LOGIN_DATA_READ=NO'
echo 'MUTATION=NO'
