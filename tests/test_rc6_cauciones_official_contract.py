import pytest
import bd_ppi_readonly_guard as m


class FakeMarket:
    def __init__(self):
        self.calls=[]

    def search_instrument(self,ticker,name,market,instrument_type):
        self.calls.append((ticker,name,market,instrument_type))
        return [{
            'ticker': ticker,
            'description': f'{ticker} caucion',
            'currency': 'Pesos' if ticker.startswith('PESOS') else 'Dolares billete | MEP',
            'type': 'CAUCIONES',
            'market': 'BYMA',
            'nominalInPrice': 1,
        }]


class FakeReader(m.ProductionMarketReader):
    def __init__(self,market):
        self.fake_market=market

    def _market(self):
        return self.fake_market


def test_default_days_are_exact_live_proven_set():
    assert m.caucion_discovery_days('') == (1,2,7,30,120)


def test_day_configuration_is_bounded_and_deduplicated():
    assert m.caucion_discovery_days('1,7,7,120') == (1,7,120)
    with pytest.raises(ValueError): m.caucion_discovery_days('0')
    with pytest.raises(ValueError): m.caucion_discovery_days('366')


def test_legacy_caucion_alias_expands_to_official_ticker_plus_days(monkeypatch):
    monkeypatch.delenv('PPI_CAUCION_DISCOVERY_DAYS',raising=False)
    market=FakeMarket(); reader=FakeReader(market)
    rows=reader.search_instruments('CAUCION','CAUCIONES',name='CAUCION',market='BYMA')
    expected=[]
    for day in (1,2,7,30,120):
        expected.extend([
            (f'PESOS{day}',str(day),'BYMA','CAUCIONES'),
            (f'DOLAR{day}',str(day),'BYMA','CAUCIONES'),
        ])
    assert market.calls == expected
    assert len(rows)==10
    assert {r['ticker'] for r in rows} == {call[0] for call in expected}


def test_regular_search_is_unchanged():
    market=FakeMarket(); reader=FakeReader(market)
    rows=reader.search_instruments('GGAL','ACCIONES',name='GGAL',market='BYMA')
    assert market.calls == [('GGAL','GGAL','BYMA','ACCIONES')]
    assert rows[0]['ticker']=='GGAL'


def test_no_budget_or_order_surface_added():
    source=open(m.__file__,encoding='utf-8').read().lower()
    assert 'order/budget' not in source
    assert 'send_order' not in source
    assert 'place_order' not in source
    assert 'cancel_order' not in source
