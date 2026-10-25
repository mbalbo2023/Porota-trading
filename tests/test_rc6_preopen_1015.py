"""The 10:15 operational check must leave the 10:20 PAPER interlock intact."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import rc6_preopen
from rc6_paper_admission_gate import publish_preopen


ART = ZoneInfo("America/Argentina/Buenos_Aires")
DAY = datetime(2026, 10, 26, tzinfo=ART)
SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


def at(hour, minute):
    return DAY.replace(hour=hour, minute=minute)


def test_early_check_has_its_own_phase_and_time_for_the_binding_gate():
    timer = (ROOT / "systemd/porota-preopen-rc6.timer").read_text()
    service = (ROOT / "systemd/porota-preopen-rc6.service").read_text()
    expected = ["09:45", "10:15", "10:20"]
    assert [line.split(" ")[2][:5] for line in timer.splitlines()
            if line.startswith("OnCalendar=")] == expected
    assert "TimeoutStartSec=3min" in service
    assert [rc6_preopen.preopen_phase(at(10, minute))
            for minute in (14, 15, 19, 20, 29, 30)] == [
                "T_MINUS_45", "T_MINUS_15", "T_MINUS_15",
                "T_MINUS_10", "T_MINUS_10", "MANUAL_OR_OUTSIDE_SCHEDULE"]


def test_early_green_check_cannot_authorize_paper_before_final_gate(tmp_path):
    release, receipt = tmp_path / "release.json", tmp_path / "paper-gate.json"
    release.write_text(json.dumps({"candidate_sha": SHA, "validation_status": "VALIDATED_RUNTIME",
                                   "mode": "PRODUCTION_PAPER", "real_orders_sent": 0}))
    checks = {"paper_focus_contracts": {"state": "GREEN"}}
    early = publish_preopen({"status": "GREEN", "phase": rc6_preopen.preopen_phase(at(10, 15)),
                             "checks": checks}, now=at(10, 15), state_path=release,
                            receipt_path=receipt)
    assert early["status"] == "NO_GO_PAPER_SCALPING"
    final = publish_preopen({"status": "GREEN", "phase": rc6_preopen.preopen_phase(at(10, 20)),
                             "checks": checks}, now=at(10, 20), state_path=release,
                            receipt_path=receipt)
    assert final["status"] == "GO_PAPER_SCALPING"
