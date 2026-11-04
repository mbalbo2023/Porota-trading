"""A657: Contract V2 Current is a literal family identity, not a display alias.

All fixtures are local synthetic SQLite evidence; there is no broker, PPI GET,
PPI Watch, real-order route, or live database interaction.
"""
import sqlite3
from datetime import datetime, timezone

import cp_contract_evidence_v2_hf6 as evidence
import rc6_contract_bridge as bridge


NOW = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _record(store, family, source="PPI_STRUCTURED_API", *, multiplier="0.01"):
    return evidence.record_snapshot(
        store, family=family, ticker="YMCJO", market="BYMA", currency="ARS",
        settlement="A-24HS", source_class=source,
        source_ref="A657_OFFHOST_SYNTHETIC_NOT_PROVIDER",
        observed_at=NOW.isoformat(),
        evidence={"currency": "ARS", "cash_multiplier": multiplier,
                  "quantity_step": "1", "quantity_min": "1"},
    )


def test_literal_obligaciones_does_not_collapse_into_legacy_on():
    assert evidence.normalize_family("OBLIGACIONES") == "OBLIGACIONES"
    assert evidence.normalize_family("ON") == "ON"
    # Older generic aliases must still be queryable, never silently migrated.
    assert evidence.normalize_family("OBLIGACIONES_NEGOCIABLES") == "ON"
    assert evidence.normalize_family("ETFS") == "ETF"


def test_legacy_on_only_cannot_appear_as_exact_obligaciones_contract(tmp_path):
    store = Store(tmp_path / "legacy-on.db")
    _record(store, "ON")
    assert len(evidence.current_records(store, family="ON")) == 1
    assert evidence.current_records(store, family="OBLIGACIONES") == []
    records = evidence.current_records(store, family="ON")
    denied = bridge.normalize_group(records, now=NOW)
    assert denied["contract_bridge"]["status"] == "BLOCKED"
    assert "ALIAS_ONLY_FAMILY_NOT_AUTHORITY" in denied["contract_bridge"]["gaps"]
    assert bridge.hard_blocked_bridge(denied["contract_bridge"]) is True
    assert denied["financial_contract_v17"] is None
    # The reconcile path drops the false-positive claim rather than pausing a
    # separate canonical READY row whose historical ON alias cannot prove it.
    assert bridge.complements_from_store(store, now=NOW) == []


def test_new_exact_obligaciones_and_old_on_coexist_without_alias_promotion(tmp_path):
    store = Store(tmp_path / "separated.db")
    on = _record(store, "ON")
    obligations = _record(store, "OBLIGACIONES")
    assert on["family"] == "ON"
    assert obligations["family"] == "OBLIGACIONES"
    with store.connect() as conn:
        rows = conn.execute("SELECT family,COUNT(*) FROM contract_evidence_v2_current "
                            "WHERE ticker='YMCJO' GROUP BY family ORDER BY family").fetchall()
    assert [(row[0], row[1]) for row in rows] == [("OBLIGACIONES", 1), ("ON", 1)]
    claims = bridge.complements_from_store(store, now=NOW)
    assert len(claims) == 1
    assert claims[0]["instrument_type"] == "OBLIGACIONES"
    assert claims[0]["contract_bridge"]["status"] == "NORMALIZED"
    assert claims[0]["financial_contract_v17"]["cash_multiplier"] == "0.01"


def test_legacy_on_and_exact_obligaciones_with_different_terms_never_merge(tmp_path):
    store = Store(tmp_path / "different-terms.db")
    _record(store, "ON")
    _record(store, "OBLIGACIONES", source="PPI_AUTHENTICATED_XHR", multiplier="0.02")
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM contract_evidence_v2_current").fetchone()[0] == 2
    claims = bridge.complements_from_store(store, now=NOW)
    assert len(claims) == 1
    assert claims[0]["instrument_type"] == "OBLIGACIONES"
    assert claims[0]["contract_bridge"]["status"] == "NORMALIZED"
    assert claims[0]["financial_contract_v17"]["cash_multiplier"] == "0.02"
