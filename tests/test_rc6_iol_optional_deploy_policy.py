"""Optional IOL freshness is an alert; PAPER admission remains independently held."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import rc6_preopen as preopen
import rc6_paper_admission_gate as admission
from scripts import rc6_deploy_preopen_gate as deploy_gate


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
NOW = datetime(2026, 10, 29, 12, 30, tzinfo=TZ)


def test_safe_iol_unavailable_allows_dry_deploy_preopen_but_never_paper_go(
        tmp_path, monkeypatch, capsys):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or TZ)

    cache = tmp_path / "iol_family_reference_latest.json"
    cache.write_text(json.dumps({
        "cache_state": "LIVE_FRESH", "last_known_good_at": None,
        "refreshed_at": (NOW - timedelta(hours=14)).isoformat(),
        "section_states": {"caucion:ARS": "LIVE_FRESH"},
        "cauciones": {"ARS": [{"term_days": 1}]},
        "fallback_order": preopen.IOL_FALLBACK_ORDER,
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
        "real_money_authorized": False, "decision_effect": "OBSERVE_ONLY",
    }))
    monkeypatch.setattr(preopen, "IOL_FAMILY_REFERENCE", cache)
    monkeypatch.setattr(preopen, "datetime", Clock)
    monkeypatch.setattr(preopen.byma, "es_dia_habil_operativo", lambda _day: True)
    monkeypatch.setattr(preopen.shutil, "disk_usage", lambda _path:
                        SimpleNamespace(free=preopen.TARGET_FREE_BYTES + 1))
    green = lambda *args, **kwargs: {"state": "GREEN"}
    for name in ("release_identity", "observer_db", "required_timers",
                 "byma_morning_watch", "blocked_history_timers", "foreign_market_policy"):
        monkeypatch.setattr(preopen, name, green)
    monkeypatch.setattr(preopen, "container", green)
    assert preopen.main("T_MINUS_10", "a" * 40) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "GREEN" and result["red_checks"] == []
    assert result["checks"]["iol_reference_fallback"]["state"] == "AMBER"
    assert result["checks"]["iol_reference_fallback"]["effective_cache_state"] == \
        "SOURCE_UNAVAILABLE"
    assert result["paper_admission_hold"] == "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM"
    accepted = deploy_gate.validate_report(result, phase="T_MINUS_10", return_code=0,
                                           now=NOW, is_operational=lambda _day: True)
    assert accepted["accepted"] is True and accepted["execution_authorized"] is False
    assert accepted["paper_admission_hold"] == "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM"
    del result["paper_admission_hold"]
    try:
        deploy_gate.validate_report(result, phase="T_MINUS_10", return_code=0,
                                    now=NOW, is_operational=lambda _day: True)
    except deploy_gate.PreopenRejected as exc:
        assert str(exc) == "PAPER_REARM_HOLD_UNPROVEN"
    else:
        raise AssertionError("A future preopen cannot conceal the persistent NO_GO")
    assert admission.admission_status(now=NOW, candidate_sha="a" * 40)[0] is False


def test_wrong_fallback_or_real_money_flag_still_blocks_preopen(
        tmp_path, monkeypatch):
    cache = tmp_path / "iol_family_reference_latest.json"
    base = {"cache_state": "SOURCE_UNAVAILABLE", "section_states": {"global": "SOURCE_UNAVAILABLE_NO_LKG"},
            "fallback_order": preopen.IOL_FALLBACK_ORDER,
            "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
            "real_money_authorized": False, "decision_effect": "OBSERVE_ONLY"}
    monkeypatch.setattr(preopen, "IOL_FAMILY_REFERENCE", cache)
    for change in ({"real_money_authorized": True},
                   {"fallback_order": ["IOL_LAST_KNOWN_GOOD"]},
                   {"continuation_state": "ZERO_FILL"}):
        cache.write_text(json.dumps({**base, **change}))
        assert preopen.iol_reference_state(NOW.date())["state"] == "RED"
