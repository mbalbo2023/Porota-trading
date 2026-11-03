"""A230: a direct runtime PAPER spot writer cannot bypass BYMA's session.

Every fixture is synthetic and temporary.  Mocked GO is local to the test;
it neither creates an operational receipt nor authorizes a market order.
Technical replay remains available 24x7 on an independent offline book.
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from be_paper_engine import PaperBroker, PaperStore, Quote
import bu_instrument_catalog as catalog
import rc6_paper_admission_gate as gate


def _synthetic_broker(tmp_path, monkeypatch, stamp, *, effective_runtime=True,
                      session_policy=None):
    at = datetime.fromisoformat(stamp)
    runtime_path = tmp_path / "effective_paper.sqlite"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(runtime_path))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    PaperStore(str(runtime_path))
    path = runtime_path if effective_runtime else tmp_path / "offline_replay.sqlite"
    store = PaperStore(str(path))

    catalog.init_schema(store)
    source_path = Path(__file__).parent / "fixtures" / "ppi_catalog_20260827.json"
    raw = next(record for record in json.loads(source_path.read_text())['records']
               if record['ticker'] == 'ALUA' and record['type'] == 'ACCIONES')
    record = catalog.normalize_record(raw, "A-24HS", stamp, "A230_SYNTHETIC_ONLY")
    with store.connect() as db:
        catalog.persist(db, record)
        db.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2 (
            ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
            settlement TEXT, can_simulate INTEGER, status TEXT, detail TEXT,
            checked_at TEXT)""")
        db.execute("INSERT INTO candidate_identity_v2 VALUES (?,?,?,?,?,?,?,?,?)",
                   ("ALUA", "ACCIONES", "BYMA", "ARS", "A-24HS", 1,
                    "AVAILABLE", "SYNTHETIC_TEST_ONLY", stamp))

    terms = catalog.quote_terms(catalog.lookup(
        store, "ALUA", "ACCIONES", "A-24HS", market="BYMA", currency="ARS"),
        now=at)
    assert not terms["opening_block_reason"]
    quote = Quote("ALUA", "ACCIONES", "A-24HS", Decimal("100"),
                  Decimal("99.9"), Decimal("100"), Decimal("100000"),
                  Decimal("100000"), stamp, book_at=stamp, trade_at=stamp,
                  last_kind="TRADE", **terms)
    store.add_quote(quote)
    broker = PaperBroker(
        store, clock_fn=lambda: stamp, session_policy=session_policy,
        require_supervisor=False, ai_mode="OFF", economics_mode="SHADOW",
        admission_guard=gate.paper_admission_guard)
    # A local stand-in for the T-10 receipt.  No real operational GO or PPI data.
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    return store, broker, quote


def _assert_no_spot_entry(store):
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        state = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        assert tuple(state) == ("PRODUCTION_PAPER", 0)


@pytest.mark.parametrize("stamp,expected", [
    ("2026-10-31T10:46:00-03:00", "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"),
    ("2026-11-09T10:46:00-03:00", "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"),
    ("2026-11-02T17:31:00-03:00", "OUTSIDE_PAPER_EXECUTION_WINDOW"),
    ("2026-11-02T16:31:00-03:00", "EOD_NO_NEW_ENTRIES"),
])
def test_effective_runtime_enforces_default_byma_session_without_policy(
        tmp_path, monkeypatch, stamp, expected):
    store, broker, quote = _synthetic_broker(tmp_path, monkeypatch, stamp)
    assert broker.session_policy is None  # reproduces the missed native policy
    opened, reason, paper_id = broker.admit_paper_candidate(quote, Decimal("0.8"), {})
    assert (opened, reason, paper_id) == (False, expected, None)
    _assert_no_spot_entry(store)


def test_effective_runtime_allows_audited_open_session_only_in_paper_zero(
        tmp_path, monkeypatch):
    store, broker, quote = _synthetic_broker(
        tmp_path, monkeypatch, "2026-11-02T10:46:00-03:00")
    opened, reason, paper_id = broker.admit_paper_candidate(quote, Decimal("0.8"), {})
    assert opened, reason
    assert paper_id.startswith("PAPER-")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] == 1
        assert db.execute("SELECT side FROM paper_fills").fetchone()[0] == "BUY_SIMULATED"
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == (
            "PRODUCTION_PAPER", 0)


def test_weekend_offline_replay_is_not_a_live_market_entry(tmp_path, monkeypatch):
    store, broker, quote = _synthetic_broker(
        tmp_path, monkeypatch, "2026-10-31T10:46:00-03:00",
        effective_runtime=False)
    opened, reason, paper_id = broker.admit_paper_candidate(quote, Decimal("0.8"), {})
    assert opened, reason  # historical/synthetic evaluation remains 24x7
    assert paper_id.startswith("PAPER-")
    with store.connect() as db:
        assert db.execute("SELECT side FROM paper_fills").fetchone()[0] == "BUY_SIMULATED"
        assert db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
