from __future__ import annotations

import bg_paper_dashboard as bg
import er_dashboard_truth_projection_rc6 as projection
from scripts import rc6_zero_known_error_runtime_audit as runtime_audit


def _instrument(index: int):
    return {
        "ticker": f"T{index:02d}",
        "family": "ACCIONES",
        "market": "BYMA",
        "settlement": "A-24HS",
        "currency": "ARS",
        "catalog_status": "AVAILABLE",
        "catalog_capability": "PAPER",
        "ui_state": "RUNTIME_READY",
        "contract_sources": 1,
        "contract_as_of": "2026-09-30T18:00:00-03:00",
        "readiness_detail": "READY",
        "readiness_as_of": "2026-09-30T18:00:00-03:00",
    }


def test_instrument_projection_applies_limit_and_offset_in_sql():
    seen = {}

    def table(name):
        return name == "financial_instrument_catalog"

    def query(sql, params=()):
        seen["sql"] = sql
        seen["params"] = params
        return []

    projection.instrument_rows(query, table, limit=10, offset=20)
    assert "LIMIT ? OFFSET ?" in seen["sql"]
    assert seen["params"] == (10, 20)


def test_instrument_matrix_renders_only_ten_server_rows(monkeypatch):
    rows = [_instrument(index) for index in range(10)]
    monkeypatch.setattr(
        bg.dashboard_truth_projection,
        "instrument_rows",
        lambda *_args, **kwargs: rows,
    )

    html = bg._instrument_readiness_matrix(offset=0, limit=10, total=13046)

    assert html.count("data-porota-server-page-record='1'") == 10
    assert "Mostrando 1-10 de 13.046" in html
    assert "Mostrar más" in html
    assert "offset=10" in html
    assert "data-porota-progressive-list" not in html


def test_instrument_matrix_next_page_remains_ten_rows(monkeypatch):
    rows = [_instrument(index) for index in range(10, 20)]
    observed = {}

    def page(*_args, **kwargs):
        observed.update(kwargs)
        return rows

    monkeypatch.setattr(bg.dashboard_truth_projection, "instrument_rows", page)
    html = bg._instrument_readiness_matrix(offset=10, limit=10, total=13046)

    assert observed == {"limit": 10, "offset": 10}
    assert html.count("data-porota-server-page-record='1'") == 10
    assert "Mostrando 11-20 de 13.046" in html
    assert "offset=0" in html
    assert "offset=20" in html


def test_runtime_surface_guard_blocks_unbounded_instrument_page():
    metrics = {
        "/instrumentos": {
            "bytes": 100_000,
            "server_page_records": 11,
            "legacy_text_hits": [],
        }
    }
    try:
        runtime_audit._assert_surface_metrics(metrics)
    except RuntimeError as exc:
        assert "SERVER_PAGE_RECORDS=11>10" in str(exc)
    else:
        raise AssertionError("surface budget must fail closed")


def test_runtime_surface_metrics_detect_legacy_pending_contract():
    body = (
        "<div class='paper-card'><table><tr><td>PENDING contrato</td></tr></table></div>"
    ).encode()
    metrics = runtime_audit._surface_metrics("/instrumentos", body)
    assert metrics["legacy_text_hits"] == ["pending contrato"]


def test_universe_catalog_is_server_paged_and_no_generic_pending_label():
    source = __import__("pathlib").Path("bh_universe_dashboard_hf6.py").read_text(encoding="utf-8")
    assert "catalog_page=catalog[offset:offset+limit]" in source
    assert "data-porota-server-page-record='1'" in source
    assert "data-porota-progressive-list='1'" not in source
    assert '"OBSERVED_BLOCKED": bg._status("PENDIENTE")' not in source
    assert '"OBSERVED_BLOCKED": bg._status("OBSERVED_BLOCKED")' in source


def test_live_page_never_serializes_the_full_decision_day():
    source = __import__("pathlib").Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "live_decisions=all_live_decisions[decision_offset:decision_offset+limit]" in source
    assert "closed=closed_all[closed_offset:closed_offset+limit]" in source
    assert "data-porota-server-page-record='1'" in source
    assert "data-porota-record='1'" not in source


def test_runtime_budgets_cover_all_three_previous_full_dom_hotspots():
    assert runtime_audit.MAX_HTML_BYTES["/instrumentos"] == 350_000
    assert runtime_audit.MAX_HTML_BYTES["/universo-operativo"] == 550_000
    assert runtime_audit.MAX_HTML_BYTES["/vivo"] == 300_000
    assert runtime_audit.MAX_SERVER_PAGE_RECORDS["/instrumentos"] == 10
    assert runtime_audit.MAX_SERVER_PAGE_RECORDS["/universo-operativo"] == 10
    assert runtime_audit.MAX_SERVER_PAGE_RECORDS["/vivo"] == 20
    assert runtime_audit.MAX_PROGRESSIVE_RECORDS["/instrumentos"] == 0
    assert runtime_audit.MAX_PROGRESSIVE_RECORDS["/universo-operativo"] == 0
    assert runtime_audit.MAX_PROGRESSIVE_RECORDS["/vivo"] == 0
