"""A906: DOM snapshots must not be admitted on an unknown PAPER order count.

All captures and SQLite files in this module are synthetic and OFFHOST.
"""

import json
import sqlite3
import sys

import pytest

import rc6_contract_dom_importer as importer


_MISSING = object()


def _capture():
    return {
        "schema": "POROTA_RC6_PPI_AUTH_DOM_V1",
        "auth_status": "AUTHENTICATED_TRUSTED_DEVICE",
        "generated_at": "2026-11-07T12:00:00+00:00",
        "blocked_nonread": False,
        "routes": [{
            "requested": "/Cotizaciones/Acciones",
            "reached": True,
            "tables": [{
                "table_index": 0,
                "materializable": True,
                "headers": ["Especie", "Último"],
                "row_count": 1,
                "rows": [["TEST-SYNTHETIC", "1.00"]],
            }],
        }],
    }


def _run_capture(tmp_path, monkeypatch, capsys, payload):
    source = tmp_path / "sanitized-synthetic-dom.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    database = tmp_path / "synthetic-contract-evidence.db"
    monkeypatch.setattr(importer, "DB", str(database))
    monkeypatch.setattr(sys, "argv", ["rc6_contract_dom_importer.py", str(source)])
    exit_code = importer.main()
    return exit_code, json.loads(capsys.readouterr().out), database


@pytest.mark.parametrize("count", [
    _MISSING, None, False, True, "0", "1", "oops", 0.0, 1, -1, [], {},
], ids=[
    "missing", "null", "false", "true", "str-zero", "str-one", "not-number",
    "float-zero", "positive-one", "negative-one", "list", "object",
])
def test_unknown_real_order_counter_blocks_snapshot_before_db_write(
        tmp_path, monkeypatch, capsys, count):
    payload = _capture()
    if count is not _MISSING:
        payload["real_orders_sent"] = count

    exit_code, result, database = _run_capture(tmp_path, monkeypatch, capsys, payload)

    assert exit_code == 4
    assert result == {"state": "FAIL_CLOSED", "reason": "REAL_ORDER_SAFETY_INVARIANT", "records": 0}
    assert not database.exists(), "unsafe capture must not open or create the SQLite store"


def test_explicit_integer_zero_preserves_synthetic_readonly_snapshot_contract(
        tmp_path, monkeypatch, capsys):
    payload = _capture()
    payload["real_orders_sent"] = 0

    exit_code, result, database = _run_capture(tmp_path, monkeypatch, capsys, payload)

    assert exit_code == 0
    assert result["state"] == "GREEN"
    assert result["records"] == 1
    assert result["families"] == ["ACCIONES"]
    assert result["real_orders_sent"] == 0
    assert database.is_file()
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT family,ticker,market,currency,settlement,source_class "
            "FROM contract_evidence_v2_current"
        ).fetchall()
    assert rows == [("ACCIONES", "*", "UNKNOWN", "UNKNOWN", "UNKNOWN", "PPI_AUTHENTICATED_WEB")]
