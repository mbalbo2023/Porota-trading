from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from fa_iol_history_readonly_rc6 import (
    IOLHistoryIdentity,
    IOLHistoryReadOnlyClient,
    IOLReadOnlyPolicyViolation,
    assert_iol_readonly_invariants,
)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith('/api/v2/token'):
            return FakeResponse(200, {'access_token':'fake','expires_in':900})
        return FakeResponse(200, {'precios':[
            {'fecha':'2026-09-04','apertura':2229,'maximo':2248,'minimo':2201,'ultimoPrecio':2216,'volumen':320651},
            {'fecha':'2026-09-07','apertura':2225,'maximo':2260,'minimo':2196,'ultimoPrecio':2203,'volumen':141513},
        ]})


def identity(verified=False):
    return IOLHistoryIdentity(
        symbol='CEPU', instrument_type='ACCIONES', market='bcba',
        settlement='A-24HS', source_term='T1',
        settlement_alignment_verified=verified,
    )


def test_transport_blocks_mutation_and_account_routes_before_network():
    assert_iol_readonly_invariants()
    c = IOLHistoryReadOnlyClient(username='u', password='p', session=FakeSession())
    for method, path in [
        ('POST','/api/v2/operar/estimar'),
        ('POST','/api/v2/operaciones'),
        ('PUT','/api/v2/operaciones/1'),
        ('DELETE','/api/v2/operaciones/1'),
        ('GET','/api/v2/portafolio/argentina'),
        ('GET','/api/v2/estadocuenta'),
    ]:
        with pytest.raises(IOLReadOnlyPolicyViolation):
            c._request(method, path)
    assert c.session.calls == []


def test_token_post_and_history_get_are_only_network_calls():
    s = FakeSession()
    c = IOLHistoryReadOnlyClient(username='u', password='p', session=s)
    bars = c.get_history_evidence(
        identity(False), from_date='2026-09-04', to_date='2026-09-07',
        price_basis='RAW', now=datetime(2026,9,7,12,50,tzinfo=TZ),
    )
    assert [call[0] for call in s.calls] == ['POST','GET']
    assert '/api/v2/token' in s.calls[0][1]
    assert '/seriehistorica/' in s.calls[1][1]
    assert len(bars) == 2


def test_unverified_t1_mapping_never_becomes_canonical():
    c = IOLHistoryReadOnlyClient(username='u', password='p', session=FakeSession())
    bars = c.get_history_evidence(
        identity(False), from_date='2026-09-04', to_date='2026-09-04',
        price_basis='RAW', now=datetime(2026,9,7,18,0,tzinfo=TZ),
    )
    assert bars[0].quality_class == 'FULL_OHLCV'
    assert bars[0].canonical_write_allowed is False
    assert bars[0].canonical_block_reason == 'ALIGNMENT_UNVERIFIED'


def test_verified_identity_still_cannot_self_promote():
    c = IOLHistoryReadOnlyClient(username='u', password='p', session=FakeSession())
    bars = c.get_history_evidence(
        identity(True), from_date='2026-09-04', to_date='2026-09-04',
        price_basis='RAW', now=datetime(2026,9,7,18,0,tzinfo=TZ),
    )
    assert bars[0].canonical_write_allowed is False
    assert bars[0].canonical_block_reason == 'IOL_CANONICAL_WRITE_NOT_AUTHORIZED'


def test_current_bcba_daily_bar_is_incomplete_while_market_open():
    c = IOLHistoryReadOnlyClient(username='u', password='p', session=FakeSession())
    bars = c.get_history_evidence(
        identity(True), from_date='2026-09-04', to_date='2026-09-07',
        price_basis='RAW', now=datetime(2026,9,7,12,50,tzinfo=TZ),
    )
    today = next(b for b in bars if b.date == '2026-09-07')
    assert today.canonical_write_allowed is False
    assert today.canonical_block_reason == 'INCOMPLETE_CURRENT_SESSION'


def test_raw_and_adjusted_are_explicit_distinct_series():
    c = IOLHistoryReadOnlyClient(username='u', password='p', session=FakeSession())
    raw = c.get_history_evidence(
        identity(False), from_date='2026-09-04', to_date='2026-09-04',
        price_basis='RAW', now=datetime(2026,9,7,18,0,tzinfo=TZ),
    )[0]
    adj = c.get_history_evidence(
        identity(False), from_date='2026-09-04', to_date='2026-09-04',
        price_basis='ADJUSTED', now=datetime(2026,9,7,18,0,tzinfo=TZ),
    )[0]
    assert raw.price_basis == 'RAW'
    assert adj.price_basis == 'ADJUSTED'
    assert '/false' in c.session.calls[1][1]
    assert '/true' in c.session.calls[3][1]


def test_no_legacy_iol_client_dependency():
    import inspect, fa_iol_history_readonly_rc6 as module
    source = inspect.getsource(module)
    assert 'import ak_iol_client' not in source
    assert 'from ak_iol_client' not in source
