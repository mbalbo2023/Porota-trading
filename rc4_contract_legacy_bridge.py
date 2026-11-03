#!/usr/bin/env python3
"""One-way legacy Contract Evidence metadata bridge into v2.

It preserves legacy status/missing-field provenance only. It deliberately does
not reinterpret legacy payload fields as verified execution semantics.
"""
from __future__ import annotations
import json
from cp_contract_evidence_v2_hf6 import record_snapshot

def bridge(store, *, limit=None):
    with store.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'contract_evidence' not in tables: return {'rows':0,'recorded':0,'changed':0}
        sql="SELECT instrument_type,ticker,market,status,owner,source,checked_at,missing_fields_json,detail FROM contract_evidence ORDER BY instrument_type,ticker,market"
        rows=[dict(r) for r in c.execute(sql)]
    if limit is not None: rows=rows[:int(limit)]
    recorded=changed=0
    for row in rows:
        try: missing=json.loads(row.get('missing_fields_json') or '[]')
        except Exception: missing=[]
        evidence={'legacy_status':row.get('status'),'legacy_owner':row.get('owner'),'legacy_source':row.get('source'),
                  'legacy_missing_fields':missing,'legacy_detail':row.get('detail') or ''}
        result=record_snapshot(store,family=row.get('instrument_type'),ticker=row.get('ticker'),market=row.get('market'),settlement='UNKNOWN',
              source_class='POROTA_LEGACY_EVIDENCE',source_ref='legacy:contract_evidence',observed_at=row.get('checked_at'),evidence=evidence)
        recorded+=1; changed+=int(bool(result.get('changed')))
    return {'rows':len(rows),'recorded':recorded,'changed':changed,'auto_activation_allowed':False}
