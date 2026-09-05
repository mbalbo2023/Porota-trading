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
    'from dh_dashboard_compact_lists_hf6 import COMPACT_CSS\n',
    'from dh_dashboard_compact_lists_hf6 import COMPACT_CSS, pager_html\nimport eb_dashboard_live_policy_hf2 as live_policy\n',
    'imports live policy')

replace_once(
    'box-shadow:0 3px 14px #14213d0c;margin:12px 0;overflow:auto}.paper-card b.metric{font-size:1.22rem}\n',
    'box-shadow:0 3px 14px #14213d0c;margin:12px 0;overflow:hidden;min-width:0}.paper-card b.metric{font-size:1.22rem}\n',
    'paper-card sin scroll horizontal')

replace_once(
    '.paper-table{width:100%;border-collapse:collapse;font-size:.86rem}.paper-table th{background:#e7edf4}\n',
    '.paper-table{width:100%;max-width:100%;table-layout:fixed;border-collapse:collapse;font-size:.86rem}.paper-table th{background:#e7edf4}\n',
    'table layout fijo')

replace_once(
    '.paper-table th,.paper-table td{padding:9px;border-bottom:1px solid #dce3ed;text-align:left;vertical-align:top}\n',
    '.paper-table th,.paper-table td{padding:9px;border-bottom:1px solid #dce3ed;text-align:left;vertical-align:top;overflow-wrap:anywhere;word-break:break-word}\n',
    'celdas wrap')

replace_once('def live_page():\n', 'def live_page(*, offset=0, limit=20):\n', 'firma live_page')

old_closed='''    closed_today=[]
    for row in data.get('closed',[]):
        try:
            closed_at=aware_datetime(row.get('closed_at')).astimezone(TZ)
        except Exception:
            continue
        if closed_at.date()==now.date():
            closed_today.append(row)
    closed=closed_today[:20]
'''
new_closed='''    closed_today=live_policy.closed_for_live(data.get('closed',[]),now=now)
    closed_page=live_policy.page_for_tablet(closed_today,offset=0,limit=20)
    closed=list(closed_page.items)
'''
replace_once(old_closed,new_closed,'cierres day-only')

old_gates='''    gates=[]
    if _table('trade_gate_evaluations'):
        gates=_rows("""SELECT * FROM trade_gate_evaluations
          ORDER BY evaluated_at DESC,id DESC LIMIT 100""")
'''
new_gates='''    gates=[]
    if _table('trade_gate_evaluations'):
        gates=live_policy.rows_for_today(_rows("""SELECT * FROM trade_gate_evaluations
          ORDER BY evaluated_at DESC,id DESC LIMIT 500"""),'evaluated_at',now=now)
'''
replace_once(old_gates,new_gates,'gates day-only')

old_decisions='''    decision_rows=[]
    live_decisions=(_rows(
        "SELECT decided_at,symbol,action,score,reason FROM paper_decisions "
        "ORDER BY decided_at DESC LIMIT 50"
    ) if _table('paper_decisions') else [])
    for row in live_decisions:
'''
new_decisions='''    decision_rows=[]
    all_live_decisions=live_policy.decisions_for_live((_rows(
        "SELECT decided_at,symbol,action,score,reason FROM paper_decisions "
        "ORDER BY decided_at DESC LIMIT 500"
    ) if _table('paper_decisions') else []),now=now)
    decision_page=live_policy.page_for_tablet(all_live_decisions,offset=offset,limit=limit)
    live_decisions=list(decision_page.items)
    for row in live_decisions:
'''
replace_once(old_decisions,new_decisions,'decisiones day-only paginadas')

old_fallback='''    if not decision_rows:
        for row in gates[:50]:
            result=str(row.get('final_result') or '')
            label='ACEPTADA' if result=='OPENED_SIMULATED' else 'RECHAZADA/BLOQUEADA'
            decision_rows.append(
                f"<tr><td>{_local_time(row.get('evaluated_at'))}</td><td><b>{_e(row.get('symbol'))}</b></td>"
                f"<td>{_status(label)}</td><td>{_status(row.get('technical_gate'))}</td>"
                f"<td>{_status(row.get('patrimonial_gate'))}</td><td>{_e(row.get('reason'))}</td></tr>"
            )
'''
new_fallback='''    if not decision_rows:
        decision_page=live_policy.page_for_tablet(gates,offset=offset,limit=limit)
        for row in decision_page.items:
            result=str(row.get('final_result') or '')
            label='ACEPTADA' if result=='OPENED_SIMULATED' else 'RECHAZADA/BLOQUEADA'
            decision_rows.append(
                f"<tr><td>{_local_time(row.get('evaluated_at'))}</td><td><b>{_e(row.get('symbol'))}</b></td>"
                f"<td>{_status(label)}</td><td>{_status(row.get('technical_gate'))}</td>"
                f"<td>{_status(row.get('patrimonial_gate'))}</td><td>{_e(row.get('reason'))}</td></tr>"
            )
    decision_pager=pager_html('/en-vivo',decision_page)
'''
replace_once(old_fallback,new_fallback,'fallback gates paginado')

replace_once(
    "      _card('Cerradas recientes',len(closed),'Incluyen resultado, causa y lección','green' if closed else 'gray'),\n",
    "      _card('Cerradas hoy',closed_page.total,f'Mostrando {len(closed)} de {closed_page.total}; resultado, causa y lección','green' if closed else 'gray'),\n",
    'tarjeta cierres')

old_decision_table='''          "<table class='paper-table'>"
          "<tr><th>Hora</th><th>Instrumento</th><th>Decisión</th><th>Técnico</th><th>Patrimonial</th><th>Explicación</th></tr>"+
          (''.join(decision_rows) or "<tr><td colspan='6'>Sin decisiones.</td></tr>")+"</table></div>"
'''
new_decision_table='''          "<table class='paper-table'>"
          "<tr><th>Hora</th><th>Instrumento</th><th>Decisión</th><th>Técnico</th><th>Patrimonial</th><th>Explicación</th></tr>"+
          (''.join(decision_rows) or "<tr><td colspan='6'>Sin decisiones de hoy.</td></tr>")+"</table>"+
          decision_pager+"<a class='paper-action' href='/en-vivo'>Actualizar ahora</a></div>"
'''
replace_once(old_decision_table,new_decision_table,'pager y actualizar')

replace_once(
    '    return _document("En vivo",body,refresh=15)\n',
    '    return _document("En vivo",body,refresh=0)\n',
    'refresh manual')

old_route='''    @app.get("/en-vivo",response_class=HTMLResponse)
    def en_vivo(request:Request,token:str=Query(default=""),authorization:str|None=Header(default=None)): auth(request,token,authorization); return HTMLResponse(live_page())
'''
new_route='''    @app.get("/en-vivo",response_class=HTMLResponse)
    def en_vivo(request:Request,offset:int=Query(default=0,ge=0),limit:int=Query(default=20,ge=1,le=50),token:str=Query(default=""),authorization:str|None=Header(default=None)):
        auth(request,token,authorization)
        return HTMLResponse(live_page(offset=offset,limit=limit))
'''
replace_once(old_route,new_route,'ruta paginada')

p.write_text(s,encoding='utf-8')

Path('tests/test_dashboard_live_integration_rc5.py').write_text('''from pathlib import Path


def test_live_page_is_day_only_paginated_and_manual_refresh():
    s=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "live_policy.decisions_for_live" in s
    assert "live_policy.closed_for_live" in s
    assert "live_policy.page_for_tablet" in s
    assert "decision_pager=pager_html('/en-vivo',decision_page)" in s
    assert "return _document(\\"En vivo\\",body,refresh=0)" in s
    assert "return _document(\\"En vivo\\",body,refresh=15)" not in s
    assert "overflow:auto}.paper-card" not in s
    assert "table-layout:fixed" in s


def test_live_route_exposes_bounded_pagination():
    s=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "offset:int=Query(default=0,ge=0)" in s
    assert "limit:int=Query(default=20,ge=1,le=50)" in s
    assert "live_page(offset=offset,limit=limit)" in s
''',encoding='utf-8')
