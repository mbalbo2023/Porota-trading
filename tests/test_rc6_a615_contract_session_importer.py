"""A615: session path must use the canonical PPI 5-key contract importer.

Offhost synthetic DB + subprocess doubles only. No provider, host or orders.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

import rc4_contract_import_job as canonical
from scripts import porota_contract_evidence_session_runner_rc4 as session


def _store(tmp_path, rows):
    path = tmp_path / "a615-contracts.db"
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE financial_instrument_catalog(
          ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
          settlement TEXT, settlement_source TEXT, status TEXT)""")
        con.executemany(
            "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)", rows
        )
    return canonical.Store(str(path))


def _caucion(*, currency="ARS", source="PPI_FIELD", status="AVAILABLE"):
    return ("CACC1", "CAUCIONES", "BYMA", currency, "A-24HS", source, status)


def _import(monkeypatch, tmp_path, catalog, provider_rows):
    store = _store(tmp_path, catalog)
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({
        "jobs": ["CONTRACT_EVIDENCE_CAUCIONES"],
        "auth_status": "AUTHENTICATED_TRUSTED_DEVICE",
        "endpoints": {
            "https://trading.portfoliopersonal.com/api/CaucionesOperables": {
                "kind": "CaucionesOperables", "rows": provider_rows,
            }
        },
    }), encoding="utf-8")
    calls = []
    monkeypatch.setattr(canonical, "start_run", lambda *a, **kw: None)
    monkeypatch.setattr(canonical, "finish_run", lambda *a, **kw: None)

    def record(*a, **kw):
        calls.append(kw)
        return {"changed": False}

    monkeypatch.setattr(canonical, "record_snapshot", record)
    return canonical.import_capture(store, path), calls


def test_effective_session_runner_calls_one_canonical_importer():
    assert "from rc4_contract_import_job import DB, Store, import_capture" in session.IMPORTER
    assert "candidate_map(" not in session.IMPORTER
    assert "candidate_universe" not in session.IMPORTER
    assert "financial_instrument_catalog" not in session.IMPORTER
    assert "PPI_AUTHENTICATED_XHR" not in session.IMPORTER
    assert "real_orders_sent" not in session.IMPORTER


def test_bridge_receives_sanitized_capture_without_provider_call(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "ROOT", tmp_path)
    monkeypatch.setattr(session, "OBSERVER", "fake-observer")
    capture = tmp_path / "data" / "contract_evidence" / "capture.json"
    commands = []

    def fake_run(cmd, **kw):
        commands.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout='{"state":"AMARILLO"}', stderr="")

    monkeypatch.setattr(session.subprocess, "run", fake_run)
    code, output = session.import_capture(capture)
    assert code == 0
    assert json.loads(output)["state"] == "AMARILLO"
    assert len(commands) == 1
    assert commands[0][0] == [
        "docker", "exec", "-i", "fake-observer", "python", "-",
        "/app/data/contract_evidence/capture.json",
    ]
    assert commands[0][1]["input"] == session.IMPORTER


@pytest.mark.parametrize("returncode,body", [
    (3, '{"state":"ERROR","due_jobs":[]}'),
    (0, '{"state":"ERROR","due_jobs":[]}'),
    (0, '{"state":"OK","due_jobs":"NOT_A_LIST"}'),
    (0, '{"state":"OK","due_jobs":["UNAUTHORIZED_JOB"]}'),
    (0, "not-json"),
])
def test_due_check_failure_cannot_be_reported_as_cached(monkeypatch, returncode, body):
    monkeypatch.setattr(session.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=returncode, stdout=body))
    with pytest.raises(RuntimeError, match="CONTRACT_DUE_"):
        session.due_jobs(False)


def test_valid_due_receipt_accepts_only_exact_known_jobs(monkeypatch):
    key = next(iter(session.base.ROUTES))
    monkeypatch.setattr(session.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0,
                            stdout=json.dumps({"state": "OK", "due_jobs": [key, key]})))
    assert session.due_jobs(False) == [key]


def test_main_returns_error_instead_of_false_cached(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(session, "OUTDIR", tmp_path / "contract-output")
    monkeypatch.setattr(session, "runtime_gid", lambda: 1000)
    monkeypatch.setattr(session, "due_jobs", lambda force:
                        (_ for _ in ()).throw(RuntimeError("CONTRACT_DUE_READ_FAILED")))
    monkeypatch.setattr(session.sys, "argv", ["session-runner"])
    assert session.main() == 3
    out = capsys.readouterr().out
    assert "STATUS=ERROR|CONTRACT_DUE_READ_FAILED" in out
    assert "STATUS=CACHED_NOT_DUE" not in out


def test_caucion_unique_ppi_identity_is_partial_not_ars_principal(
        monkeypatch, tmp_path):
    result, calls = _import(monkeypatch, tmp_path, [_caucion()], [{
        "ticker": "CACC1", "currency": "ARS", "quantity": 1,
        "commission_rate_estimated": 0.004,
        "applied_exchange_rate": 1,
        "amount_invested_ars": 835.7, "payment_currency": "USD",
    }])
    # Contradictory payment currency is a valuation hint, not trading identity.
    assert result["records"] == 1 and result["conflicts"] == 0
    assert result["state"] == "AMARILLO"
    assert "CAUCIONES_PARTIAL_UNIT_UNVERIFIED" in result["notes"]
    assert len(calls) == 1
    target = calls[0]
    assert (target["ticker"], target["family"], target["market"],
            target["currency"], target["settlement"]) == (
                "CACC1", "CAUCIONES", "BYMA", "ARS", "A-24HS")
    evidence = target["evidence"]
    assert "quantity" not in evidence
    assert evidence["provider_quantity_unverified"] == {"quantity": 1}
    assert evidence["book_quantity_unit_status"] == "BOOK_QUANTITY_UNIT_UNVERIFIED"
    assert evidence["readiness_guard"] == "NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"
    for key in ("applied_exchange_rate", "amount_invested_ars",
                "payment_currency", "commission_rate_estimated"):
        assert key not in evidence
    assert "CAUCIONES_XHR_FEE_ESTIMATE_UNBOUND_CACC1" in result["notes"]


@pytest.mark.parametrize("catalog", [
    [_caucion(), _caucion(currency="USD_CCL",status="STALE")],
    [_caucion(source="IOL_COMPLEMENTARY")],
    [_caucion(status="STALE")],
])
def test_caucion_cross_currency_ambiguity_cannot_fanout(
        monkeypatch, tmp_path, catalog):
    result, calls = _import(monkeypatch, tmp_path, catalog, [
        {"ticker": "CACC1", "currency": "ARS", "quantity": 1}
    ])
    assert result["records"] == 0 and result["conflicts"] == 1
    assert calls == []
    assert any("CAUCIONES_PPI_FIVE_KEY_ABSENT_AMBIGUOUS" in x
               for x in result["notes"])


def test_schema_only_never_creates_wildcard_contract(monkeypatch, tmp_path):
    result, calls = _import(monkeypatch, tmp_path, [_caucion()], [])
    assert result["records"] == 0 and not calls
    assert "CAUCIONES_SCHEMA_ONLY_NOT_CONTRACT" in result["notes"]


def test_missing_ticker_never_creates_wildcard_contract(monkeypatch, tmp_path):
    result, calls = _import(monkeypatch, tmp_path, [_caucion()], [
        {"tenor_days": 1, "quantity": 1}
    ])
    assert result["records"] == 0 and result["conflicts"] == 1
    assert not calls
    assert "CAUCIONES_PPI_FIVE_KEY_ABSENT_AMBIGUOUS_UNKNOWN" in result["notes"]

def test_authenticated_empty_capture_never_reports_job_green(monkeypatch, tmp_path):
    result, calls = _import(monkeypatch, tmp_path, [_caucion()], [])
    assert result["state"] == "AMARILLO"
    assert result["records"] == 0 and not calls
    assert "AUTHENTICATED_XHR_NO_BOUND_CONTRACT_EVIDENCE" in result["notes"]
