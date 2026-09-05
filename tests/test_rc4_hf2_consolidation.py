from datetime import datetime, timezone
from pathlib import Path

import rc4_contract_schedule as schedule
from _version import VERSION, IMAGE, REAL_ORDER_CAPABILITY


ROOT = Path(__file__).resolve().parents[1]


def _utc(y,m,d,h,minute):
    return datetime(y,m,d,h,minute,tzinfo=timezone.utc)


def test_hf2_version_and_orders_remain_blocked():
    assert VERSION == "17.0.0-rc4-hf2"
    assert IMAGE == "porota-trading-bot:17.0.0-rc4-hf2"
    assert REAL_ORDER_CAPABILITY == "BLOCKED"


def test_contract_evidence_weekend_is_completely_off():
    saturday=_utc(2026,9,5,15,0)
    for job in schedule.JOB_TO_CADENCE:
        assert schedule.window_allows(job,saturday) is False


def test_contract_windows_keep_preopen_market_and_weekday_static():
    assert schedule.window_allows("CONTRACT_EVIDENCE_DYNAMIC",_utc(2026,9,4,13,40)) is True
    assert schedule.window_allows("CONTRACT_EVIDENCE_CAUCIONES",_utc(2026,9,4,19,59)) is True
    assert schedule.window_allows("CONTRACT_EVIDENCE_DYNAMIC",_utc(2026,9,4,20,0)) is False
    assert schedule.window_allows("CONTRACT_EVIDENCE_STATIC",_utc(2026,9,4,20,5)) is True


def test_live_page_is_today_only_for_closed_trades_and_keeps_pending_semantics_clean():
    src=(ROOT/"bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "closed_today=[]" in src
    assert "closed_at.date()==now.date()" in src
    assert "2. Operaciones cerradas hoy" in src
    assert "Sin operaciones cerradas hoy." in src
    assert "active_pending=[r for r in rows_diag" in src
    assert "AVAILABLE_AFTER_FULL_SETTLEMENT_DATE" in src
    assert "ya liberadas/no listadas" in src
    assert "Fuente primaria: paper_decisions" in src
    assert "Aprendizaje EVENT-DRIVEN" in src


def test_formalized_browser_runner_keeps_fail_closed_guards():
    src=(ROOT/"scripts/porota_contract_evidence_session_runner_rc4.py").read_text(encoding="utf-8")
    assert ".porota-secrets" in src
    assert "BLOCKED_AUTH_2FA_REQUIRED" in src
    assert "route.abort()" in src
    assert '"real_orders_sent":0' in src or '"real_orders_sent": 0' in src
    assert "PPI_WEB_PASSWORD=" not in src
    assert "PPI_WEB_USERNAME=" not in src
