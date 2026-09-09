#!/usr/bin/env python3
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import rc4_trusted_browser_contract_collector as legacy

TRADING='https://trading.portfoliopersonal.com'

def collect(ctx, page, output_path: str) -> None:
    out={
        'schema':'POROTA_RC6_PPI_TRUSTED_SAME_CONTEXT_1',
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'auth_status':'AUTHENTICATED_TRUSTED_DEVICE',
        'jobs':['CONTRACT_EVIDENCE_FULL_BROWSER'],
        'routes':[], 'endpoints':{}, 'blocked_nonread':[],
        'continue_clicked':False,'amount_filled':False,'price_filled':False,
        'real_orders_sent':0,
    }
    def on_response(response):
        try:
            if response.request.method.upper()!='GET': return
            if not any(x in response.url for x in legacy.TARGETS): return
            if response.status>=400: return
            payload=response.json()
            sanitized=legacy.sanitize_endpoint(response.url,payload)
            if sanitized is not None:
                out['endpoints'][legacy.clean_url(response.url)]={'status':response.status,**sanitized}
        except Exception:
            pass
    page.on('response',on_response)
    seen=set()
    for route in legacy.ROUTES['CONTRACT_EVIDENCE_FULL_BROWSER']:
        if route in seen: continue
        seen.add(route)
        row={'job':'CONTRACT_EVIDENCE_FULL_BROWSER','requested':route,'reached':False}
        try:
            page.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000)
            page.wait_for_timeout(900)
            pu=urlsplit(page.url)
            row['url']=legacy.clean_url(page.url)
            row['title']=page.title()[:180]
            row['reached']=pu.netloc=='trading.portfoliopersonal.com' and 'login' not in pu.path.lower() and 'logout' not in pu.path.lower()
            row['controls']=legacy.safe_controls(page)
        except Exception as exc:
            row['error']=type(exc).__name__
        out['routes'].append(row)
    target=Path(output_path)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    os.chmod(target,0o600)
