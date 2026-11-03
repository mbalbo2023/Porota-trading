"""Provider request identity cannot certify the currency/basis of PPI bars."""
from contextlib import contextmanager
import sqlite3

import pytest

import ct_ppi_history_salvage_hf6 as salvage


class Store:
    def __init__(self, path):
        self.path = str(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def bar(**changes):
    row = dict(date='2026-10-23T17:00:00-03:00', openingPrice=100,
               max=110, min=90, price=105, volume=50)
    row.update(changes)
    return row


def ingest(tmp_path, payload, *, contract=None):
    observer = Store(tmp_path / 'observer.sqlite')
    history = Store(tmp_path / 'history.sqlite')
    args = dict(symbol='GGAL', instrument_type='ACCIONES', market='BYMA',
                currency='ARS', settlement='A-24HS', payload=payload,
                attempted_at='2026-10-26T15:00:00Z', history_store=history,
                provider_contract_id=contract)
    return observer, history, args


def rows(store, table):
    with store.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM ' + table)]


def test_normal_ppi_response_is_quarantined_and_retry_keeps_zero_canonical(tmp_path):
    observer, history, args = ingest(tmp_path, [bar()])
    first = salvage.ingest_ppi_payload(observer, **args)
    retry = salvage.ingest_ppi_payload(observer, **args)
    assert first['provider_rows'] == first['rejected_rows'] == 1
    assert first['full_ohlc_rows'] == first['close_only_rows'] == 0
    assert first['versions_appended'] == first['canonical_updates'] == 0
    assert retry['attempt_id'] == first['attempt_id']
    assert retry['versions_appended'] == 0
    assert rows(history, 'history_versions_v2') == []
    assert rows(history, 'history_canonical_v2') == []
    assert rows(observer, 'history_row_rejections_v2')[0]['reason'] == 'HISTORY_PROVENANCE_UNVERIFIED'


def test_row_fields_cannot_override_absent_source_contract(tmp_path):
    observer, history, args = ingest(tmp_path, [bar(currency='ARS', priceBasis='RAW',
        adjustmentBasis='RAW_NO_ADJUSTMENT', adjusted=False)], contract='UNPINNED')
    result = salvage.ingest_ppi_payload(observer, **args)
    assert result['full_ohlc_rows'] == result['close_only_rows'] == 0
    assert rows(history, 'history_canonical_v2') == []
    assert rows(observer, 'history_row_rejections_v2')[0]['reason'] == 'HISTORY_PROVENANCE_UNVERIFIED'


@pytest.mark.parametrize('changes, reason', [
    ({}, 'HISTORY_CURRENCY_UNVERIFIED'),
    ({'currency': 'ARS', 'moneda': 'USD'}, 'HISTORY_CURRENCY_CONFLICT'),
    ({'currency': 'ARS', 'priceBasis': 'SPLIT'}, 'HISTORY_BASIS_UNVERIFIED'),
])
def test_even_synthetic_reviewed_contract_rejects_incomplete_row(
        tmp_path, monkeypatch, changes, reason):
    monkeypatch.setattr(salvage, 'VERIFIED_PPI_HISTORY_CONTRACTS',
                        frozenset({'SYNTHETIC_TEST_CONTRACT'}))
    row = bar(priceBasis='RAW', adjustmentBasis='RAW_NO_ADJUSTMENT',
              adjusted=False)
    row.update(changes)
    observer, history, args = ingest(tmp_path, [row], contract='SYNTHETIC_TEST_CONTRACT')
    result = salvage.ingest_ppi_payload(observer, **args)
    assert result['full_ohlc_rows'] == result['close_only_rows'] == 0
    assert rows(history, 'history_canonical_v2') == []
    assert rows(observer, 'history_row_rejections_v2')[0]['reason'] == reason
