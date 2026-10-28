from __future__ import annotations
import hashlib,re
from bn_telegram_bus import enqueue
CRITICAL_COMPONENTS={'PPI_PRODUCTION_AUTH','PPI_PRODUCTION_MARKETDATA','PPI_PRODUCTION_HISTORY'}
MATERIAL_BAD={'ROJO','ERROR','COOLDOWN','RATE_LIMIT'}; GOOD={'VERDE','OK','READY'}
def _clean(text):
    s=str(text or ''); s=re.sub(r'(?i)(token|secret|password|authorization|cookie)\s*[:=]\s*\S+',r'\1=[OFUSCADO]',s); s=re.sub(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}','[JWT_OFUSCADO]',s); return s[:600]
def _class(s):
    s=str(s or '').upper(); return 'BAD' if s in MATERIAL_BAD else ('GOOD' if s in GOOD else 'OTHER')
def enqueue_transition(c,*,component,previous_state,state,detail,checked_at,last_success_at=None,source='',real_orders_sent=0):
    component=str(component or '').upper()
    if component not in CRITICAL_COMPONENTS:return False
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
