#!/usr/bin/env python3
"""Static RC5 release preflight.

No Docker, broker, database, credentials or network access.  This checks only
versioned release invariants before an image is allowed to reach the host.
"""
from pathlib import Path
import re

import _version
import co_market_sessions_hf6 as sessions

ROOT = Path(__file__).resolve().parent
checks = []


def check(name, ok, detail):
    checks.append((name, bool(ok), str(detail)))


check("version_rc5", _version.VERSION == "17.0.0-rc5", _version.VERSION)
check("image_matches_version", _version.IMAGE == "porota-trading-bot:17.0.0-rc5", _version.IMAGE)
check("mode_paper", _version.MODE == "PRODUCTION_PAPER", _version.MODE)
check("execution_simulated", _version.EXECUTION == "SIMULATED", _version.EXECUTION)
check("real_orders_blocked", _version.REAL_ORDER_CAPABILITY == "BLOCKED", _version.REAL_ORDER_CAPABILITY)
check(
    "rc5_integration_base",
    _version.RC5_INTEGRATION_BASE_COMMIT == "109d328f5a40c3887eb2c96310a65b02e0acc264",
    _version.RC5_INTEGRATION_BASE_COMMIT,
)
check("historical_source_sha", bool(re.fullmatch(r"[0-9a-f]{40}", _version.RELEASE_SOURCE_COMMIT)), _version.RELEASE_SOURCE_COMMIT)
check("operative_baseline_sha256", bool(re.fullmatch(r"[0-9a-f]{64}", _version.OPERATIVE_BASE_SHA256)), _version.OPERATIVE_BASE_SHA256)

mode = (ROOT / "porota_mode_manager.py").read_text(encoding="utf-8")
check("split_observer", "porota_production_observer" in mode and "bv_paper_runtime.py" in mode, "observer split")
check("split_dashboard", "porota_production_dashboard" in mode and "o_dashboard.py" in mode, "dashboard split")
check("observer_read_only", '"--user", "botuser", "--read-only"' in mode, "docker --read-only")
check("ppi_secret_read_only", "ppi_production.json:ro" in mode, "secret mount :ro")
check("orders_blocked_declared", '"PPI_ORDERS": "BLOCKED"' in mode, "PPI_ORDERS BLOCKED")
check("runtime_open_1030", '"MARKET_OPEN_HOUR=10"' in mode and '"MARKET_OPEN_MINUTE=30"' in mode, "10:30")
check("runtime_close_1700", '"MARKET_CLOSE_HOUR=17"' in mode and '"MARKET_CLOSE_MINUTE=0"' in mode, "17:00")
check("legacy_1100_absent", '"MARKET_OPEN_HOUR=11"' not in mode, "no 11:00 runtime injection")

compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
check("compose_not_canonical_paper", "NO es el contrato canónico de PRODUCTION_PAPER" in compose, "legacy compose labelled")
check("no_runtime_memory_ses", not (ROOT / ":memory:.ses").exists(), ":memory:.ses absent")

ce_service = (ROOT / "deploy/systemd/porota-contract-evidence-rc5.service").read_text(encoding="utf-8")
ce_timer = (ROOT / "deploy/systemd/porota-contract-evidence-rc5.timer").read_text(encoding="utf-8")
ce_runtime = (ROOT / "scripts/porota_contract_evidence_rc5_runtime.sh").read_text(encoding="utf-8")
check("ce_rc5_service", "porota-contract-evidence-rc5-runtime.sh" in ce_service, "RC5 service")
check("ce_rc5_timer", "OnUnitActiveSec=5min" in ce_timer and "Persistent=" not in ce_timer, "5m monotonic due wakeup")
check("ce_exact_image_guard", "POROTA_CONTRACT_EVIDENCE_EXPECTED_IMAGE" in ce_runtime and "UNEXPECTED_IMAGE" in ce_runtime, "exact release image")
check("ce_not_due_no_browser", "STATUS=GREEN_NOT_DUE" in ce_runtime and "AUTH_BROWSER_STARTED=NO" in ce_runtime and "PPI_CALLS=0" in ce_runtime, "safe off-window")
check("ce_orders_zero", "REAL_ORDERS_SENT=0" in ce_runtime, "orders zero")
check("ce_no_force", "--force" not in ce_service and "--force" not in ce_timer, "due policy governs")

status = sessions.byma_schedule_status()
check("byma_communication", status.get("communication") == "19016" and status.get("source_date") == "2026-09-01", status)
check("byma_spot_open", status.get("paper_spot_regular_open") == "10:30", status.get("paper_spot_regular_open"))
check("byma_spot_close", status.get("paper_spot_regular_close") == "17:00", status.get("paper_spot_regular_close"))
check("byma_half_open", status.get("interval") == "[10:30,17:00)", status.get("interval"))
check("byma_special_fail_closed", status.get("extended_sessions_enabled") is False and status.get("unverified_special_sessions") == "FAIL_CLOSED", status)

for name, ok, detail in checks:
    print(f"{'GREEN' if ok else 'RED'}|{name}|{detail}")
failed = [row for row in checks if not row[1]]
print(f"RC5_PREFLIGHT={'GREEN' if not failed else 'RED'}")
print(f"CHECKS={len(checks)} FAILED={len(failed)}")
raise SystemExit(0 if not failed else 2)
