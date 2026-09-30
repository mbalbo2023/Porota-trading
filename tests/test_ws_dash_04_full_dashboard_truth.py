import ast
from pathlib import Path

from fastapi.testclient import TestClient

import ay_dashboard_auth as auth
import bg_paper_dashboard as bg
import bh_universe_dashboard_hf6 as universe
import be_paper_engine
import er_dashboard_truth_projection_rc6 as projection
import ep_dashboard_truth_layer_rc6 as truth_layer
import o_dashboard
import rc6_annual_instrument_analysis as annual
import zz_wave8_dashboard_live_rc6 as wave8
from scripts import rc6_dashboard_route_inventory as route_inventory


COUNTS = {
    "ACCIONES": 126,
    "CEDEARS": 683,
    "BONOS": 1674,
    "LETRAS": 31,
    "OBLIGACIONES": 2041,
    "FCI": 1003,
}


def _truth():
    families = [
        {
            "family": family,
            "catalog_total": count,
            "catalog_available": count,
            "candidate_total": count,
            "runtime_ready": count,
            "paused_explicit": 0,
            "state": "RUNTIME_READY",
            "catalog_as_of": "2026-09-29T10:00:00-03:00",
            "readiness_as_of": "2026-09-29T10:01:00-03:00",
        }
        for family, count in COUNTS.items()
    ]
    contracts = [
        {"family": family, "evidence_rows": count, "identities": count,
         "sources": 1, "as_of": "2026-09-29T09:59:00-03:00"}
        for family, count in COUNTS.items()
    ]
    return {
        "schema": projection.SCHEMA,
        "authorities": projection.AUTHORITY_MATRIX,
        "runtime": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0},
        "catalog": {"source": "financial_instrument_catalog", "total": 5558,
                    "available": 5558, "families": families},
        "readiness": {"source": "candidate_identity_v2", "total": 5558,
                      "ready": 5558, "paused_explicit": 0, "families": families,
                      "as_of": "2026-09-29T10:01:00-03:00"},
        "contract": {"source": "contract_evidence_v2_current", "families": contracts,
                     "evidence_rows": 5558, "identities": 5558},
        "history": {"role": "HISTORICAL_ONLY", "governs_readiness": False},
        "strategy_eligibility": {"state": "EVENT_DRIVEN", "evaluated_today": 0,
                                 "detail": "No existe un censo estático."},
        "iol": {"quotes": {"state": "SOURCE_UNAVAILABLE", "total": 0},
                "families": {"state": "CACHE_FRESH", "last_known_good_at": "2026-09-29T09:58:00-03:00",
                             "sections": {"caucion:ARS": {"state": "CACHE_FRESH",
                                                           "raw_source_state": "SOURCE_UNAVAILABLE",
                                                           "as_of": "2026-09-29T09:58:00-03:00"}}}},
        "scalping": {"mode": "ACTIVE_OBSERVE", "selected_last_cycle": 50,
                     "successful_last_cycle": 50, "failed_last_cycle": 0,
                     "real_orders_sent": 0},
        "caucion": {"ledger_state": "AVAILABLE", "open": 0, "settled": 0,
                    "allocation_status": "HOLD", "hold_reason": "NO_ELIGIBLE_OFFER",
                    "iol_source": {"state": "CACHE_FRESH", "raw_source_state": "SOURCE_UNAVAILABLE"},
                    "guarantee_required_for_placing_paper": False},
    }


def test_projection_uses_only_the_canonical_authorities_for_counts():
    seen = []

    def table(name):
        return name in {"observer_state", "financial_instrument_catalog",
                        "candidate_identity_v2", "contract_evidence_v2_current"}

    def query(sql, params=()):
        seen.append(sql)
        if "FROM observer_state" in sql:
            return [{"mode": "PRODUCTION_PAPER", "real_orders_sent": 0}]
        if "FROM financial_instrument_catalog" in sql:
            return [{"family": family, "total": count, "available": count,
                     "as_of": "2026-09-29T10:00:00-03:00"}
                    for family, count in COUNTS.items()]
        if "FROM candidate_identity_v2" in sql:
            return [{"family": family, "total": count, "ready": count,
                     "paused": 0, "as_of": "2026-09-29T10:01:00-03:00"}
                    for family, count in COUNTS.items()]
        if "FROM contract_evidence_v2_current" in sql:
            return [{"family": family, "evidence_rows": count, "identities": count,
                     "sources": 1, "as_of": "2026-09-29T09:59:00-03:00"}
                    for family, count in COUNTS.items()]
        raise AssertionError(sql)

    truth = projection.build(query, table, quote_payload={}, family_payload={})
    assert truth["readiness"]["source"] == "candidate_identity_v2"
    assert truth["readiness"]["ready"] == 5558
    assert truth["catalog"]["source"] == "financial_instrument_catalog"
    assert truth["contract"]["source"] == "contract_evidence_v2_current"
    assert truth["runtime"]["real_orders_sent"] == truth["scalping"]["real_orders_sent"] == 0
    assert truth["history"] == {
        "source": "history_canonical_v2 / candle store",
        "role": "HISTORICAL_ONLY",
        "governs_readiness": False,
    }
    assert not any("production_history" in sql or "catalog_family_coverage" in sql
                   or "candidate_universe" in sql for sql in seen)


def test_projection_aggregates_family_aliases_without_last_write_wins():
    def table(name):
        return name in {"observer_state", "financial_instrument_catalog", "candidate_identity_v2"}

    def query(sql, params=()):
        if "FROM observer_state" in sql:
            return [{"mode": "PRODUCTION_PAPER", "real_orders_sent": 0}]
        if "FROM financial_instrument_catalog" in sql:
            return [
                {"family": "OBLIGACIONES", "total": 2993, "available": 2993,
                 "as_of": "2026-09-30T13:01:22+00:00"},
                {"family": "ON", "total": 16, "available": 16,
                 "as_of": "2026-09-30T13:01:23+00:00"},
            ]
        if "FROM candidate_identity_v2" in sql:
            return [
                {"family": "OBLIGACIONES", "total": 2993, "ready": 2993, "paused": 0,
                 "as_of": "2026-09-30T13:01:22+00:00"},
                {"family": "ON", "total": 16, "ready": 0, "paused": 16,
                 "as_of": "2026-09-30T13:01:23+00:00"},
            ]
        raise AssertionError(sql)

    truth = projection.build(query, table, quote_payload={}, family_payload={})
    obligation_rows = [
        row for row in truth["readiness"]["families"]
        if row["family"] == "OBLIGACIONES"
    ]
    assert len(obligation_rows) == 1
    row = obligation_rows[0]
    assert row["candidate_total"] == 3009
    assert row["runtime_ready"] == 2993
    assert row["paused_explicit"] == 16
    assert row["readiness_as_of"] == "2026-09-30T13:01:23+00:00"
    assert truth["readiness"]["total"] == 3009
    assert truth["readiness"]["ready"] == 2993
    assert truth["readiness"]["paused_explicit"] == 16
    catalog_row = next(
        row for row in truth["catalog"]["families"]
        if row["family"] == "OBLIGACIONES"
    )
    assert catalog_row["catalog_total"] == 3009
    assert catalog_row["catalog_available"] == 3009


def test_truth_endpoint_payload_includes_the_full_canonical_projection(monkeypatch):
    canonical = _truth()
    monkeypatch.setattr(truth_layer.projection, "build", lambda *_args, **_kwargs: canonical)
    monkeypatch.setattr(truth_layer.bg, "_table", lambda _name: False)
    result = truth_layer.runtime_truth()
    assert result["readiness"]["source"] == "candidate_identity_v2"
    assert result["readiness"]["ready"] == 5558
    assert result["catalog"]["source"] == "financial_instrument_catalog"
    assert result["history"]["governs_readiness"] is False


def test_iol_live_cache_fresh_cache_stale_and_unavailable_are_distinct():
    assert projection.normalize_iol_state("LIVE") == "LIVE"
    assert projection.normalize_iol_state("CACHE_FRESH") == "CACHE_FRESH"
    assert projection.normalize_iol_state("CACHE_STALE") == "CACHE_STALE"
    assert projection.normalize_iol_state("SOURCE_UNAVAILABLE") == "SOURCE_UNAVAILABLE"
    assert projection.normalize_iol_state(
        "SOURCE_UNAVAILABLE", fallback_cache_state="CACHE_FRESH"
    ) == "CACHE_FRESH"
    truth = projection.iol_truth(
        quote_payload={"cache_state": "LIVE", "symbols": [{"state": "LIVE"}]},
        family_payload={"cache_state": "CACHE_FRESH",
                        "section_states": {"caucion:ARS": "SOURCE_UNAVAILABLE"},
                        "section_observed_at": {"caucion:ARS": "2026-09-29T10:00:00-03:00"}},
    )
    section = truth["families"]["sections"]["caucion:ARS"]
    assert truth["quotes"]["state"] == "LIVE"
    assert section["state"] == "CACHE_FRESH"
    assert section["raw_source_state"] == "SOURCE_UNAVAILABLE"


def test_dashboard_iol_default_points_to_the_container_data_mount():
    source = Path("er_dashboard_truth_projection_rc6.py").read_text(encoding="utf-8")
    assert '"/app/data/market"' in source
    assert '"/opt/porota-trading/data/market"' not in source


def test_cross_route_readiness_numbers_cannot_diverge(monkeypatch):
    truth = _truth()
    monkeypatch.setattr(bg, "truth_projection", lambda: truth)
    monkeypatch.setattr(bg, "_paper_history_by_family", lambda _normalizer: {})
    monkeypatch.setattr(bg, "_table", lambda _name: False)
    monkeypatch.setattr(annual.truth_projection, "build", lambda *_args, **_kwargs: truth)

    instruments = bg._family_ux_table(tuple(COUNTS))
    analysis = annual._render_family_readiness([])
    strategies = wave8._family_activity_section("")

    for count in COUNTS.values():
        expected = f"{count:,}/{count:,}".replace(",", ".")
        assert expected in instruments
        assert expected in analysis
        assert expected in strategies
    assert "5.558/5.558" in analysis


def test_learning_keeps_decision_time_evidence_separate_from_current_health(monkeypatch):
    monkeypatch.setattr(bg, "truth_projection", _truth)
    current = bg._current_source_health_panel()
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "Salud actual de fuentes" in current
    assert "CACHE_FRESH" in current and "SOURCE_UNAVAILABLE" in current
    assert "Fuentes al momento de la decisión" in source


def test_scalping_modes_and_caucion_placing_semantics_are_explicit(monkeypatch):
    monkeypatch.setattr(bg, "truth_projection", _truth)
    monkeypatch.setattr(bg, "_table", lambda _name: False)
    monkeypatch.setattr(bg, "PAPER_SCALPING_MODE", "ACTIVE_OBSERVE")
    observe = bg.scalping_page()
    assert "ACTIVE_OBSERVE: NO ABRE POSICIONES" in observe
    assert "5.558" in observe
    monkeypatch.setattr(bg, "PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    paper = bg.scalping_page()
    assert "fills exclusivamente simulados" in paper
    caucion = bg._caucion_truth_panel()
    assert "garantía como requisito genérico" in caucion
    assert "NO_ELIGIBLE_OFFER" in caucion


def test_locale_argentino_is_used_for_visible_numbers():
    assert bg._locale_number(1234567.89) == "1.234.567,89"
    assert bg._amount(1234567.89, "ARS") == "ARS $ 1.234.567,89"


def test_every_registered_route_has_a_programmatic_surface_and_dataset_inventory():
    inventory = route_inventory.inventory()
    assert inventory["registered_paths"] >= 53
    assert {"/analisis", "/instrumentos", "/universo-operativo", "/api/dashboard/truth"} <= {
        row["path"] for row in inventory["routes"]
    }
    assert all(row["datasets"] for row in inventory["routes"])
    assert all(row["kind"] == "FRAMEWORK" or row["canonical_concepts"]
               for row in inventory["routes"])


def test_every_core_read_only_surface_and_canonical_api_returns_http_200(tmp_path, monkeypatch):
    token = "D" * 40
    store = be_paper_engine.PaperStore(str(tmp_path / "paper.db"))
    monkeypatch.setattr(bg, "DB_PATH", store.path)
    monkeypatch.setattr(bg, "MODE", "PRODUCTION_PAPER")
    monkeypatch.setattr(auth, "TOKEN_BEARER", token)
    monkeypatch.setattr(auth, "ENTORNO", "SANDBOX")
    monkeypatch.setattr(auth, "SESSION_STORE_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setattr(auth, "_sesiones", {})
    monkeypatch.setattr(o_dashboard, "DASHBOARD_ACCESS_TOKEN", token)

    paths = (
        "/", "/en-vivo", "/trading", "/trading/bonos", "/universo-operativo",
        "/instrumentos", "/validacion", "/analisis", "/aprendizaje", "/scalping",
        "/riesgo", "/historicos", "/reportes", "/sistema", "/salud",
        "/api/dashboard/truth",
    )
    with TestClient(o_dashboard.app) as client:
        entry = client.get("/?token=" + token, follow_redirects=False)
        assert entry.status_code == 303
        failures = {path: response.status_code for path in paths
                    if (response := client.get(path)).status_code != 200}
    assert failures == {}


def test_active_surfaces_have_no_legacy_two_family_readiness_filters():
    files = (
        "bg_paper_dashboard.py",
        "bh_universe_dashboard_hf6.py",
        "rc6_annual_instrument_analysis.py",
        "zz_wave8_dashboard_live_rc6.py",
    )
    source = "\n".join(Path(name).read_text(encoding="utf-8") for name in files)
    forbidden = (
        "DESACTIVADO POR ALCANCE",
        "solo Acciones y CEDEARs",
        "sólo acciones/CEDEARs",
        "UPPER(instrument_type) IN ('ACCIONES','CEDEARS')",
        "rc6_family_readiness",
    )
    for text in forbidden:
        assert text not in source
    assert "data-porota-progressive-list='1'" in source
    assert "aria-label='Menú principal'" in bg.top_nav_html()


def test_universe_source_uses_candidate_identity_v2_and_all_families():
    source = Path(universe.__file__).read_text(encoding="utf-8")
    assert "candidate_identity_v2" in source
    assert "Universo operativo — todas las familias" in source
    assert "WHERE upper(instrument_type) IN" not in source


def test_universe_route_treats_null_catalog_aggregate_as_zero(monkeypatch):
    truth = _truth()
    truth["readiness"]["families"][0]["catalog_total"] = None
    monkeypatch.setattr(bg, "truth_projection", lambda: truth)
    monkeypatch.setattr(bg, "_table", lambda _name: False)
    page = universe._page()
    assert "Universo operativo" in page
    assert "ACCIONES" in page


def test_dashboard_sources_parse_with_runtime_python_311():
    for path in ("bh_universe_dashboard_hf6.py", "o_dashboard.py"):
        source = Path(path).read_text(encoding="utf-8")
        ast.parse(source, filename=path, feature_version=(3, 11))