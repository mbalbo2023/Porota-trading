from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import rc4_contract_schedule as schedule

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "porota_contract_evidence_rc5_runtime.sh"
SERVICE = ROOT / "deploy" / "systemd" / "porota-contract-evidence-rc5.service"
TIMER = ROOT / "deploy" / "systemd" / "porota-contract-evidence-rc5.timer"


def _fake_docker(tmp_path: Path) -> Path:
    p = tmp_path / "docker"
    p.write_text(
        r'''#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "inspect" ]]; then
  all="$*"
  if [[ "$all" == *".State.Running"* ]]; then echo true; exit 0; fi
  if [[ "$all" == *".Config.Image"* ]]; then echo "${FAKE_IMAGE:-porota-trading-bot:test}"; exit 0; fi
  if [[ "$all" == *".HostConfig.ReadonlyRootfs"* ]]; then echo "${FAKE_READONLY:-true}"; exit 0; fi
fi
if [[ "$1" == "exec" ]]; then
  all="$*"
  if [[ "$all" == *"rc4_contract_due_job.py"* ]]; then
    printf '%s\n' "${FAKE_DUE_JSON:-{\"state\":\"OK\",\"due_jobs\":[]}}"
    exit 0
  fi
  if [[ "$all" == *"python -"* ]]; then
    cat >/dev/null || true
    printf '%s\n' "${FAKE_PREFLIGHT_JSON:-{\"quick_check\":\"ok\",\"mode\":\"PRODUCTION_PAPER\",\"real_orders_sent\":0}}"
    exit 0
  fi
fi
exit 90
''',
        encoding="utf-8",
    )
    p.chmod(0o755)
    return p


def _run(tmp_path: Path, *, expected: str | None, image: str = "porota-trading-bot:test"):
    fake = _fake_docker(tmp_path)
    env = os.environ.copy()
    env.update(
        POROTA_DOCKER_BIN=str(fake),
        FAKE_IMAGE=image,
        POROTA_ROOT=str(ROOT),
    )
    if expected is None:
        env.pop("POROTA_CONTRACT_EVIDENCE_EXPECTED_IMAGE", None)
    else:
        env["POROTA_CONTRACT_EVIDENCE_EXPECTED_IMAGE"] = expected
    return subprocess.run(
        ["bash", str(WRAPPER)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )


def test_missing_expected_image_fails_closed(tmp_path):
    p = _run(tmp_path, expected=None)
    assert p.returncode == 3
    assert "STATUS=RED_FAIL_CLOSED" in p.stdout
    assert "EXPECTED_IMAGE_NOT_CONFIGURED" in p.stdout


def test_image_mismatch_fails_closed(tmp_path):
    p = _run(tmp_path, expected="porota-trading-bot:expected", image="porota-trading-bot:other")
    assert p.returncode == 3
    assert "UNEXPECTED_IMAGE:porota-trading-bot:other" in p.stdout


def test_exact_image_and_not_due_is_green_without_browser_or_ppi(tmp_path):
    p = _run(tmp_path, expected="porota-trading-bot:test")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "STATUS=GREEN_NOT_DUE" in p.stdout
    assert "AUTH_BROWSER_STARTED=NO" in p.stdout
    assert "PPI_CALLS=0" in p.stdout
    assert "REAL_ORDERS_SENT=0" in p.stdout


def test_schedule_boundaries_and_weekend_fail_closed():
    ar = schedule.AR_TZ
    def utc(y, m, d, hh, mm):
        return datetime(y, m, d, hh, mm, tzinfo=ar).astimezone(timezone.utc)

    dynamic = "CONTRACT_EVIDENCE_CAUCIONES"
    assert schedule.window_allows(dynamic, utc(2026, 9, 7, 10, 40)) is True
    assert schedule.window_allows(dynamic, utc(2026, 9, 7, 16, 59)) is True
    assert schedule.window_allows(dynamic, utc(2026, 9, 7, 17, 0)) is False
    assert schedule.window_allows(dynamic, utc(2026, 9, 5, 12, 0)) is False

    static = "CONTRACT_EVIDENCE_STATIC"
    assert schedule.window_allows(static, utc(2026, 9, 7, 17, 0)) is True
    assert schedule.window_allows(static, utc(2026, 9, 5, 18, 0)) is False


def test_systemd_scheduler_matches_five_minute_minimum_and_exact_release_env():
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/porota/contract-evidence-rc5.env" in service
    assert "porota-contract-evidence-rc5-runtime.sh" in service
    assert "OnUnitActiveSec=5min" in timer
    assert "Persistent=" not in timer
    assert "--force" not in service


def test_authenticated_collector_safety_contract_remains_versioned():
    collector = (ROOT / "rc4_trusted_browser_contract_collector.py").read_text(encoding="utf-8")
    session = (ROOT / "scripts" / "porota_contract_evidence_session_runner_rc4.py").read_text(encoding="utf-8")
    assert "SAFE_METHODS={'GET','HEAD','OPTIONS'}" in collector
    assert "return route.abort()" in collector
    assert "continue_clicked':False" in collector
    assert "amount_filled':False" in collector
    assert "price_filled':False" in collector
    assert "BLOCKED_AUTH_2FA_REQUIRED" in session
    assert 'if not sys.stdin.isatty()' in session
