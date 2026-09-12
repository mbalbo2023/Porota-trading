"""Fail-closed crosswalk between API-confirmed identities and Contract Evidence.

Only exact API identity evidence can participate in per-instrument binding. Family
wildcards remain contextual and are never silently promoted to an instrument.
No network access, no orders, no PAPER promotion.
"""
from __future__ import annotations

ALIASES={
    'ACCIONES_USA':'ACCIONES-USA','FCI_EXTERIOR':'FCI-EXTERIOR','FCI_LOCAL':'FCI',
    'OBLIGACIONES':'ON','ETFS':'ETF',
}
PLACEHOLDERS={'','*','UNKNOWN','NONE','N/A','NULL'}


def canon(value):
    raw=str(value or '').strip().upper()
    return ALIASES.get(raw,raw)


def _concrete(value):
    return str(value or '').strip().upper() not in PLACEHOLDERS


def partition(identity: dict, records) -> dict:
    family=canon(identity.get('family'))
    ticker=str(identity.get('ticker') or '').strip().upper()
    market=str(identity.get('market') or '').strip().upper()
    if not identity.get('api_discovered') or not _concrete(ticker) or not _concrete(market):
        raise ValueError('CROSSWALK_REQUIRES_API_DISCOVERED_IDENTITY')

    exact=[]
    family_context=[]
    rejected=[]
    for record in records or []:
        if not isinstance(record,dict):
            continue
        rfam=canon(record.get('family'))
        rticker=str(record.get('ticker') or '*').strip().upper()
        rmarket=str(record.get('market') or 'UNKNOWN').strip().upper()
        if rfam != family:
            rejected.append(record); continue
        if rticker in PLACEHOLDERS:
            family_context.append(record); continue
        if rticker != ticker:
            rejected.append(record); continue
        # If provider gave a concrete market, it must agree with the API identity.
        if _concrete(rmarket) and rmarket != market:
            rejected.append(record); continue
        exact.append(record)
    return {
        'identity':identity,
        'exact':exact,
        'family_context':family_context,
        'rejected':rejected,
        'binding_records':exact,
        'wildcard_used_for_binding':False,
        'paper_candidate':False,
    }


def assert_no_execution_capability():
    assert not any(name in globals() for name in ('send_order','place_order','cancel_order'))
