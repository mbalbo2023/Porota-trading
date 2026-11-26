#!/usr/bin/env python3
"""Publish separated proof of offhost tests, live-data viability and release lock."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from xml.etree import ElementTree

from scripts.porota_testing_scope import TESTING_ONLY_MARKER


ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
            "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
LAB = "tests/test_rc6_iol_migration_offhost.py"
LAB_CLASS = "tests.test_rc6_iol_migration_offhost."
PROVIDER_HOLDS = {"FUTUROS": "IOL_A3_MARKET_UNVERIFIED",
                  "CAUCIONES": "IOL_REPO_BOOK_CLOCK_UNVERIFIED",
                  "FCI": "IOL_FUND_TERMS_AND_NAV_UNVERIFIED"}
DOMAINS = {
    "timers": ("tests/test_rc6_a145_timer_phases.py::test_verified_art_phases_and_com19024_snapshot_switch",
               "tests/test_rc6_a145_timer_phases.py::test_nontrading_holiday_closed_2027_never_launch_provider"),
    "workers": ("tests/test_porota_rootless_jobs.py", "tests/test_porota_runtime_controller.py"),
    "apis_current_book": (LAB, "tests/test_rc6_iol_rest_cutover.py", "tests/test_rc6_iol_oauth_health.py"),
    "history": (LAB + "::test_iol_daily_ingest_preserves_canonical_history", "test_hf6_history_data912_v2.py"),
    "candles": (LAB + "::test_iol_family_contract", "tests/test_rc6_ppi_gateway_family_ready.py"),
    "engines": ("tests/test_rc6_future_paper_engine.py", "tests/test_rc6_fixed_income_runtime.py",
                "tests/test_rc6_a232_fund_caucion_market_entry.py", "tests/test_cash_sweep_runtime_hf6.py",
                "tests/test_rc6_fund_paper_allocator.py"),
    "funnels": ("tests/test_rc6_shadow_operational_funnel.py", LAB + "::test_ggal_paper_ledger_and_exit"),
    "paper_exit": ("tests/test_rc6_take_profit_regression.py", LAB + "::test_ggal_paper_ledger_and_exit"),
    "deployment_lock": (LAB + "::test_predeploy_packaging_and_promotion_reject_testing_tree",),
}


def _test_name(node):
    return node.get("classname", "") + "." + node.get("name", "")


def _selected(name, pattern):
    path, _, testcase = pattern.partition("::")
    prefix = path.removesuffix(".py").replace("/", ".") + "."
    return name.startswith(prefix + testcase)


def summarize_focus(junit, exit_code):
    tests = {}
    if junit.is_file():
        for case in ElementTree.parse(junit).iter("testcase"):
            name = _test_name(case)
            passed = not any(case.find(problem) is not None for problem in ("error", "skipped", "failure"))
            tests[name] = passed and name not in tests
    domains = {}
    for area, patterns in DOMAINS.items():
        evidence = [[passed for name, passed in tests.items() if _selected(name, pattern)]
                    for pattern in patterns]
        domains[area] = "PASS" if all(cases and all(cases) for cases in evidence) else "FAIL"
    families = {}
    for family in FAMILIES:
        passed = tests.get(LAB_CLASS + f"test_iol_family_contract[{family}]") is True
        families[family] = {
            "fixture_test": "PASS" if passed else "FAIL",
            "fixture_source_path": ("HOLD" if family in PROVIDER_HOLDS else "OBSERVED") if passed else "UNVERIFIED",
            "market_viability": "INVIABLE",
            "market_blocker": PROVIDER_HOLDS.get(family, "IOL_FRESH_EXECUTABLE_PAIR_UNVERIFIED"),
        }
    passed = (exit_code == 0 and tests and all(tests.values()) and all(value == "PASS" for value in domains.values())
              and all(row["fixture_test"] == "PASS" for row in families.values()))
    return {"schema": "porota.iol.focus.v1", "status": "PASS" if passed else "FAIL",
            "count": len(tests), "failed_or_skipped": sum(not passed for passed in tests.values()),
            "domains": domains, "families": families}


def run_focus(output):
    forbidden = {"DO_HOST", "DO_USER", "DO_SSH_KEY", "IOL_PASSWORD", "PPI_API_KEY", "PPI_API_SECRET",
                 "TELEGRAM_BOT_TOKEN", "PAPER_V17_DB_PATH"}
    if (ROOT == Path("/opt/porota-trading") or os.environ.get("POROTA_IOL_OFFHOST") != "1"
            or forbidden.intersection(name for name in os.environ if os.environ[name])
            or not (ROOT / TESTING_ONLY_MARKER).is_file()):
        raise ValueError("IOL_OFFHOST_ISOLATION_REQUIRED")
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
        report = summarize_focus(junit, result.returncode)
        output.write_text(json.dumps(report, sort_keys=True) + "\n")
    return report["status"] == "PASS"


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
               and isinstance(families, dict) and set(families) == set(FAMILIES)
               and all(isinstance(row, dict) and row.get("fixture_test") == "PASS" for row in families.values())
               and isinstance(domains, dict) and set(domains) == set(DOMAINS)
               and all(value == "PASS" for value in domains.values()))
    return {"schema": "porota.iol.offhost.v1", **identity,
            "environment": "OFFHOST_REPLAY", "regression": "PASS" if regression else "FAIL",
            "paper_and_http": "PASS" if paper else "FAIL", "focused": "PASS" if focused else "FAIL",
            "offhost_tests": "PASS" if regression and paper and focused else "FAIL",
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
