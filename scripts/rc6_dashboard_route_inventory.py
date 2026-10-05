#!/usr/bin/env python3
"""Inventory every registered dashboard route and its data authority.

This is intentionally executable: CI fails when a new route is registered but
not classified.  It never calls an endpoint and never opens a write-capable
database connection.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


os.environ.setdefault("POROTA_RUNTIME_MODE", "PAPER")
os.environ.setdefault("POROTA_MODE", "PAPER")
os.environ.setdefault("POROTA_DASHBOARD_TOKEN", "inventory-read-only")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import o_dashboard  # noqa: E402
from rc6_trader_dashboard.navigation import CANONICAL_PATHS, LEGACY, resolve  # noqa: E402


FRAMEWORK_ROUTES = {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}

ROUTE_CONCEPTS: dict[str, tuple[list[str], list[str], str]] = {
    "/": (["runtime", "safety", "readiness"], ["observer_state", "candidate_identity_v2"], "dashboard principal"),
    "/vivo": (["runtime", "readiness", "strategy_eligibility"], ["observer_state", "candidate_identity_v2", "paper_decisions", "paper_positions", "trade_gate_evaluations"], "actividad actual"),
    "/en-vivo": (["runtime", "readiness", "strategy_eligibility"], ["observer_state", "candidate_identity_v2", "paper_decisions", "paper_positions", "trade_gate_evaluations"], "actividad actual"),
    "/analisis": (["catalog", "readiness", "contract", "history", "iol_current", "caucion"], ["financial_instrument_catalog", "candidate_identity_v2", "contract_evidence_v2_current", "history_canonical_v2", "IOL section cache", "paper_caucion_allocations"], "análisis anual"),
    "/instrumentos": (["catalog", "readiness", "contract"], ["financial_instrument_catalog", "candidate_identity_v2", "contract_evidence_v2_current"], "instrumentos y contratos"),
    "/universo-operativo": (["catalog", "readiness", "contract", "strategy_eligibility", "caucion"], ["financial_instrument_catalog", "candidate_identity_v2", "contract_evidence_v2_current", "trade_gate_evaluations", "paper_cauciones"], "universo multi-familia"),
    "/historicos": (["history"], ["history_canonical_v2", "production_history", "candle store"], "histórico, sin autoridad sobre readiness"),
    "/aprendizaje": (["learning_history", "iol_current"], ["decision_evidence_latest", "paper_learning_samples", "IOL section cache"], "aprendizaje histórico y salud actual separada"),
    "/scalping": (["scalping", "readiness", "strategy_eligibility"], ["intraday_scalping_worker_state", "candidate_identity_v2", "ppi_intraday_contract_state", "scalping_candidates", "paper_positions"], "scalping PAPER/OBSERVE"),
    "/motor-trading": (["runtime", "readiness", "strategy_eligibility"], ["observer_state", "candidate_identity_v2", "trade_gate_evaluations", "paper_positions"], "motor PAPER visible"),
    "/trading": (["readiness", "contract", "strategy_eligibility", "caucion"], ["candidate_identity_v2", "contract_evidence_v2_current", "trade_gate_evaluations", "paper_cauciones"], "estrategias"),
    "/trading/{section}": (["readiness", "contract", "strategy_eligibility", "caucion"], ["candidate_identity_v2", "contract_evidence_v2_current", "paper_decisions", "paper_cauciones"], "estrategia por familia"),
    "/salud": (["runtime", "iol_current", "timers"], ["observer_state", "api_health", "source_sync", "IOL section cache"], "salud de fuentes"),
    "/health": (["runtime"], ["observer_state"], "health probe"),
    "/sistema": (["runtime", "timers", "readiness", "caucion"], ["observer_state", "systemd snapshot", "candidate_identity_v2", "paper_cauciones"], "sistema e introspección"),
    "/validacion": (["strategy_eligibility"], ["trade_gate_evaluations"], "validación SHADOW/BINDING"),
    "/riesgo": (["runtime", "safety"], ["observer_state", "trade_gate_evaluations", "risk ledgers"], "riesgo"),
    "/observacion": (["runtime", "catalog"], ["observer_state", "financial_instrument_catalog", "market_snapshots"], "observación"),
    "/informacion-financiera": (["history"], ["official_source_evidence", "financial snapshots"], "información financiera"),
    "/reportes": (["history", "learning_history"], ["paper_positions", "paper_learning_samples", "reports"], "reportes"),
    "/testing": (["runtime", "safety"], ["observer_state", "testing state"], "controles de testing"),
    "/telegram": (["runtime"], ["notification_outbox", "notification_worker_state"], "notificaciones"),
    "/sre": (["runtime", "timers"], ["sre snapshots", "systemd snapshot"], "SRE"),
    "/infra": (["runtime", "timers"], ["observer_state", "systemd snapshot"], "infraestructura"),
    "/ai-decisions": (["learning_history"], ["paper_decisions", "decision_evidence_latest"], "decisiones de IA"),
    "/dashboard/logs": (["runtime"], ["sanitized log snapshots"], "logs"),
    "/config": (["runtime", "safety"], ["runtime configuration"], "configuración"),
}

API_CONCEPTS: dict[str, tuple[list[str], list[str], str]] = {
    "/api/dashboard/truth": (["runtime", "catalog", "readiness", "contract", "history", "strategy_eligibility", "iol_current", "scalping", "caucion"], ["canonical dashboard truth projection"], "API canónica"),
    "/api/observer/state": (["runtime"], ["observer_state"], "estado runtime"),
    "/api/paper/caucion-allocations": (["caucion"], ["paper_caucion_allocations"], "asignaciones de caución"),
    "/api/learning-logs": (["learning_history"], ["paper_learning_samples"], "logs de aprendizaje"),
    "/api/observation-instruments": (["catalog", "readiness"], ["financial_instrument_catalog", "candidate_identity_v2"], "instrumentos observados"),
    "/api/dashboard": (["runtime"], ["observer_state", "paper_positions"], "snapshot dashboard"),
    "/api/v15/estado": (["runtime"], ["observer_state"], "compatibilidad v15"),
    "/api/v16/estado": (["runtime"], ["observer_state"], "compatibilidad v16"),
    "/api/testing/estado": (["runtime", "safety"], ["testing state"], "estado testing"),
    "/api/rc6/snapshots/latest": (["runtime", "timers"], ["rc6 snapshots"], "snapshot RC6"),
    "/api/sre/propuestas": (["runtime"], ["sre proposals"], "propuestas SRE"),
    "/api/failed-notifications": (["runtime"], ["notification_outbox"], "notificaciones fallidas"),
    "/api/logs/current": (["runtime"], ["sanitized log snapshots"], "log actual"),
    "/api/logs/download/{log_type}": (["runtime"], ["sanitized log snapshots"], "descarga de log"),
    "/api/diagnostics/download": (["runtime"], ["diagnostics snapshot"], "diagnóstico"),
    "/api/reports/weekly/download": (["history", "learning_history"], ["weekly reports"], "reporte semanal"),
    "/api/reports/{report_id}/{kind}": (["history", "learning_history"], ["reports"], "reporte versionado"),
    "/api/reports/action4": (["history"], ["action4 reports"], "reporte action4"),
    "/reports/monthly": (["history", "learning_history"], ["monthly reports"], "reporte mensual"),
    "/restart-request": (["runtime", "safety"], ["restart request ledger"], "solicitud controlada"),
    "/api/testing/autorizar": (["runtime", "safety"], ["testing authorization ledger"], "autorización testing"),
    "/api/sre/encolar/{propuesta_id}": (["runtime"], ["sre proposals"], "encolado SRE"),
}


def classify(path: str) -> tuple[list[str], list[str], str, str]:
    if path in FRAMEWORK_ROUTES:
        return [], ["FastAPI schema/UI"], "framework", "FRAMEWORK"
    destination, tab = resolve(path)
    if destination is not None and (path in CANONICAL_PATHS or path in LEGACY):
        concepts, datasets = TERMINAL_DATASETS[destination.key]
        return concepts, datasets, f"{destination.label} > {dict(destination.tabs)[tab]}", "VISIBLE_SURFACE"
    if path == "/api/trader/logs/download":
        return ["runtime"], ["bounded sanitized log snapshot"], "sanitized log tail", "API"
    values = ROUTE_CONCEPTS.get(path) or API_CONCEPTS.get(path)
    if values is None:
        raise KeyError(f"UNCLASSIFIED_ROUTE:{path}")
    concepts, datasets, surface = values
    return concepts, datasets, surface, "API" if path.startswith("/api/") else "VISIBLE_SURFACE"


TERMINAL_DATASETS = {
    "inicio": (["runtime", "safety"], ["observer_state", "paper_equity_by_currency", "paper_daily_risk", "paper_positions", "paper_future_positions", "paper_future_marks", "paper_family_lifecycle_events", "read_committed_generation V2: operational_funnel"]),
    "en-vivo": (["runtime", "strategy_eligibility", "readiness"], ["observer_state", "candidate_identity_v2", "paper_positions", "paper_future_positions", "paper_future_marks", "paper_family_lifecycle_events", "paper_future_exit_intents", "paper_decisions", "decision_evidence_snapshots", "trade_gate_evaluations", "runtime-health.json", "read_committed_generation V2: operational_funnel/engines"]),
    "trading": (["readiness", "strategy_eligibility", "contract"], ["candidate_identity_v2", "financial_instrument_catalog", "contract_evidence_v2_current", "committed SHADOW generation adapter"]),
    "universo": (["readiness", "strategy_eligibility", "catalog"], ["candidate_identity_v2", "financial_instrument_catalog", "committed SHADOW generation adapter"]),
    "instrumentos": (["catalog", "readiness", "contract"], ["financial_instrument_catalog", "candidate_identity_v2", "contract_evidence_v2_current", "contract_evidence_v2_snapshots", "market_snapshots"]),
    "riesgo": (["runtime", "safety"], ["paper_daily_risk", "paper_equity_by_currency", "paper_positions", "paper_future_positions", "paper_future_marks", "paper_family_lifecycle_events", "paper_exit_intents", "paper_future_exit_intents", "read_committed_generation V2: event_risk if published"]),
    "analitica": (["history", "learning_history"], ["paper_positions", "paper_future_positions", "paper_family_lifecycle_events", "history_versions_v2", "history_checks_v2", "report_registry", "read_committed_generation V2: entry_signal_lab/economic_exit_lab/operational_funnel"]),
    "sistema": (["runtime", "timers"], ["observer_state", "api_health", "runtime-health.json: child liveness and runtime_budget_snapshot", "canonical worker states", "systemd snapshot", "bounded sanitized log snapshot", "non-secret configuration allowlist", "read_committed_generation V2: independent generation integrity/freshness"]),
}


def inventory() -> dict[str, Any]:
    routes = []
    for route in sorted(o_dashboard.app.routes, key=lambda item: (item.path, sorted(item.methods or []))):
        concepts, datasets, surface, kind = classify(route.path)
        methods = sorted(route.methods or [])
        routes.append({
            "path": route.path,
            "methods": methods,
            "kind": kind,
            "title_or_surface": surface,
            "endpoint": f"{route.endpoint.__module__}.{route.endpoint.__name__}",
            "canonical_concepts": concepts,
            "datasets": datasets,
            "as_of_semantics": ("Timestamp propio de la fuente; UNKNOWN/STALE nunca se reemplaza por generated_at"
                                if kind != "FRAMEWORK" else "NOT_APPLICABLE"),
            "mode_authority": "observer_state" if kind != "FRAMEWORK" else "NOT_APPLICABLE",
            "ready_counter_authority": ("candidate_identity_v2" if "readiness" in concepts else "NOT_APPLICABLE"),
            "family_dimension": ("ticker + familia + mercado + moneda + settlement"
                                 if any(item in concepts for item in ("catalog", "readiness", "contract"))
                                 else "NOT_APPLICABLE"),
            "stale_unknown_semantics": ("VISIBLE_EXPLICIT; no se promueve a LIVE/READY"
                                        if kind != "FRAMEWORK" else "NOT_APPLICABLE"),
            "navigation": "Enlaces sin token; menú canónico y Arriba" if kind == "VISIBLE_SURFACE" else "NOT_APPLICABLE",
            "pagination": "Máximo 10 filas visibles + Mostrar más" if kind == "VISIBLE_SURFACE" else "Paginación/limit del contrato API",
            "accessibility": "ARIA + foco + texto además de color + Voice Access" if kind == "VISIBLE_SURFACE" else "JSON/HTTP",
            "locale": "es-AR" if kind == "VISIBLE_SURFACE" else "JSON canónico",
            "write_route": any(method not in {"GET", "HEAD", "OPTIONS"} for method in methods),
            "presentation_endpoint": ("rc6_trader_dashboard.routes.trader_terminal" if route.path in CANONICAL_PATHS or route.path in LEGACY else "NOT_APPLICABLE"),
            "canonical_view": ("/".join(CANONICAL_PATHS.get(route.path) or LEGACY.get(route.path)) if route.path in CANONICAL_PATHS or route.path in LEGACY else "NOT_APPLICABLE"),
            "legacy_compatible_alias": route.path in LEGACY,
        })
    return {
        "schema": "rc6-dashboard-route-inventory-v1",
        "route_count": len(routes),
        "registered_paths": len({row["path"] for row in routes}),
        "routes": routes,
        "terminal_contracts": {
            "generation": "rc6.shadow-evidence-generation.v2; pointer/manifest/members, safety and canonical reader",
            "shadow_root": "shadow_evidence_root(db): artifact_root(db)/dynamic-shadow; matching frozen overrides only",
            "sqlite": "readonly_copy(validate=False); coherent private main+WAL copy; source is never opened by SQLite",
            "positions": "Spot OPEN and future ACTIVE at exact UTC microsecond cut; full identity, family ledger and currency preserved",
            "funnel": "rc6.prospective-operational-funnel.v1; one currency/channel/native cohort for widget and stage cards",
            "labs": "rc6.runtime-entry-signals.v1 experiments/cohorts; rc6.runtime-shadow-lab.v2 entries; entry_authority=false",
            "history": "immutable versions selected by known_at/source authority; currency/price_basis/adjustment_basis isolated",
            "annual_fixed_income": "price variation excludes coupon/amortization/accrual/reinvestment; total return/TIR NO_VERIFICADO",
            "worker_health": "rc6.runtime-child-health.v1; child liveness, generation freshness and operational readiness are independent",
            "capacity": "native OFF/SHADOW/APPROVED/BASELINE_FAIL_CLOSED policy separate from measured OPEN evidence",
            "ppi_budget": "RC6_RUNTIME_BUDGET_SNAPSHOT_V1; read-only observed counters/EXIT pressure, never capacity certification",
            "greeks": "European Black-Scholes-Merton orientative; explicit exercise/dividend/rate contract; entry_authority=false",
            "source_closure": "o_dashboard.py and rc6_trader_dashboard Python package must belong to the same frozen SHA/tree/image",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = inventory()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
