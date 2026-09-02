import json
import sqlite3
from pathlib import Path

import bd_ppi_readonly_guard as readonly
import ci_ppi_bond_estimate_patch_hf6  # noqa: F401
import ch_contract_evidence_hf6 as evidence
import cm_special_family_discovery_hf6 as discovery


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c


def schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE financial_instrument_catalog(
          ticker TEXT NOT NULL, instrument_type TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL, settlement_source TEXT NOT NULL,
          description TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL,
          status TEXT NOT NULL, capability TEXT NOT NULL, metadata_json TEXT NOT NULL,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
        CREATE TABLE catalog_family_coverage(
          instrument_type TEXT PRIMARY KEY, run_id TEXT NOT NULL,
          declared INTEGER NOT NULL, queries INTEGER NOT NULL,
          observed_count INTEGER NOT NULL, ready_paper_count INTEGER NOT NULL,
          discovery_status TEXT NOT NULL, checked_at TEXT NOT NULL);
        CREATE TABLE broker_market_configuration(
          name TEXT PRIMARY KEY, checked_at TEXT NOT NULL, payload_json TEXT NOT NULL);
        """)
    evidence.init_schema(store)


def test_documented_bond_endpoint_is_get_only_allowlisted():
    assert "/api/1.0/marketdata/bonds/estimate" in readonly._GET_PATHS
    guard = readonly.ReadOnlyTransportGuard()
    assert guard.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Bonds/Estimate")
    try:
        guard.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Bonds/Estimate")
    except readonly.ReadOnlyPolicyViolation:
        pass
    else:
        raise AssertionError("Bonds/Estimate must remain GET-only")


def test_bond_estimate_is_evidence_not_automatic_multiplier(tmp_path):
    store = Store(tmp_path / "evidence.db")
    schema(store)
    raw = {
        "ticker": "AL30", "type": "BONOS", "market": "BYMA", "currency": "PESOS",
        "nominalInPrice": 100, "isin": "TEST", "cajaValoresCode": "TEST",
    }
    with store.connect() as c:
        c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("AL30","BONOS","BYMA","ARS","A-24HS","REQUEST_CANDIDATE","",
                   "2026-09-02T00:00:00+00:00","run","AVAILABLE","NEEDS_NOMINAL_UNITS",
                   json.dumps(raw)))
        c.execute("INSERT INTO catalog_family_coverage VALUES(?,?,?,?,?,?,?,?)",
                  ("BONOS","run",1,1,1,0,"INSTRUMENTS_OBSERVED","2026-09-02T00:00:00+00:00"))

    class Reader:
        def current(self, *args):
            return {"price": 100, "date": "2026-09-02T00:00:00+00:00"}
        def estimate_bond(self, *args, **kwargs):
            return [{"amountToInvest": 99.5, "quantityTitles": 1, "currency": "ARS",
                     "expirationDate": "2030-07-09T00:00:00+00:00"}]

    evidence.collect(Reader(), store, run_id="test")
    with store.connect() as c:
        row = c.execute("SELECT status,owner,missing_fields_json,evidence_json FROM contract_evidence WHERE instrument_type='BONOS'").fetchone()
    assert row["status"] == "PPI_DOCUMENTED_ESTIMATE_COLLECTED_SEMANTICS_PENDING"
    assert row["owner"] == "PPI_SUPPORT"
    missing = json.loads(row["missing_fields_json"])
    assert "official_semantics_of_nominalInPrice" in missing
    recorded = json.loads(row["evidence_json"])
    assert recorded["nominalInPrice"] == 100
    assert "cash_multiplier" not in recorded


def test_caucion_empty_discovery_is_support_required_not_waiting(tmp_path):
    store = Store(tmp_path / "caucion.db")
    schema(store)
    with store.connect() as c:
        c.execute("INSERT INTO catalog_family_coverage VALUES(?,?,?,?,?,?,?,?)",
                  ("CAUCIONES","run",1,1,0,0,"EMPTY_FILTER_RESULTS","2026-09-02T00:00:00+00:00"))
    evidence.collect(object(), store, run_id="test")
    with store.connect() as c:
        row = c.execute("SELECT status,owner,detail FROM contract_evidence WHERE instrument_type='CAUCIONES'").fetchone()
    assert row["status"] == "PPI_SEARCH_HTTP200_EMPTY_SUPPORT_REQUIRED"
    assert row["owner"] == "PPI_SUPPORT"
    assert "Repetir la misma ingesta no completa" in row["detail"]


def test_special_probe_never_writes_trading_catalog(tmp_path):
    store = Store(tmp_path / "probe.db")
    schema(store)
    with store.connect() as c:
        for name, value in {
            "instrument_types": ["ETF", "CAUCIONES"],
            "markets": ["BYMA", "NYSE", "NASDAQ"],
        }.items():
            c.execute("INSERT INTO broker_market_configuration VALUES(?,?,?)",
                      (name,"2026-09-02T00:00:00+00:00",json.dumps(value)))

    class Reader:
        def search_instruments(self, ticker, family, name=None, market="BYMA"):
            if family == "ETF" and ticker == "SPY" and market == "NYSE":
                return [{"ticker":"SPY","type":"ETF","market":"NYSE","currency":"DOLARES","nominalInPrice":1}]
            return []

    result = discovery.probe(Reader(), store)
    assert result["ETF"]["records"] == 1
    assert result["CAUCIONES"]["records"] == 0
    with store.connect() as c:
        etf = c.execute("SELECT status,owner FROM contract_evidence WHERE instrument_type='ETF' AND ticker='*'").fetchone()
        cau = c.execute("SELECT status,owner FROM contract_evidence WHERE instrument_type='CAUCIONES' AND ticker='*'").fetchone()
        assert c.execute("SELECT COUNT(*) FROM financial_instrument_catalog").fetchone()[0] == 0
    assert etf["status"] == "PPI_DISCOVERY_CONFIRMED_SPECIALIZED_EXECUTOR_PENDING"
    assert etf["owner"] == "POROTA"
    assert cau["status"] == "PPI_SEARCH_HTTP200_EMPTY_SUPPORT_REQUIRED"
    assert cau["owner"] == "PPI_SUPPORT"
