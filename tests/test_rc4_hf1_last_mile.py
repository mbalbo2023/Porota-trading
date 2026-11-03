from datetime import datetime
from pathlib import Path
import sqlite3

from cf_sale_settlement import conservative_unconfirmed_availability, validated_sale_settlement
import cr_pending_settlement_diagnostics_hf6 as diag


def test_t1_without_cutoff_waits_until_full_expected_date_elapsed(monkeypatch):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    traded = "2026-09-01T15:00:00-03:00"
    boundary = conservative_unconfirmed_availability("A-24HS", traded)
    assert boundary is not None
    assert boundary.isoformat() == "2026-09-03T00:00:00-03:00"
    assert validated_sale_settlement("A-24HS", traded, None, "PENDING_CONFIRMATION") == boundary


def test_unknown_settlement_remains_fail_closed():
    assert conservative_unconfirmed_availability("UNKNOWN", "2026-09-01T15:00:00-03:00") is None
    assert validated_sale_settlement("UNKNOWN", "2026-09-01T15:00:00-03:00", None,
                                     "PENDING_CONFIRMATION") is None


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c


def test_pending_diagnostic_uses_symbol_alias_and_elapsed_boundary(tmp_path):
    db = tmp_path / "paper.db"
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE paper_positions(
        paper_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, currency TEXT NOT NULL,
        settlement TEXT NOT NULL, closed_at TEXT, status TEXT NOT NULL);
      CREATE TABLE paper_sale_receivables(
        paper_id TEXT PRIMARY KEY, currency TEXT NOT NULL, net_proceeds TEXT NOT NULL,
        available_at TEXT, basis TEXT NOT NULL);
      CREATE TABLE paper_equity_by_currency(
        id INTEGER PRIMARY KEY AUTOINCREMENT, measured_at TEXT NOT NULL,
        currency TEXT NOT NULL, cash TEXT NOT NULL, exposure TEXT NOT NULL,
        pending_proceeds TEXT NOT NULL, caucion_principal TEXT NOT NULL,
        caucion_accrued TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
        realized_pnl TEXT NOT NULL, equity TEXT NOT NULL);
    """)
    c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?)",
              ("PAPER-1", "ALUA", "ARS", "A-24HS", "2026-09-01T15:00:00-03:00", "CLOSED"))
    c.execute("INSERT INTO paper_sale_receivables VALUES(?,?,?,?,?)",
              ("PAPER-1", "ARS", "100", None, "PENDING_CONFIRMATION"))
    c.commit(); c.close()

    snap = diag.snapshot(Store(db), "2026-09-04T12:00:00-03:00")
    assert snap["state"] == "READY"
    assert snap["rows"][0]["ticker"] == "ALUA"
    assert snap["rows"][0]["state"] == "AVAILABLE_AFTER_FULL_SETTLEMENT_DATE"
    assert snap["live_pending"] == "0"


def test_contract_evidence_service_loads_host_profile_and_precreates_v2_schema():
    service = Path("systemd/porota-contract-evidence-rc4.service").read_text(encoding="utf-8")
    collector = Path("scripts/porota_contract_evidence_trusted_rc4.sh").read_text(encoding="utf-8")
    assert "EnvironmentFile=-/etc/porota/contract-evidence.env" in service
    assert "CONTRACT_V2_SCHEMA=READY" in collector
    assert "init_schema(Store(DB))" in collector


def test_contract_evidence_sanitized_capture_is_readable_by_observer_importer():
    collector = Path("scripts/porota_contract_evidence_trusted_rc4.sh").read_text(encoding="utf-8")
    gid_at = collector.index('RUNTIME_GID=')
    dir_chmod_at = collector.index('chmod 0750 "$OUTDIR"')
    file_chmod_at = collector.index('chmod 0640 "$OUT"')
    import_at = collector.index('rc4_contract_import_job.py --input')
    assert gid_at < dir_chmod_at < import_at
    assert file_chmod_at < import_at
    assert 'chown 0:"$RUNTIME_GID" "$OUTDIR"' in collector
    assert 'chown 0:"$RUNTIME_GID" "$OUT"' in collector
    assert "credenciales de sesión" in collector
