#!/usr/bin/env python3
"""Publish separated proof of offhost tests, live-data viability and release lock."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from xml.etree import ElementTree

from scripts.porota_testing_scope import TESTING_ONLY_MARKER
from scripts.rc6_iol_offhost_timers import DISPATCH, SCHEMA as TIMER_INVENTORY_SCHEMA, timer_inventory


ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
            "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
LAB = "tests/test_rc6_iol_migration_offhost.py"
LAB_CLASS = "tests.test_rc6_iol_migration_offhost."
NATIVE_REPLAY = "tests/test_rc6_ppi_gateway_family_ready.py"
NATIVE_CASE = NATIVE_REPLAY + "::test_one_instrument_per_family_through_gateway_and_engine"
FI_RUNTIME = "tests/test_rc6_fixed_income_runtime.py"
FI_EXIT = "tests/test_rc6_fi_market_sale_authority.py::test_exit_reader_and_supervisor_replay_archived_wheel_books"
FI_ROUNDTRIP = "tests/test_rc6_fi_market_sale_authority.py::test_reviewed_market_lot_buys_then_sells_after_signer_ack"
FI_LOCKED_EXIT_GUARD = ("tests/test_rc6_fi_market_sale_authority.py"
                       "::test_market_buy_freeze_cannot_admit_after_exit_signer_is_revoked")
COLD_TO_HOT = ("tests/test_rc6_shadow_runtime_wiring.py"
               "::test_outside_preopen_native_discovery_promotes_warm_then_hot_never_opens")
CAUCION_SWEEP = ("tests/test_ws_caucion_12_live_ppi_wiring.py"
                 "::test_caucion_reconcile_and_sweep_keep_static_terms_separate_from_live_bid")
IOL_CAUCION_SWEEP = ("tests/test_rc6_iol_caucion_book_runtime.py"
                     "::test_book_arrival_after_hold_places_once_and_settles_cash_once")
FCI_CALENDAR = ("tests/test_rc6_a232_fund_caucion_market_entry.py"
                "::test_fci_weekday_go_and_ready_catalog_still_need_authenticated_class_calendar")
FCI_SETTLEMENT = ("tests/test_rc6_fund_attested_settlement.py"
                  "::test_runtime_settlement_applies_signed_receipts_once_and_credits_exact_payment")
PAPER_CYCLES = {
    "ACCIONES": ("BUY_SELL_SIMULATED", (NATIVE_CASE + "[ACCIONES]",)),
    "CEDEARS": ("BUY_SELL_SIMULATED", (NATIVE_CASE + "[CEDEARS]",)),
    "ETFS": ("BUY_SELL_WITH_TEST_FEES_UNVERIFIED_COST_HOLD",
             (NATIVE_CASE + "[ETFS]", NATIVE_CASE + "[MERV_TEST_FEE_CYCLE]")),
    "BONOS": ("BUY_AND_SIGNED_BOOK2_SALE_SIMULATED", (
        FI_ROUNDTRIP + "[BONOS]",
        FI_RUNTIME + "::test_engine_reports_precise_input_blocker_then_keeps_event_exit_unbound[BONOS]",
        FI_EXIT + "[GD30]")),
    "LETRAS": ("BUY_AND_REDEMPTION_SIMULATED", (
        FI_RUNTIME + "::test_reviewed_letter_opens_exact_vn_and_closes_only_after_the_matched_payment",)),
    "OBLIGACIONES": ("BUY_AND_SIGNED_BOOK2_SALE_SIMULATED", (
        FI_ROUNDTRIP + "[OBLIGACIONES]",
        FI_RUNTIME + "::test_engine_reports_precise_input_blocker_then_keeps_event_exit_unbound[OBLIGACIONES]",
        FI_EXIT + "[YMCJO]")),
    "OPCIONES": ("BUY_SELL_SIMULATED", (NATIVE_CASE + "[OPCIONES]",)),
    "FUTUROS": ("OPEN_MARGIN_CLOSE_SIMULATED", (NATIVE_CASE + "[FUTUROS]",)),
    "CAUCIONES": ("PLACEMENT_AND_MATURITY_SIMULATED", (CAUCION_SWEEP + "[CURRENT_STATIC-None]",)),
    "FCI": ("OFFLINE_SUBSCRIBE_REDEEM_RUNTIME_CALENDAR_HOLD", (FCI_CALENDAR, FCI_SETTLEMENT)),
}
PROVIDER_HOLDS = {"FUTUROS": "IOL_A3_MARKET_UNVERIFIED",
                  "CAUCIONES": "IOL_REPO_BOOK_CLOCK_UNVERIFIED",
                  "FCI": "IOL_FUND_TERMS_AND_NAV_UNVERIFIED"}
MULTI_REPLAY = (
    "tests/test_rc6_multi_replay_spot.py",
    "tests/test_rc6_multi_replay_fixed_income.py",
    "tests/test_rc6_multi_replay_contracts.py",
)
REPLAY_CASE_ID = re.compile(r"\[(\w+)[-:]([A-Z0-9][A-Z0-9./_-]{0,39})\]\Z")
IOL_SPOT = frozenset({"ACCIONES", "CEDEARS", "ETFS"})
IOL_SPECIALIZED = frozenset({"BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"})
DOMAINS = {
    "timers": ("tests/test_rc6_a145_timer_phases.py::test_verified_art_phases_and_com19024_snapshot_switch",
               "tests/test_rc6_a145_timer_phases.py::test_nontrading_holiday_closed_2027_never_launch_provider"),
    "workers": ("tests/test_porota_rootless_jobs.py", "tests/test_porota_runtime_controller.py"),
    "apis_current_book": (LAB, "tests/test_rc6_iol_rest_cutover.py", "tests/test_rc6_iol_oauth_health.py",
                          "tests/test_rc6_iol_trade_stream_pool.py"),
    "history": (LAB + "::test_iol_daily_ingest_preserves_canonical_history", "test_hf6_history_data912_v2.py",
                LAB + "::test_iol_local_eod_archive_does_not_certify_postclose_provider",
                "tests/test_rc6_issue600_regressions.py::test_local_archives_above_old_cold_cap_without_rewriting_provenance",
                "tests/test_rc6_issue600_regressions.py::test_local_disk_reserve_drop_rolls_back_receipt_and_cursor"),
    "candles": (LAB + "::test_iol_family_contract", NATIVE_REPLAY,
                LAB + "::test_iol_inmarket_candle_evidence_binds_both_frames_to_receipts",
                "tests/test_rc6_candle_inmarket_evidence.py"),
    "engines": ("tests/test_rc6_future_paper_engine.py", "tests/test_rc6_fixed_income_runtime.py",
                "tests/test_rc6_a232_fund_caucion_market_entry.py", "tests/test_cash_sweep_runtime_hf6.py",
                "tests/test_rc6_fund_paper_allocator.py"),
    "funnels": ("tests/test_rc6_shadow_operational_funnel.py", "tests/test_rc6_shadow_entry_signals.py",
                "tests/test_rc6_iol_coverage.py",
                COLD_TO_HOT, LAB + "::test_ggal_paper_ledger_and_exit",
                LAB + "::test_iol_scalping_native_consumes_interval_receipts_and_revokes_counter_reset",
                LAB + "::test_iol_dynamic_capacity_uses_completed_live_cycle_evidence",
                "tests/test_rc6_dynamic_orchestrator.py::test_iol_book_cannot_complete_native_ppi_spot_warmup",
                "tests/test_rc6_dynamic_orchestrator.py::test_runtime_current_book_has_distinct_clocks_and_cannot_promote_unconfirmed_volume"),
    "paper_exit": ("tests/test_rc6_take_profit_regression.py", LAB + "::test_ggal_paper_ledger_and_exit"),
    "native_gateway_scanner": (NATIVE_REPLAY,),
    "iol_gateway_scanner_exit": (
        LAB + "::test_ggal_paper_ledger_and_exit",
        LAB + "::test_runtime_iol_profile_starts_local_workers_without_ppi",
        LAB + "::test_iol_workers_observe_samples_and_hold_unqualified_cauciones",
        LAB + "::test_runtime_parent_registers_iol_scanner_and_exit_on_owner_rotation",
        LAB + "::test_iol_profile_rejects_legacy_provider_commands_before_gateway_attach",
        LAB + "::test_iol_scanner_phases_use_only_iol_and_hold_unqualified_trade_clock",
        "tests/test_rc6_iol_rest_cutover.py::test_gateway_owner_recovers_401_and_keeps_403_local_to_the_read",
        LAB + "::test_iol_gateway_429_blocks_opening_and_exit_until_the_shared_budget_recovers",
        LAB + "::test_iol_gateway_rolling_budget_defers_without_waiting_or_requesting_book",
        LAB + "::test_iol_gateway_binds_roles_to_separate_processes_and_rejects_stale_generation",
        LAB + "::test_iol_book_selects_best_prices_and_rejects_ambiguous_best_size",
    ),
    "family_lifecycles": (FI_RUNTIME, FI_EXIT, FI_ROUNDTRIP, FI_LOCKED_EXIT_GUARD,
                          CAUCION_SWEEP, IOL_CAUCION_SWEEP, FCI_CALENDAR, FCI_SETTLEMENT,
                          "tests/test_rc6_fund_attested_settlement.py",
                          "tests/test_rc6_option_paper_strategy.py"),
    "complementary_sources": ("tests/test_rc6_a234_cafci_fci_reference.py",
                              "tests/test_rc6_ppi_public_fci_reference.py",
                              "tests/test_rc6_future_contract_policy.py",
                              "tests/test_rc6_cauciones_official_contract.py"),
    "deployment_lock": (LAB + "::test_predeploy_packaging_and_promotion_reject_testing_tree",),
    "multi_instrument_paper": MULTI_REPLAY,
}


def _test_name(node):
    return node.get("classname", "") + "." + node.get("name", "")


def _selected(name, pattern):
    path, _, testcase = pattern.partition("::")
    prefix = path.removesuffix(".py").replace("/", ".") + "."
    return name.startswith(prefix + testcase)


def _summarize_instrument_cases(tests, elapsed):
    families = {family: {} for family in FAMILIES}
    malformed = 0
    for name, passed in tests.items():
        if not any(_selected(name, module) for module in MULTI_REPLAY):
            continue
        match = REPLAY_CASE_ID.search(name)
        if match is None or match[1] not in families or match[2] in families[match[1]]:
            malformed += 1
            continue
        family, ticker = match.groups()
        families[family][ticker] = {"status": "PASS" if passed else "FAIL",
                                     "seconds": elapsed.get(name)}

    rows = {}
    for family, instruments in families.items():
        path = "IOL_GATEWAY_PAPER" if family in IOL_SPOT else "NATIVE_CONTRACT_PAPER_REPLAY"
        hold = ("IOL_SPECIALIZED_ENTRY_EVIDENCE_REQUIRED" if family in IOL_SPECIALIZED
                else "IOL_MARKET_IDENTITY_UNSUPPORTED" if family in PROVIDER_HOLDS else None)
        passed = len(instruments) >= 3 and all(case["status"] == "PASS" for case in instruments.values())
        rows[family] = {"status": "PASS" if passed else "FAIL", "path": path,
                        "iol_fixture_hold": hold, "instruments": instruments}
    status = "PASS" if not malformed and all(row["status"] == "PASS" for row in rows.values()) else "FAIL"
    return {"status": status, "instrument_count": sum(len(items) for items in families.values()),
            "distinct_required_per_family": 3, "go_scope": "SYNTHETIC_FIXTURE",
            "clock_and_book_or_nav": "SYNTHETIC", "market_admission_effect": "NONE",
            "invalid_case_ids": malformed, "families": rows}


def summarize_focus(junit, exit_code, *, gateway_ipc):
    tests = {}
    elapsed = {}
    if junit.is_file():
        for case in ElementTree.parse(junit).iter("testcase"):
            name = _test_name(case)
            passed = not any(case.find(problem) is not None for problem in ("error", "skipped", "failure"))
            tests[name] = passed and name not in tests
            try:
                seconds = float(case.get("time", ""))
                if math.isfinite(seconds) and seconds >= 0:
                    elapsed[name] = round(seconds, 3)
            except ValueError:
                pass

    def passed_patterns(patterns):
        evidence = [[passed for name, passed in tests.items() if _selected(name, pattern)]
                    for pattern in patterns]
        return all(cases and all(cases) for cases in evidence)

    domains = {area: "PASS" if passed_patterns(patterns) else "FAIL"
               for area, patterns in DOMAINS.items()}
    multi = _summarize_instrument_cases(tests, elapsed)
    domains["multi_instrument_paper"] = multi["status"]
    families = {}
    for family in FAMILIES:
        passed = tests.get(LAB_CLASS + f"test_iol_family_contract[{family}]") is True
        native = passed_patterns((NATIVE_CASE + f"[{family}]",))
        cycle, sources = PAPER_CYCLES[family]
        cycle_passed = passed_patterns(sources)
        families[family] = {
            "fixture_test": "PASS" if passed else "FAIL",
            "fixture_source_path": ("HOLD" if family in PROVIDER_HOLDS else "OBSERVED") if passed else "UNVERIFIED",
            "native_scanner": ("REFERENCE_HOLD" if family in {"FCI", "CAUCIONES"} else "CONTRACT_ADMITTED")
            if native else "UNVERIFIED",
            "paper_cycle": cycle if cycle_passed else "UNVERIFIED",
            "market_viability": "INVIABLE",
            "market_blocker": PROVIDER_HOLDS.get(family, "IOL_FRESH_EXECUTABLE_PAIR_UNVERIFIED"),
        }
    passed = (exit_code == 0 and tests and all(tests.values()) and all(value == "PASS" for value in domains.values())
              and all(row["fixture_test"] == "PASS" and row["native_scanner"] != "UNVERIFIED"
                      and row["paper_cycle"] != "UNVERIFIED" for row in families.values()))
    return {"schema": "porota.iol.focus.v1", "status": "PASS" if passed else "FAIL",
            "count": len(tests), "failed_or_skipped": sum(not passed for passed in tests.values()),
            "native_gateway_transport": "AF_UNIX_PEERCRED" if gateway_ipc and passed else "IN_PROCESS",
            "iol_gateway_transport": ("AF_UNIX_PEERCRED" if gateway_ipc and
                                      domains["iol_gateway_scanner_exit"] == "PASS" else "IN_PROCESS"),
            "domains": domains, "families": families, "multi_instrument_replay": multi}


def run_focus(output):
    forbidden = {"DO_HOST", "DO_USER", "DO_SSH_KEY", "IOL_PASSWORD", "PPI_API_KEY", "PPI_API_SECRET",
                 "TELEGRAM_BOT_TOKEN", "PAPER_V17_DB_PATH"}
    if (ROOT == Path("/opt/porota-trading") or os.environ.get("POROTA_IOL_OFFHOST") != "1"
            or forbidden.intersection(name for name in os.environ if os.environ[name])
            or not (ROOT / TESTING_ONLY_MARKER).is_file()):
        raise ValueError("IOL_OFFHOST_ISOLATION_REQUIRED")
    report = run_fixture_suite()
    output.write_text(json.dumps(report, sort_keys=True) + "\n")
    return report["status"] == "PASS"


def run_fixture_suite():
    """Run the established fixture suite and retain only its typed summary."""
    with tempfile.TemporaryDirectory(prefix="rc6-iol-validation-") as temporary:
        directory = Path(temporary)
        junit = directory / "focused.xml"
        selected = sorted({test for patterns in DOMAINS.values() for test in patterns})
        # A module already selected by another domain owns its test cases once.
        selected = [test for test in selected if "::" not in test or test.split("::", 1)[0] not in selected]
        result = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                 "-o", "xfail_strict=true", "--basetemp", str(directory / "cases"),
                                 "--junitxml", str(junit), *selected], cwd=ROOT, check=False,
                                timeout=360, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"))
        return summarize_focus(junit, result.returncode,
                               gateway_ipc=os.environ.get("POROTA_IOL_OFFHOST_IPC") == "1")


def _read(directory, name):
    try:
        path = directory / name
        if path.is_file() and path.stat().st_size <= 1024 * 1024:
            data = json.loads(path.read_bytes())
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def verdict(directory, identity):
    focus = _read(directory, "focus.json")
    code = _read(directory, "code.json")
    functional = _read(directory, "functional.json")
    timer = _read(directory, "timer.json")
    timer_dispatch = _read(directory, "timer_inventory.json")
    def exact(value):
        return all(value.get(key) == expected for key, expected in identity.items())

    collected = code.get("collected")
    regression = bool(code.get("schema") == "porota.code-tests.v1" and code.get("status") == "PASS"
                      and exact(code) and type(collected) is int and 0 < collected == code.get("executed")
                      and all(type(code.get(key)) is int and code[key] == 0
                              for key in ("failed", "errors", "skipped")))
    paper = (functional.get("schema") == "porota.functional.v1" and functional.get("status") == "PASS"
             and exact(functional) and functional.get("order_routes_blocked") is True
             and type(functional.get("real_orders_sent")) is int and functional["real_orders_sent"] == 0)
    families, domains = focus.get("families"), focus.get("domains")
    focused = (focus.get("schema") == "porota.iol.focus.v1" and focus.get("status") == "PASS"
               and focus.get("native_gateway_transport") == "AF_UNIX_PEERCRED"
               and focus.get("iol_gateway_transport") == "AF_UNIX_PEERCRED"
               and isinstance(families, dict) and set(families) == set(FAMILIES)
               and all(isinstance(row, dict) and row.get("fixture_test") == "PASS"
                       and row.get("paper_cycle") == PAPER_CYCLES[family][0]
                       for family, row in families.items())
               and isinstance(domains, dict) and set(domains) == set(DOMAINS)
               and all(value == "PASS" for value in domains.values()))
    scheduled = (timer.get("schema") == "porota.iol.offhost.timer.v1"
                 and timer.get("scheduler") == "SYSTEMD_TRANSIENT_TIMER"
                 and exact(timer) and timer.get("triggered") is True
                 and timer.get("worker_completed") is True)
    expected_timers = timer_inventory(ROOT, identity["source_sha"])
    dispatched = (timer_dispatch.get("schema") == TIMER_INVENTORY_SCHEMA and exact(timer_dispatch)
                  and timer_dispatch.get("dispatch") == DISPATCH
                  and timer_dispatch.get("worker") == "READ_ONLY_SOURCE_HASH_CHECK"
                  and timer_dispatch.get("production_service_bodies") == "NOT_EXECUTED"
                  and timer_dispatch.get("timers") == {
                      name: {**source, "triggered": True, "worker_completed": True}
                      for name, source in expected_timers.items()})
    return {"schema": "porota.iol.offhost.v1", **identity,
            "environment": "OFFHOST_REPLAY", "regression": "PASS" if regression else "FAIL",
            "paper_and_http": "PASS" if paper else "FAIL", "focused": "PASS" if focused else "FAIL",
            "systemd_timer": "PASS" if scheduled else "FAIL",
            "active_timer_dispatches": len(expected_timers) if dispatched else 0,
            "native_gateway_transport": focus.get("native_gateway_transport", "UNVERIFIED"),
            "iol_gateway_transport": focus.get("iol_gateway_transport", "UNVERIFIED"),
            "offhost_tests": "PASS" if regression and paper and focused and scheduled and dispatched else "FAIL",
            "regression_tests": code.get("executed") if regression else None,
            "real_orders_sent": 0 if paper else None, "deployment_allowed": False,
            "market_viability": "INVIABLE", "reason": "IOL_EXECUTABLE_LIVE_COVERAGE_UNVERIFIED",
            "domains": domains if isinstance(domains, dict) else {},
            "families": families if isinstance(families, dict) else {}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("focus", "verdict"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--source-tree")
    parser.add_argument("--image-id")
    args = parser.parse_args()
    if args.action == "focus":
        return 0 if run_focus(args.output) else 1
    identity = {"source_sha": args.source_sha, "source_tree": args.source_tree, "image_id": args.image_id}
    if (not args.directory or not all(re.fullmatch("[0-9a-f]{40}", value or "") for value in
                                      (args.source_sha, args.source_tree))
            or not re.fullmatch("sha256:[0-9a-f]{64}", args.image_id or "")):
        parser.error("Exact test image identity is required")
    report = verdict(args.directory, identity)
    args.output.write_text(json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["offhost_tests"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
