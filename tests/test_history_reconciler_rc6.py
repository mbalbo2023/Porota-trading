from datetime import datetime, timezone
import pytest

from fb_history_reconciler_rc6 import (
    HistoryCandidate, HistoryIdentity, HistoryReconciliationError,
    choose_winner, classify, discrepancy, assert_shadow_only,
)


def ident(*,basis='RAW',settlement='A-24HS',symbol='GGAL',family='ACCIONES',market='BYMA',date='2026-09-04'):
    return HistoryIdentity(symbol,family,market,settlement,date,basis)


def cand(source,*,basis='RAW',o=7000,h=7100,l=6900,c=7050,v=1000,at='2026-09-05T00:00:00+00:00',settlement='A-24HS',symbol='GGAL'):
    return HistoryCandidate(ident(basis=basis,settlement=settlement,symbol=symbol),source,at,o,h,l,c,v,payload_hash=source)


def test_full_iol_beats_a3_close_only_even_though_a3_has_better_rank():
    a3=cand('A3_CEM_CLOSING',o=None,h=None,l=None,v=None,c=7050)
    iol=cand('IOL',c=7048)
    result=choose_winner([a3,iol])
    assert result['winner'].source == 'IOL'
    assert result['quality'] == 'FULL_OHLCV'


def test_full_ppi_beats_discrepant_iol_absent_family_override():
    ppi=cand('PPI_PRODUCTION_HISTORY',c=7050)
    iol=cand('IOL',c=7150)
    result=choose_winner([iol,ppi])
    assert result['winner'].source == 'PPI_PRODUCTION_HISTORY'
    d=discrepancy(ppi,iol,relative_tolerance=0.005)
    assert d['status']=='SOURCE_DISCREPANCY'
    assert d['action']=='AUDIT_NO_AVERAGE'


def test_raw_and_adjusted_can_never_compete_in_same_selection():
    with pytest.raises(HistoryReconciliationError,match='IDENTITY_MIXED_OR_PRICE_BASIS_MIXED'):
        choose_winner([cand('PPI_API',basis='RAW'),cand('PPI_API',basis='ADJUSTED')])


def test_settlement_mismatch_is_fail_closed():
    with pytest.raises(HistoryReconciliationError,match='IDENTITY_MIXED_OR_PRICE_BASIS_MIXED'):
        choose_winner([cand('PPI_API',settlement='A-24HS'),cand('IOL',settlement='CI')])


def test_unknown_identity_is_fail_closed():
    bad=cand('IOL',settlement='UNKNOWN')
    assert classify(bad)=='IDENTITY_UNVERIFIED'
    with pytest.raises(HistoryReconciliationError,match='IDENTITY_UNVERIFIED'):
        choose_winner([bad])


def test_partial_ohlc_is_invalid_not_close_only():
    assert classify(cand('A3_CEM_CLOSING',o=7000,h=None,l=None,v=None))=='INVALID'


def test_close_only_is_explicit_low_completeness():
    assert classify(cand('A3_CEM_CLOSING',o=None,h=None,l=None,v=None))=='CLOSE_ONLY'


def test_later_observation_only_wins_inside_same_quality_and_rank():
    old=cand('IOL',at='2026-09-05T00:00:00+00:00',c=7000)
    new=cand('IOL',at='2026-09-05T01:00:00+00:00',c=7010)
    assert choose_winner([old,new])['winner'].close == 7010


def test_different_symbol_is_never_mixed():
    with pytest.raises(HistoryReconciliationError):
        choose_winner([cand('PPI_API'),cand('IOL',symbol='BMA')])


def test_no_db_or_network_capability_contract():
    assert_shadow_only()
    import ast, inspect, fb_history_reconciler_rc6 as module
    tree=ast.parse(inspect.getsource(module))
    imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    assert not any(x in {'requests','sqlite3'} or x.startswith('ppi') or x.startswith('ak_iol') for x in imports)
