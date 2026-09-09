import ast, inspect
from decimal import Decimal
import fe_fixed_income_nominal_contract_rc6 as m

def ev(**kw):
    d=dict(symbol='GD30',family='BONOS',market='BYMA',currency='ARS',settlement='A-24HS',quote_basis_nominal=100,quantity_step_nominal=1,minimum_nominal=100,metadata_source='OFFICIAL_CONTRACT',observed_at='2026-09-07T17:00:00+00:00',source_reference='ref')
    d.update(kw); return m.NominalEvidence(**d)
def test_complete_explicit_contract_computes_dimensional_notional():
    r=m.evaluate(ev()); assert r['status']=='VERIFIED_NOMINAL_CONTRACT'; c=r['contract']; assert c.cash_notional(80000,100)==Decimal('80000'); assert r['paper_execution_authorized'] is False
def test_missing_minimum_stays_needs_nominal_units():
    r=m.evaluate(ev(minimum_nominal=None)); assert r['status']=='NEEDS_NOMINAL_UNITS'; assert 'minimum_nominal' in r['missing']; assert r['contract'] is None
def test_iol_units_per_lot_alone_never_claims_full_contract():
    e=m.evidence_from_iol_asset(symbol='GD30',family='BONOS',market='BYMA',currency='ARS',settlement='A-24HS',units_per_lot=100,observed_at='2026-09-07T17:00:00+00:00'); r=m.evaluate(e); assert r['status']=='NEEDS_NOMINAL_UNITS'; assert set(r['missing'])=={'quote_basis_nominal','minimum_nominal'}
def test_minimum_must_be_multiple_of_step(): assert m.evaluate(ev(quantity_step_nominal=100,minimum_nominal=150))['status']=='INVALID_NOMINAL_CONTRACT'
def test_below_minimum_and_step_are_blocked():
    c=m.evaluate(ev(quantity_step_nominal=100,minimum_nominal=1000))['contract']
    try: c.normalize_quantity(900); assert False
    except m.NominalContractError as e: assert str(e)=='BELOW_MINIMUM_NOMINAL'
    try: c.normalize_quantity(1050); assert False
    except m.NominalContractError as e: assert str(e)=='NOMINAL_STEP_MISMATCH'
def test_unknown_identity_is_fail_closed(): assert m.evaluate(ev(market='UNKNOWN'))['status']=='IDENTITY_UNVERIFIED'
def test_offline_no_db_network_orders():
    m.assert_shadow_only(); tree=ast.parse(inspect.getsource(m)); imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    assert not any(x in {'sqlite3','requests','httpx'} or 'order' in x.lower() for x in imports)
