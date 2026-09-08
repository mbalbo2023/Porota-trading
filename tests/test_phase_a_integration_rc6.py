import json

import bl_candle_engine as candles
import bf_production_paper_observer as observer
import fa_raw_evidence_store_rc6 as evidence


class NoObserverWrite:
    def execute(self, *args, **kwargs):
        raise AssertionError("PPI_HISTORY external mode must not write historical_raw_archive")


def _payload():
    return [{
        "date": "2026-09-05T17:00:00-03:00",
        "openingPrice": 100,
        "max": 105,
        "min": 99,
        "price": 102,
        "volume": 123,
    }]


def test_archive_raw_routes_only_ppi_history_to_external_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(evidence.RAW_STORAGE_ENV, evidence.EXTERNAL_V1)
    wrapper = {
        "symbol": "GGAL",
        "asset_class": "ACCIONES",
        "settlement": "A-24HS",
        "date_from": "2025-09-08",
        "date_to": "2026-09-08",
        "metadata": {"market": "BYMA"},
        "valid_rows": 1,
        "payload_json": json.dumps(_payload()),
    }
    assert candles.archive_raw(
        NoObserverWrite(), origin="PPI_HISTORY", row_key="attempt-1",
        payload=wrapper, recorded_at="2026-09-08T10:00:00-03:00",
        quality="VALID_PAYLOAD",
    ) is True
    assert evidence.manifest_metrics()["attempts"] == 1


def test_download_histories_skips_network_when_coordinator_busy(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(evidence.COORDINATOR_ENV, evidence.EXTERNAL_V1)

    class Reader:
        calls = 0
        def history(self, *args):
            self.calls += 1
            raise AssertionError("competing trigger must not reach PPI")

    class Store:
        def __init__(self):
            self.events = []
        def event(self, kind, detail):
            self.events.append((kind, detail))

    reader = Reader()
    store = Store()
    with evidence.ppi_history_ingest_lease() as lease:
        assert lease.acquired is True
        assert observer._download_histories(reader, store) == 0
    assert reader.calls == 0
    assert store.events and store.events[-1][0] == "HISTORY_INGEST_SKIPPED"
