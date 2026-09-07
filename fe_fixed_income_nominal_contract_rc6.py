"""RC6 fixed-income nominal-unit contract (offline/shadow only).

It never infers nominal conventions from a ticker.  A bond/ON/letra becomes
mathematically measurable only with explicit evidence for quote basis,
quantity step and minimum nominal quantity.  Missing fields remain
NEEDS_NOMINAL_UNITS and cannot authorize PAPER execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

FAMILIES=frozenset({'BONOS','LETRAS','OBLIGACIONES'})

class NominalContractError(ValueError): pass

def _d(v,name,*,positive=True):
    try: x=Decimal(str(v))
    except (InvalidOperation,TypeError,ValueError) as exc: raise NominalContractError(name+'_INVALID') from exc
    if not x.is_finite() or (positive and x<=0): raise NominalContractError(name+'_OUT_OF_RANGE')
    return x

@dataclass(frozen=True)
class NominalEvidence:
    symbol:str
    family:str
    market:str
    currency:str
    settlement:str
    quote_basis_nominal:object|None
    quantity_step_nominal:object|None
    minimum_nominal:object|None
    metadata_source:str
    observed_at:str
    source_reference:str=''

@dataclass(frozen=True)
class NominalContract:
    symbol:str; family:str; market:str; currency:str; settlement:str
    quote_basis_nominal:Decimal; quantity_step_nominal:Decimal; minimum_nominal:Decimal
    metadata_source:str; observed_at:str; source_reference:str
    status:str='VERIFIED_NOMINAL_CONTRACT'
    paper_execution_authorized:bool=False

    def normalize_quantity(self,nominal)->Decimal:
        q=_d(nominal,'NOMINAL')
        if q<self.minimum_nominal: raise NominalContractError('BELOW_MINIMUM_NOMINAL')
        if q % self.quantity_step_nominal: raise NominalContractError('NOMINAL_STEP_MISMATCH')
        return q

    def cash_notional(self,price,nominal)->Decimal:
        p=_d(price,'PRICE'); q=self.normalize_quantity(nominal)
        return p*q/self.quote_basis_nominal


def evaluate(e:NominalEvidence)->dict:
    family=str(e.family or '').strip().upper()
    identity=[str(x or '').strip().upper() for x in (e.symbol,e.market,e.currency,e.settlement)]
    if family not in FAMILIES or any(x in {'','UNKNOWN'} for x in identity):
        return {'status':'IDENTITY_UNVERIFIED','contract':None,'paper_execution_authorized':False}
    if not str(e.metadata_source or '').strip() or not str(e.observed_at or '').strip():
        return {'status':'PROVENANCE_INCOMPLETE','contract':None,'paper_execution_authorized':False}
    missing=[]
    for name,value in [('quote_basis_nominal',e.quote_basis_nominal),('quantity_step_nominal',e.quantity_step_nominal),('minimum_nominal',e.minimum_nominal)]:
        if value is None: missing.append(name)
    if missing:
        return {'status':'NEEDS_NOMINAL_UNITS','missing':missing,'contract':None,'paper_execution_authorized':False}
    try:
        basis=_d(e.quote_basis_nominal,'QUOTE_BASIS_NOMINAL')
        step=_d(e.quantity_step_nominal,'QUANTITY_STEP_NOMINAL')
        minimum=_d(e.minimum_nominal,'MINIMUM_NOMINAL')
    except NominalContractError as exc:
        return {'status':'INVALID_NOMINAL_CONTRACT','reason':str(exc),'contract':None,'paper_execution_authorized':False}
    if minimum % step:
        return {'status':'INVALID_NOMINAL_CONTRACT','reason':'MINIMUM_NOT_MULTIPLE_OF_STEP','contract':None,'paper_execution_authorized':False}
    c=NominalContract(identity[0],family,identity[1],identity[2],identity[3],basis,step,minimum,str(e.metadata_source),str(e.observed_at),str(e.source_reference or ''))
    return {'status':c.status,'contract':c,'paper_execution_authorized':False}


def evidence_from_iol_asset(*,symbol,family,market,currency,settlement,units_per_lot,observed_at,source_reference=''):
    """IOL lot evidence alone is intentionally insufficient for execution.

    It may populate quantity_step_nominal for research, but quote basis and
    minimum nominal remain unproven until an authoritative contract source
    supplies them explicitly.
    """
    return NominalEvidence(symbol,family,market,currency,settlement,None,units_per_lot,None,'IOL_ASSET_INFO',observed_at,source_reference)


def assert_shadow_only():
    assert NominalContract.__dataclass_fields__['paper_execution_authorized'].default is False
