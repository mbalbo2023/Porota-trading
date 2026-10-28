"""Bounded canonical adapter to the existing pure annual calculation."""
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode

from .components import e, fields, notice, sanitized
from .projection import Store, TZ, age
from .view_common import render_table


def render(p,destination,tab):
    heading='<h2>Análisis anual por instrumento</h2>'
    scope=notice('Histórico canónico, identidad monetaria y bases exactas; hasta 520 barras. '
                 'Sin autoridad de entrada ni llamadas al proveedor. La fecha de ingestión no acredita la última rueda.')
    if not p.filters.get('identity'):
        page=p.history()
        links=[]
        for row in page.rows:
            names=('symbol','instrument_type','market','currency','settlement')
            if not all(row.get(k) for k in names): continue
            params={'identity':json.dumps([row[k] for k in names],separators=(',',':')),
                    'price_basis':row.get('price_basis'),'adjustment_basis':row.get('adjustment_basis')}
            if not params['price_basis'] or not params['adjustment_basis']: continue
            links.append("<a class='button' href='/analitica/anual?"+e(urlencode(params))+"'>"+
                         e(' / '.join(str(row[k]) for k in names))+"</a>")
        selector_status = (notice('ANNUAL_NO_SELECTABLE_CANONICAL_SERIES · '
                                  + (page.reason or page.source)
                                  + ' · faltan identidad completa o bases de precio/ajuste verificadas.')
                           if not links else '')
        return heading+scope+selector_status+''.join(links)+render_table(p,destination,tab,page,'Seleccionar serie anual',
            fields('symbol|Ticker;currency|Moneda;family|Familia;series_last_session|Última rueda;freshness|Frescura|status'),
            fields('market|Mercado;settlement|Liquidación;price_basis|Base;adjustment_basis|Ajuste;last_ingest_at|Ingestión|time'),
            note='Los enlaces antiguos sin moneda requieren seleccionar una identidad completa; no se combinan series ambiguas.')
    try:
        key=json.loads(p.filters['identity'])
        if not isinstance(key,list) or len(key)!=5 or not all(isinstance(k,str) and k for k in key):
            raise ValueError
        bases=[p.filters.get('price_basis'),p.filters.get('adjustment_basis')]
        if not all(isinstance(k,str) and k for k in bases): raise ValueError
    except (ValueError,TypeError):
        return heading+scope+notice('ANNUAL_FULL_IDENTITY_AND_BASES_REQUIRED')
    path=Path(os.getenv('HIST_DB_PATH','data/market_history.db'))
    # Store uses mode=ro/query_only; it never creates/migrates the source.
    rows=[];reason='ANNUAL_SOURCE_UNAVAILABLE';deadline=time.monotonic()+.25
    with Store(path,now=p.now) as store:
        if store.connection is not None:
            store.connection.set_progress_handler(lambda:int(time.monotonic()>deadline),200)
        names=('symbol','instrument_type','market','currency','settlement','price_basis','adjustment_basis')
        required=set(names)|{'date','open','high','low','close','volume','source','adjusted','observed_at','version_known_at'}
        if required<=store.columns('history_canonical_v2'):
            rows=store.query('SELECT date,open,high,low,close,volume,source,adjusted,observed_at,version_known_at FROM history_canonical_v2 WHERE '+
                ' AND '.join(k+'=?' for k in names)+' AND date<=? ORDER BY date DESC LIMIT 520',
                (*key,*bases,p.now.astimezone(TZ).date().isoformat()))
            reason='ANNUAL_NO_CANONICAL_BARS'
        if store.errors or time.monotonic()>deadline:
            rows=[];reason='ANNUAL_READ_BUDGET_OR_SCHEMA'
        p.store.query_count+=store.query_count
    if not rows: return heading+scope+notice(reason)
    if any(age(row.get(k),p.now) is None for row in rows for k in ('observed_at','version_known_at')):
        return heading+scope+notice('ANNUAL_PUBLICATION_CLOCK_UNVERIFIED')
    # Reuse numerical/report logic only, never render_page() or its I/O.
    from rc6_annual_instrument_analysis import _render_report
    for row in rows: row['source']=sanitized(row.get('source'))
    report=_render_report((key[1],key[0],key[2],key[4]),list(reversed(rows)),p.now.astimezone(TZ).year)
    return heading+scope+notice('Moneda '+key[3]+' · '+bases[0]+' / '+bases[1]+' · Última rueda '+rows[0]['date'])+report
