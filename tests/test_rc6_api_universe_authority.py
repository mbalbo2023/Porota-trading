import pytest

import rc6_api_universe_authority as u


API_TYPES = [
    'ACCIONES','ACCIONES-USA','BONOS','CAUCIONES','CEDEARS','ETF','FCI',
    'FCI-EXTERIOR','FUTUROS','LEBAC','LETRAS','LICITACIONES','NOBAC','ON','OPCIONES'
]


def test_web_only_context_never_enters_binding():
    for family in ('INDICES','MONEDAS','TASAS'):
        x=u.classify_identity(family=family,ticker='CTX',market='BYMA',api_types=API_TYPES)
        assert x['status']=='CONTEXT_ONLY'
        assert x['context_only'] is True
        assert u.eligible_for_contract_binding(x) is False
        assert x['paper_candidate'] is False


def test_api_declared_requires_concrete_identity():
    x=u.classify_identity(family='FCI_LOCAL',ticker='*',market='UNKNOWN',api_types=API_TYPES)
    assert x['family']=='FCI'
    assert x['status']=='API_DECLARED_NOT_DISCOVERED'
    assert u.eligible_for_contract_binding(x) is False


def test_api_discovered_is_binding_eligible_but_never_auto_paper():
    x=u.classify_identity(family='ACCIONES_USA',ticker='MSFT',market='NASDAQ',api_types=API_TYPES)
    assert x['family']=='ACCIONES-USA'
    assert x['status']=='API_DISCOVERED'
    assert u.eligible_for_contract_binding(x) is True
    assert x['paper_candidate'] is False


def test_discovery_query_requires_explicit_market_name_and_ticker():
    q=u.DiscoveryQuery('ETF','SPY','SPY','NYSE')
    assert (q.family,q.ticker,q.name,q.market)==('ETF','SPY','SPY','NYSE')
    with pytest.raises(ValueError):
        u.DiscoveryQuery('ETF','SPY','','NYSE')


def test_module_has_no_execution_capability():
    u.assert_no_execution_capability()
