"""Offline-only regression tests; no browser launch or PPI request."""
from __future__ import annotations

import hashlib
import json

from rc4_trusted_browser_contract_collector import (
    PASSIVE_FCI_MAX_BYTES,
    PASSIVE_FCI_MAX_IDENTITY_ROWS,
    PASSIVE_FCI_MAX_RESPONSES,
    SAFE_METHODS,
    append_passive_fci_receipt,
    sanitize_passive_fci_xhr,
)


PPI = 'https://trading.portfoliopersonal.com/api/FCIs/Detalle?account=never-persist'
OBSERVED = '2026-11-03T14:30:00+00:00'


def capture(payload, **overrides):
    body = json.dumps(payload).encode('utf-8')
    defaults = dict(
        url=PPI,
        method='GET',
        resource_type='xhr',
        status=200,
        headers={'content-type': 'application/json; charset=utf-8',
                 'content-length': str(len(body)),
                 'date': 'Tue, 03 Nov 2026 14:29:59 GMT'},
        body=body,
        observed_at=OBSERVED,
        authenticated=True,
    )
    defaults.update(overrides)
    return sanitize_passive_fci_xhr(**defaults)


def test_passive_complete_literal_five_key_is_only_unbound_candidate():
    payload = {'data': [{'ticker': 'FONDO1', 'instrument_type': 'FCI',
                         'market': 'FCI', 'currency': 'ARS',
                         'settlement': 'INMEDIATA',
                         'clase': 'A', 'horarioLimite': '14:00',
                         'subscriptionFee': '0.50',
                         'account_number': 'TOPSECRET-123',
                         'token': 'DONT-STORE-ME'}]}
    result = capture(payload)
    assert result['state'] == 'SOURCE_UNBOUND'
    assert result['source_authenticated'] is False
    assert result['account_tariff_verified'] is False
    assert result['auto_ready'] is False
    assert result['read_only'] is True
    assert result['endpoint'] == 'https://trading.portfoliopersonal.com/api/FCIs/Detalle'
    assert result['response_sha256_integrity_not_authentication'] == hashlib.sha256(
        json.dumps(payload).encode('utf-8')).hexdigest()
    assert result['unverified_ppi_five_key_candidates'] == [{
        'ticker': 'FONDO1', 'instrument_type': 'FCI',
        'market': 'FCI', 'currency': 'ARS', 'settlement': 'INMEDIATA'}]
    assert 'horariolimite' in result['field_names_observed_only']
    assert 'subscriptionfee' in result['field_names_observed_only']
    assert 'published_at' not in result
    assert 'effective_at' not in result
    assert result['http_response_date_not_contract_published_at'] == '2026-11-03T14:29:59+00:00'
    assert all(s not in json.dumps(result) for s in (
        'TOPSECRET', 'DONT-STORE', '0.50', 'never-persist', 'account_number', 'token'))


def test_unbound_when_fci_screen_has_no_identity_or_terms():
    # Real route known to collector, but a page visit or generic shape is
    # not an XHR receipt of account/class-specific financial terms.
    assert capture({}, url='https://trading.portfoliopersonal.com/Cotizaciones/FCIs',
                   resource_type='document') is None
    result = capture({'data': [{'fondo': 'A', 'clase': 'B', 'tarifa': 0}]})
    assert result['state'] == 'SOURCE_UNBOUND'
    assert result['unverified_ppi_five_key_candidates'] == []
    assert 'tarifa' in result['field_names_observed_only']
    assert '0' not in result['field_names_observed_only']


def test_existing_nonfci_known_xhr_does_not_count_as_fund_terms():
    # A603 recorded ConfiguracionOperatoriaSimplificada among five targets.
    assert capture({'client': 'ignored'},
                   url='https://trading.portfoliopersonal.com/api/ConfiguracionOperatoriaSimplificada') is None


def test_no_capture_without_genuine_authenticated_navigation():
    assert capture({'data': []}, authenticated=False) is None


def test_only_ppi_tls_origin_and_get_xhr_or_fetch():
    body = {'data': []}
    assert capture(body, url='https://trading.portfoliopersonal.com.evil.test/api/FCIs') is None
    assert capture(body, url='http://trading.portfoliopersonal.com/api/FCIs') is None
    assert capture(body, url='https://trading.portfoliopersonal.com:444/api/FCIs') is None
    assert capture(body, url='https://other.portfoliopersonal.com/api/FCIs') is None
    assert capture(body, method='POST') is None
    assert capture(body, resource_type='document') is None
    assert capture(body, status=206) is None
    assert capture(body, headers={'content-type':'text/html'}) is None
    assert 'POST' not in SAFE_METHODS


def test_oversize_body_or_declared_size_fails_closed():
    assert capture({'data':[]}, headers={'content-type':'application/json',
                                         'content-length':str(PASSIVE_FCI_MAX_BYTES+1)}) is None
    assert capture({}, body=b'[' + b' ' * PASSIVE_FCI_MAX_BYTES + b']') is None
    assert capture({}, headers={'content-type':'application/json','content-length':'bogus'}) is None


def test_pit_requires_aware_observation_and_never_promotes_http_date():
    assert capture({'data': []}, observed_at='2026-11-03T14:30:00') is None
    result = capture({'data': []}, headers={'content-type':'application/json'})
    assert result['observed_at'] == '2026-11-03T14:30:00+00:00'
    assert result['http_response_date_not_contract_published_at'] is None
    assert result['state'] == 'SOURCE_UNBOUND'


def test_ambiguous_or_incomplete_literals_never_silently_bind():
    rows = [
        {'ticker':'UNKNOWN', 'instrument_type':'FCI', 'market':'FCI',
         'currency':'ARS', 'settlement':'INMEDIATA'},
        {'ticker':'FONDO1','instrument_type':'FONDO', 'market':'FCI',
         'currency':'ARS', 'settlement':'INMEDIATA'},
        {'ticker':'FONDO2','instrument_type':'FCI','market':'FCI',
         'currency':'ARS'},
    ]
    result = capture({'data':rows})
    # This stage records literal candidates only; UNKNOWN must be excluded.
    assert result['unverified_ppi_five_key_candidates'] == []
    assert result['state'] == 'SOURCE_UNBOUND'


def test_candidate_row_count_is_bounded_and_deduped():
    rows = [
        {'ticker':f'FONDO{i:02}', 'instrument_type':'FCI',
         'market':'FCI', 'currency':'ARS', 'settlement':'INMEDIATA'}
        for i in range(PASSIVE_FCI_MAX_IDENTITY_ROWS+8)
    ]
    result = capture({'data':rows})
    assert len(result['unverified_ppi_five_key_candidates']) == PASSIVE_FCI_MAX_IDENTITY_ROWS
    assert result['identity_candidates_limit_reached'] is True
    assert result['auto_ready'] is False


def test_invalid_json_captures_only_integrity_and_unbound_reason():
    result = capture({}, body=b'{not-json')
    assert result['json_shape'] == 'UNPARSEABLE_JSON'
    assert result['field_names_observed_only'] == []
    assert result['state'] == 'SOURCE_UNBOUND'


def test_output_is_idempotent_and_capacity_is_explicit():
    report={'passive_fci_xhr':[], 'passive_fci_capture_truncated':False}
    first=capture({'data':[{'clase':'A'}]})
    assert append_passive_fci_receipt(report,first) is True
    assert append_passive_fci_receipt(report,first) is False
    assert len(report['passive_fci_xhr'])==1
    for index in range(1,PASSIVE_FCI_MAX_RESPONSES):
        assert append_passive_fci_receipt(report,capture({'data':[{'clase':str(index)}]})) is True
    assert append_passive_fci_receipt(report,capture({'data':[{'clase':'overflow'}]})) is False
    assert report['passive_fci_capture_truncated'] is True
    assert len(report['passive_fci_xhr'])==PASSIVE_FCI_MAX_RESPONSES

