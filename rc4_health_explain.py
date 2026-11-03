from datetime import datetime,timezone
def classify(state,checked_at=None,expected_seconds=None,detail=''):
    key=str(state or 'SIN_EVIDENCIA').upper(); age=None
    if checked_at:
        try:
            d=datetime.fromisoformat(str(checked_at).replace('Z','+00:00')); d=d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d; age=max(0,(datetime.now(timezone.utc)-d.astimezone(timezone.utc)).total_seconds())
        except Exception:pass
    if key in {'NO_APLICA','NOT_APPLICABLE','DISABLED'}:cause='NO_APLICA'
    elif key in {'ERROR','FAILED','ROJO'}:cause='ERROR'
    elif checked_at is None:cause='NUNCA_EJECUTADO'
    elif expected_seconds and age is not None and age>expected_seconds*1.5:cause='RETRASADO'
    elif key in {'PARTIAL','DEGRADED','AMARILLO','PENDIENTE'}:cause='DEGRADADO'
    else:cause='OK'
    return {'cause':cause,'age_seconds':age,'expected_seconds':expected_seconds,'detail':detail}
