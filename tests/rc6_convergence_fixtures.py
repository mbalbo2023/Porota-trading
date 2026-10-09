"""Synthetic dated contracts for offline adversarial tests; no provider claims."""

def with_synthetic_volume_contract(record):
    record = dict(record)
    raw = dict(record.get('raw') or {})
    raw['intraday_volume_contract'] = {
        'schema': 'rc6.provider-volume-contract.v1', 'provider': 'PPI',
        'endpoint': 'MarketData/Intraday', 'family': record['instrument_type'],
        'verification': 'VERIFIED', 'evidence_ref': 'OFFLINE_SYNTHETIC_TEST_ONLY',
        'unit': 'QUANTITY', 'accumulation': 'INTERVAL', 'reset_rule': 'SESSION_ONLY',
        'effective_at': '2026-08-01T00:00:00+00:00', 'known_at': '2026-08-01T00:00:00+00:00',
    }
    record['raw'] = raw
    return record
