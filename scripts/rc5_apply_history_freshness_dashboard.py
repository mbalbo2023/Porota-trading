from pathlib import Path

p=Path('bg_paper_dashboard.py')
s=p.read_text(encoding='utf-8')


def replace_once(old,new,label):
    global s
    n=s.count(old)
    if n != 1:
        raise SystemExit(f'{label}: esperado 1 match, encontrado {n}')
    s=s.replace(old,new,1)


replace_once(
    'from dd_history_metrics_hf6 import observer_history_metrics, v2_store_metrics, effective_store_metrics\n',
    'from dd_history_metrics_hf6 import observer_history_metrics, v2_store_metrics, effective_store_metrics\nfrom ek_history_freshness_metrics_rc5 import freshness_qualified_metrics\n',
    'import freshness metrics')

replace_once(
    '''    try:\n        with closing(_conn()) as c:\n            store_v2=effective_store_metrics(c)\n    except Exception:\n        store_v2=v2_store_metrics()\n    history_target=int(dynamic.get('target_total') or 0)\n''',
    '''    try:\n        with closing(_conn()) as c:\n            store_v2=effective_store_metrics(c)\n    except Exception:\n        store_v2=v2_store_metrics()\n    try:\n        with closing(_conn()) as c:\n            fresh_v5=freshness_qualified_metrics(c)\n    except Exception as exc:\n        fresh_v5={"available":False,"reason":type(exc).__name__,"target_total":0,\n                  "fresh_total":0,"fresh_ge30":0,"fresh_ge90":0,"fresh_ge180":0,\n                  "stale_ge90_count":0,"store_latest_date":None}\n    history_target=int(dynamic.get('target_total') or 0)\n''',
    'calcular freshness')

old='''        _card("Cobertura histórica",target_label,coverage_detail,\n              "green" if coverage_complete else "gray"),\n        _card("Historia efectiva",f"{store_v2.get('canonical_rows',0)} filas",\n              f"capa {store_v2.get('layer','V2')} · {store_v2.get('reason','OK')}",\n              "green" if store_v2.get('available') else "yellow"),\n'''
new='''        _card("Cobertura histórica ANY",target_label,coverage_detail+" · profundidad/freshness se informan por separado",\n              "green" if coverage_complete else "gray"),\n        _card("Historia fresca ≥30",\n              f"{fresh_v5.get('fresh_ge30',0)}/{fresh_v5.get('target_total') or history_target or '—'}",\n              f"FULL_OHLC · última fecha store {_e(fresh_v5.get('store_latest_date'))} · no implica READY PAPER",\n              "green" if fresh_v5.get('available') else "yellow"),\n        _card("Historia fresca ≥90",\n              f"{fresh_v5.get('fresh_ge90',0)}/{fresh_v5.get('target_total') or history_target or '—'}",\n              f"FULL_OHLC + freshness ≤2 ruedas · stale ≥90: {fresh_v5.get('stale_ge90_count',0)}",\n              "green" if fresh_v5.get('available') else "yellow"),\n        _card("Historia fresca ≥180",\n              f"{fresh_v5.get('fresh_ge180',0)}/{fresh_v5.get('target_total') or history_target or '—'}",\n              "FULL_OHLC profundo y fresco; no es precio de ejecución ni autorización PAPER",\n              "green" if fresh_v5.get('available') else "yellow"),\n        _card("Historia efectiva",f"{store_v2.get('canonical_rows',0)} filas",\n              f"capa {store_v2.get('layer','V2')} · {store_v2.get('reason','OK')} · ANY no equivale a fresh",\n              "green" if store_v2.get('available') else "yellow"),\n'''
replace_once(old,new,'cards freshness')

replace_once(
    '''    body=f"<h1>Históricos y universo</h1><div class='paper-grid'>{cards}</div><div class='paper-notice'><b>Fecha del dato, fecha de ingesta y readiness PAPER son conceptos distintos.</b> Una familia HOLD puede acumular históricos si su identidad financiera está verificada. El denominador ya no es 243 fijo: surge del universo histórico disponible por familia. Para saber cuándo vuelve a ejecutarse cada trabajo, usar Sistema → Scheduler.</div>''',
    '''    freshness_notice=(\n        "<div class='paper-notice'><b>Profundidad y freshness son métricas distintas.</b> "\n        f"Fresh FULL_OHLC: ≥30 {fresh_v5.get('fresh_ge30',0)}, ≥90 {fresh_v5.get('fresh_ge90',0)}, "\n        f"≥180 {fresh_v5.get('fresh_ge180',0)} de {fresh_v5.get('target_total') or history_target or '—'} identidades. "\n        f"Hay {fresh_v5.get('stale_ge90_count',0)} identidades con al menos 90 barras pero historia stale; no cuentan como fresh ≥90. "\n        "CLOSE_ONLY se informa por separado y nunca habilita ATR, VWAP, precio de ejecución ni READY PAPER.</div>"\n    )\n    body=f"<h1>Históricos y universo</h1><div class='paper-grid'>{cards}</div>{freshness_notice}<div class='paper-notice'><b>Fecha del dato, fecha de ingesta y readiness PAPER son conceptos distintos.</b> Una familia HOLD puede acumular históricos si su identidad financiera está verificada. El denominador ya no es 243 fijo: surge del universo histórico disponible por familia. Para saber cuándo vuelve a ejecutarse cada trabajo, usar Sistema → Scheduler.</div>''',
    'notice freshness')

p.write_text(s,encoding='utf-8')

Path('tests/test_history_freshness_metrics_rc5.py').write_text(r'''from datetime import date
from pathlib import Path

import ek_history_freshness_metrics_rc5 as metrics


def test_freshness_metric_contract():
    metrics.assert_freshness_metric_contract()
    assert metrics._depth(30) == "LT90"
    assert metrics._depth(90) == "LT180"
    assert metrics._freshness(date(2025,12,9),date(2026,9,4))[0] == "STALE_UNVERIFIED_CALENDAR"


def test_dashboard_separates_any_depth_and_freshness():
    s=Path('bg_paper_dashboard.py').read_text(encoding='utf-8')
    assert 'freshness_qualified_metrics' in s
    assert 'Cobertura histórica ANY' in s
    assert 'Historia fresca ≥30' in s
    assert 'Historia fresca ≥90' in s
    assert 'Historia fresca ≥180' in s
    assert 'Profundidad y freshness son métricas distintas.' in s
    assert 'CLOSE_ONLY se informa por separado' in s
    assert 'no cuentan como fresh ≥90' in s
''',encoding='utf-8')
