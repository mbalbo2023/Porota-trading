#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${POROTA_ROOT:-/opt/porota-trading}"
CONTAINER="${POROTA_OBSERVER_CONTAINER:-porota_production_observer}"
DOCKER_BIN="${POROTA_DOCKER_BIN:-/usr/bin/docker}"
LIB="${POROTA_RC6_CE_LIB:-/usr/local/lib/porota-contract-evidence-rc6}"
PROFILE="${POROTA_CHROME_PROFILE:-/home/porotaadmin/porota-browser-lab/chrome-profile}"
CE_PYTHON="${POROTA_CE_PYTHON:-/opt/porota-contract-evidence-venv/bin/python}"
BASE="${POROTA_RC6_CE_BASE:-/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh}"
OUTDIR="$ROOT/data/contract_evidence_rc6_dom"

"$BASE"

safety="$($DOCKER_BIN exec -i "$CONTAINER" python - <<'PY'
import sqlite3,json
c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=10)
c.execute('pragma query_only=on')
r=c.execute('select mode,session_state,real_orders_sent from observer_state where id=1').fetchone()
c.close()
print(json.dumps({'mode':r[0] if r else None,'session':r[1] if r else None,'orders':r[2] if r else None}))
PY
)"
SAFETY="$safety" python3 - <<'PY'
import os,json,sys
d=json.loads(os.environ['SAFETY'])
if d.get('mode')!='PRODUCTION_PAPER' or int(d.get('orders') or 0)!=0:
    raise SystemExit(1)
PY

ROUTES="$($DOCKER_BIN exec -i "$CONTAINER" python - <<'PY'
import sqlite3
from datetime import datetime,timezone

db='/app/data/paper_v17/observer_v17.db'
c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=10)
c.execute('pragma query_only=on')
rows=dict(c.execute("""
select family,max(observed_at)
from contract_evidence_v2_current
where source_class='PPI_AUTHENTICATED_WEB'
group by family
""").fetchall())
c.close()

route_policy=[
    ('CAUCIONES','/Cotizaciones/Cauciones',300),
    ('LICITACIONES','/Cotizaciones/Licitaciones',300),
    ('FUTUROS','/Cotizaciones/Futuros',900),
    ('BONOS','/Cotizaciones/Bonos',900),
    ('OPCIONES','/Cotizaciones/Opciones',900),
]
now=datetime.now(timezone.utc)
due=[]
for family,route,max_age in route_policy:
    raw=rows.get(family)
    age=None
    if raw:
        try:
            dt=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
            if dt.tzinfo is None:
                dt=dt.replace(tzinfo=timezone.utc)
            age=(now-dt.astimezone(timezone.utc)).total_seconds()
        except Exception:
            age=None
    if age is None or age >= max_age:
        due.append(route)
print(','.join(due))
PY
)"

if [ -z "$ROUTES" ]; then
  printf 'STATUS=GREEN_DOM_NOT_DUE\nAUTH_BROWSER_STARTED=NO\nREAL_ORDERS_SENT=0\n'
  exit 0
fi

BROWSER_USER="$(stat -c '%U' "$PROFILE")"
STAGE="$(mktemp -d /tmp/porota-ce-dom.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
chown "$BROWSER_USER" "$STAGE"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE_CAPTURE="$STAGE/dom_${stamp}.json"
CAPTURE="$OUTDIR/dom_${stamp}.json"
install -d -m 0755 "$OUTDIR"

echo "DOM_DUE_ROUTES=$ROUTES"
runuser -u "$BROWSER_USER" -- env PYTHONPATH="$LIB:$ROOT" "$CE_PYTHON" "$LIB/rc6_contract_dom_collector.py" \
  --profile "$PROFILE" --output "$STAGE_CAPTURE" --routes "$ROUTES"

install -o root -g root -m 0644 "$STAGE_CAPTURE" "$CAPTURE"
REL="${CAPTURE#${ROOT}/data/}"
IMPORT_OUT="$($DOCKER_BIN exec -i "$CONTAINER" python - "/app/data/$REL" < "$LIB/rc6_contract_dom_importer.py" 2>&1)"
echo "$IMPORT_OUT"

POST="$($DOCKER_BIN exec -i "$CONTAINER" python - <<'PY'
import sqlite3,json
from datetime import datetime,timezone
c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=10)
c.row_factory=sqlite3.Row
c.execute('pragma query_only=on')
r=c.execute('select mode,session_state,real_orders_sent from observer_state where id=1').fetchone()
ce=c.execute("""
select count(*) n,max(observed_at) mx
from contract_evidence_v2_current
where source_class='PPI_AUTHENTICATED_WEB'
""").fetchone()
fam=[x[0] for x in c.execute("""
select distinct family
from contract_evidence_v2_current
where source_class='PPI_AUTHENTICATED_WEB'
order by family
""")]
c.close()
mx=ce['mx'] if ce else None
age=None
if mx:
    try:
        dt=datetime.fromisoformat(str(mx).replace('Z','+00:00'))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        age=(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()
    except Exception:
        pass
print(json.dumps({
    'mode':r['mode'] if r else None,
    'session':r['session_state'] if r else None,
    'orders':r['real_orders_sent'] if r else None,
    'web_rows':ce['n'] if ce else 0,
    'web_max_observed':mx,
    'web_age_seconds':age,
    'families':fam,
},default=str))
PY
)"
echo "DOM_POST=$POST"

IMPORT_OUT="$IMPORT_OUT" POST="$POST" python3 - <<'PY'
import os,json,sys
line=os.environ['IMPORT_OUT'].strip().splitlines()[-1]
imp=json.loads(line)
post=json.loads(os.environ['POST'])
if post.get('mode')!='PRODUCTION_PAPER' or int(post.get('orders') or 0)!=0:
    raise SystemExit(1)
if int(post.get('web_rows') or 0)<1:
    print('STATUS=RED_DOM_NO_PERSISTED_EVIDENCE')
    raise SystemExit(6)
state=imp.get('state')
if state=='GREEN' and int(imp.get('records') or 0)>=1:
    print('STATUS=GREEN_DOM_EXTENSION')
    raise SystemExit(0)
if state=='YELLOW_NO_TABLE_THIS_PASS':
    age=post.get('web_age_seconds')
    if age is not None and float(age) <= 1800:
        print('STATUS=YELLOW_DOM_NO_TABLE_THIS_PASS')
        raise SystemExit(0)
    print('STATUS=RED_DOM_STALE_AFTER_EMPTY_PASS')
    raise SystemExit(6)
print('STATUS=RED_DOM_IMPORT_UNEXPECTED')
raise SystemExit(6)
PY

echo REAL_ORDERS_SENT=0
