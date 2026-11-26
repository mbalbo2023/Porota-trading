from __future__ import annotations

import hashlib
import re
from zoneinfo import ZoneInfo

from bn_telegram_bus import enqueue
from bs_instrument_contracts import aware_datetime

MARKETDATA_COMPONENT = 'PPI_PRODUCTION_MARKETDATA'
_MARKETDATA_KEY = f'operational-health:{MARKETDATA_COMPONENT}'
_ART = ZoneInfo('America/Argentina/Buenos_Aires')
CRITICAL_COMPONENTS={'PPI_PRODUCTION_AUTH',MARKETDATA_COMPONENT,'PPI_PRODUCTION_HISTORY'}
MATERIAL_BAD={'ROJO','ERROR','COOLDOWN','RATE_LIMIT'}; GOOD={'VERDE','OK','READY'}
def _clean(text):
    s=str(text or ''); s=re.sub(r'(?i)(token|secret|password|authorization|cookie)\s*[:=]\s*\S+',r'\1=[OFUSCADO]',s); s=re.sub(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}','[JWT_OFUSCADO]',s); return s[:600]
def _class(s):
    s=str(s or '').upper(); return 'BAD' if s in MATERIAL_BAD else ('GOOD' if s in GOOD else 'OTHER')


def init_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS paper_marketdata_alert (
        id INTEGER PRIMARY KEY CHECK(id=1), incident_key TEXT,
        first_healthy_at TEXT, checked_at TEXT NOT NULL
    )''')


def _marketdata_transition(c, *, state, detail, checked_at, last_success_at, source, real_orders_sent):
    checked = aware_datetime(checked_at)
    current = c.execute('SELECT incident_key,first_healthy_at,checked_at FROM paper_marketdata_alert WHERE id=1').fetchone()
    if current and checked <= aware_datetime(current[2]):
        return False
    if current:
        incident_key, first_healthy_at, _ = current
    else:
        # Continue an open notice from the old implementation on the first cycle after deploy.
        previous = c.execute('''SELECT event_key,kind,state FROM paper_notification_outbox
            WHERE event_key GLOB ? ORDER BY id DESC LIMIT 1''', (f'{_MARKETDATA_KEY}:*',)).fetchone()
        incident_key = previous[0] if (previous and previous[1] == 'OPERATIONAL_HEALTH_RED'
                                       and previous[2] in {'PENDING', 'SENDING', 'SENT'}) else None
        first_healthy_at = None

    category = _class(state)
    outgoing = None
    if category == 'BAD':
        first_healthy_at = None
        if incident_key is None:
            incident = hashlib.sha256(checked.isoformat().encode()).hexdigest()[:16]
            incident_key = f'{_MARKETDATA_KEY}:BAD:{incident}'
            body = (
                f'POROTA · DATOS PPI DEGRADADOS\n{_clean(detail)}\n'
                f'Último dato válido {_clean(last_success_at) or "sin registro"}\n'
                f'Fuente {_clean(source)}\nÓrdenes reales enviadas {int(real_orders_sent or 0)}'
            )
            outgoing = (incident_key, 'OPERATIONAL_HEALTH_RED', body, 5)
    elif category == 'GOOD' and incident_key is not None:
        if (first_healthy_at is None or
                aware_datetime(first_healthy_at).astimezone(_ART).date() != checked.astimezone(_ART).date()):
            first_healthy_at = checked.isoformat()
        else:
            incident = incident_key.rsplit(':', 1)[1]
            body = (
                'POROTA · DATOS PPI ESTABLES EN LA MUESTRA\n'
                'Dos ciclos consecutivos con datos válidos.\n'
                f'Último ciclo {_clean(detail)}\nFuente {_clean(source)}\n'
                f'Órdenes reales enviadas {int(real_orders_sent or 0)}'
            )
            outgoing = (f'{_MARKETDATA_KEY}:GOOD:{incident}', 'OPERATIONAL_HEALTH_RECOVERY', body, 8)
            incident_key = first_healthy_at = None
    else:
        first_healthy_at = None

    c.execute('SAVEPOINT marketdata_notice')
    try:
        c.execute('''INSERT INTO paper_marketdata_alert VALUES(1,?,?,?)
            ON CONFLICT(id) DO UPDATE SET incident_key=excluded.incident_key,
            first_healthy_at=excluded.first_healthy_at,checked_at=excluded.checked_at''',
            (incident_key, first_healthy_at, checked.isoformat()))
        if outgoing:
            key, kind, body, priority = outgoing
            return enqueue(c, key, kind, body, checked_at, priority=priority)
        return False
    except Exception:
        c.execute('ROLLBACK TO marketdata_notice')
        raise
    finally:
        c.execute('RELEASE marketdata_notice')


def enqueue_transition(c,*,component,previous_state,state,detail,checked_at,last_success_at=None,source='',real_orders_sent=0):
    component=str(component or '').upper()
    if component not in CRITICAL_COMPONENTS:return False
    if component == MARKETDATA_COMPONENT:
        return _marketdata_transition(c, state=state, detail=detail, checked_at=checked_at,
                                      last_success_at=last_success_at, source=source,
                                      real_orders_sent=real_orders_sent)
    before,after=_class(previous_state),_class(state)
    if after=='OTHER' or before==after:return False
    if after=='GOOD' and before!='BAD':return False
    if after=='BAD' and before=='BAD':return False
    incident=hashlib.sha256(f'{component}|{after}|{last_success_at or "NEVER"}'.encode()).hexdigest()[:16]
    key=f'operational-health:{component}:{after}:{incident}'; state=str(state or '').upper()
    if after=='BAD':
        body=f'POROTA · ALERTA OPERATIVA\n{component}: {state}\nCausa: {_clean(detail)}\nÚltimo OK: {last_success_at or "sin registro"}\nFuente: {_clean(source)}\nÓrdenes reales enviadas: {int(real_orders_sent or 0)}\nAcción: mantener fail-closed y revisar la fuente sin forzar reintentos.'; kind='OPERATIONAL_HEALTH_RED'; priority=5
    else:
        body=f'POROTA · RECUPERACIÓN OPERATIVA\n{component}: {state}\nDetalle: {_clean(detail)}\nFuente: {_clean(source)}\nÓrdenes reales enviadas: {int(real_orders_sent or 0)}\nLa recuperación no habilita producción real automáticamente.'; kind='OPERATIONAL_HEALTH_RECOVERY'; priority=8
    return enqueue(c,key,kind,body,checked_at,priority=priority)
