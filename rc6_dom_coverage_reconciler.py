"""Pure reconciliation of authenticated DOM coverage against an independent universe.

Never calls PPI, browser or DB. A DOM row count can prove truncation when
expected identities are known; it can never prove completeness by itself.
"""
from __future__ import annotations
from dataclasses import dataclass

FAMILY_MAP={
    'FCI':'FCI','FCI EXTERIOR':'FCI-EXTERIOR','ACCIONES':'ACCIONES',
    'ACCIONES USA':'ACCIONES-USA','BONOS':'BONOS','CAUCIONES':'CAUCIONES',
    'CEDEARS':'CEDEARS','CEDEAR':'CEDEARS','ETF':'ETF','FUTUROS':'FUTUROS',
    'LETRAS':'LETRAS','LICITACIONES':'LICITACIONES','ON':'ON','OPCIONES':'OPCIONES',
    'ÍNDICES':'INDICES','INDICES':'INDICES','MONEDAS':'MONEDAS','TASAS':'TASAS'}

@dataclass(frozen=True)
class CoverageResult:
    family:str
    dom_unique:int
    expected_unique:int|None
    matched:int|None
    missing:int|None
    extra:int|None
    status:str
    expected_source:str|None
    complete_proven:bool

def canonical_family(value):
    key=' '.join(str(value or '').strip().upper().split())
    return FAMILY_MAP.get(key,key)

def reconcile(*,family,dom_ids,expected_ids=None,expected_source=None):
    fam=canonical_family(family)
    dom={str(x).strip().upper() for x in dom_ids if str(x).strip()}
    if expected_ids is None:
        return CoverageResult(fam,len(dom),None,None,None,None,
            'PARTIAL_UNKNOWN_EXPECTED',None,False)
    expected={str(x).strip().upper() for x in expected_ids if str(x).strip()}
    if not expected_source:
        raise ValueError('EXPECTED_SOURCE_REQUIRED')
    matched=len(dom & expected); missing=len(expected-dom); extra=len(dom-expected)
    if missing:
        status='PARTIAL_TRUNCATED' if len(dom)<=len(expected) else 'IDENTITY_MISMATCH'
        complete=False
    elif extra:
        status='DOM_EXTRA_IDENTITY_MISMATCH'; complete=False
    else:
        status='COMPLETE_PROVEN'; complete=True
    return CoverageResult(fam,len(dom),len(expected),matched,missing,extra,status,str(expected_source),complete)

def readiness(result):
    if result.complete_proven: return 'COVERAGE_GREEN'
    if result.status in {'PARTIAL_TRUNCATED','IDENTITY_MISMATCH','DOM_EXTRA_IDENTITY_MISMATCH'}: return 'COVERAGE_YELLOW'
    return 'COVERAGE_GRAY'
