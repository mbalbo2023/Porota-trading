"""A180 finite operator hold and rearm migration, including 30-Oct.

All scheduler calls are mocked; no market, Docker, systemctl or broker writes.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import rc6_paper_admission_gate as admission
import rc6_preopen as preopen
from scripts import rc6_deploy_preopen_gate as deploy_gate
from scripts import rc6_deploy_scheduler_suspend as suspend


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
NOW = datetime(2026, 10, 30, 10, 21, tzinfo=TZ)
SHA = "a" * 40
TREE = "b" * 40
DIGEST = "f" * 64
A176 = {
    "status": "NO_GO_PENDING_EXPLICIT_REARM", "business_day_art": "2026-10-29",
    "receipt_sha256": DIGEST, "operation_id": "A176-40796450488-1",
    "a151_origin_sha256": "c" * 64,
    "a175_quiescence_sha256": "d" * 64,
    "a175_scheduler_sha256": "e" * 64,
    "paper_execution_authorized": False,
}


def report(status):
    checks = {key: {"state": "GREEN"} for key in deploy_gate.CHECKS}
    row = {
        "schema": deploy_gate.SCHEMA, "generated_at_ar": NOW.isoformat(),
        "status": status, "phase": "T_MINUS_10", "read_only": True,
        "network_order_test_performed": False, "dry_equivalent": True,
        "direct_telegram_send": False, "red_checks": [], "checks": checks,
        "paper_admission_hold": None,
    }
    if status == "PAUSED_NO_GO":
        row.update({
            "operator_paper_paused": True, "operator_pause_day_ar": "2026-10-30",
            "operator_pause_operation_id": "A180-NO-GO-" + DIGEST[:16],
            "operator_pause_receipt_sha256": DIGEST,
            "operator_no_go_continuation": dict(A176),
            "paper_admission_hold": "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM",
        })
        checks["required_rc6_timers"].update({
            key: row[key] for key in (
                "operator_paper_paused", "operator_pause_day_ar", "operator_pause_operation_id",
                "operator_pause_receipt_sha256", "operator_no_go_continuation")
        })
    else:
        row["operator_rearm_authority"] = {
            "candidate_sha": SHA, "candidate_tree_sha": TREE,
            "image_id": "sha256:" + "c" * 64,
            "operator_comment_id": 6500001234, "rearm_run_id": 4090001234,
        }
    return row


def accepted(row):
    return deploy_gate.validate_report(
        row, phase="T_MINUS_10", return_code=0,
        now=NOW, is_operational=lambda _: True)


def test_30_oct_non_rearmed_hold_is_valid_deploy_dry_probe():
    evidence = accepted(report("PAUSED_NO_GO"))
    assert evidence["preopen_status"] == "PAUSED_NO_GO"
    assert evidence["execution_authorized"] is False
    assert evidence["readiness_verified"] is False


def test_30_oct_rearmed_green_does_not_grant_deploy_execution():
    evidence = accepted(report("GREEN"))
    assert evidence["preopen_status"] == "GREEN"
    assert evidence["execution_authorized"] is False
    for mutation in ("missing", "wrong_sha", "fake_comment", "hold"):
        row = report("GREEN")
        if mutation == "missing":
            row.pop("operator_rearm_authority")
        elif mutation == "wrong_sha":
            row["operator_rearm_authority"]["candidate_sha"] = "short"
        elif mutation == "fake_comment":
            row["operator_rearm_authority"]["operator_comment_id"] = 0
        else:
            row["paper_admission_hold"] = "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM"
        with pytest.raises(deploy_gate.PreopenRejected):
            accepted(row)


def test_30_oct_forged_a176_new_day_or_green_is_rejected():
    row = report("PAUSED_NO_GO")
    row["operator_no_go_continuation"]["business_day_art"] = "2026-10-30"
    with pytest.raises(deploy_gate.PreopenRejected):
        accepted(row)


def test_30_oct_daily_timer_requires_live_disabled_hold_and_historical_anchor(monkeypatch):
    class Day30(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or TZ)

    seen = []
    monkeypatch.setattr(preopen, 'datetime', Day30)
    monkeypatch.setattr(preopen, 'evaluate_rearm', lambda **kwargs: (False, 'NO_GO', None))
    monkeypatch.setattr(suspend, 'exact_a176_operator_no_go_continuation',
                        lambda **kwargs: (seen.append(kwargs), dict(A176))[1])

    def systemctl(args, timeout=20):
        operation, unit = args[1:3]
        if operation == 'is-enabled':
            return (1, 'disabled', '') if unit == suspend.OPERATOR_PREOPEN_TIMER else (0, 'enabled', '')
        if operation == 'is-active':
            return (3, 'inactive', '') if unit == suspend.OPERATOR_PREOPEN_TIMER or unit.endswith('.service') else (0, 'active', '')
        if operation == 'show':
            return 0, 'porota-preopen-rc6.service', ''
        raise AssertionError(args)

    monkeypatch.setattr(preopen, 'cmd', systemctl)
    result = preopen.required_timers()
    assert result['state'] == 'GREEN'
    assert result['operator_paper_paused'] is True
    assert result['operator_pause_day_ar'] == '2026-10-30'
    assert result['operator_pause_operation_id'] == 'A180-NO-GO-' + DIGEST[:16]
    assert seen == [{'historical_anchor': True}]
    row = report("PAUSED_NO_GO")
    row["operator_pause_operation_id"] = A176["operation_id"]
    with pytest.raises(deploy_gate.PreopenRejected):
        accepted(row)


def test_30_oct_v2_gate_can_go_only_after_verified_rearm(tmp_path, monkeypatch):
    marker, v1, v2 = (tmp_path / name for name in ("marker.json", "v1.json", "v2.json"))
    marker.write_text(json.dumps({
        "candidate_sha": SHA, "validation_status": "VALIDATED_RUNTIME",
        "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
    }))
    original = '{"schema":"RC6_PAPER_PREOPEN_GATE_V1","status":"NO_GO"}\n'
    v1.write_text(original)
    monkeypatch.setattr(admission, "HOST_RECEIPT", v1)
    monkeypatch.setattr(admission, "evaluate_rearm", lambda **k: (True, "OPERATOR_REARM_AUTHORIZED", {}))
    result = {"status": "GREEN", "phase": "T_MINUS_10",
              "checks": {"paper_focus_contracts": {"state": "GREEN"}}}
    issued = admission.publish_preopen(result, now=NOW,
                                       state_path=marker, receipt_path=v2)
    assert issued["schema"] == "RC6_PAPER_PREOPEN_GATE_V2"
    assert issued["status"] == "GO_PAPER_SCALPING"
    assert v1.read_text() == original
    assert admission.admission_status(now=NOW, candidate_sha=SHA,
                                      receipt_path=v2) == (True, "")
    # A past v1 GO cannot be replayed as v2; neither can a fake T-10 at 10:31.
    v2.write_text(json.dumps(dict(issued, schema="RC6_PAPER_PREOPEN_GATE_V1")))
    assert admission.admission_status(now=NOW, candidate_sha=SHA, receipt_path=v2)[0] is False
    later = NOW.replace(minute=31)
    blocked = admission.publish_preopen(result, now=later,
                                        state_path=marker, receipt_path=v2)
    assert blocked["status"] == "NO_GO_PAPER_SCALPING"
