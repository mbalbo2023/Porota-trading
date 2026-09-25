from datetime import datetime, timezone
from pathlib import Path

import rc4_contract_schedule as schedule
from _version import VERSION, IMAGE, REAL_ORDER_CAPABILITY


ROOT = Path(__file__).resolve().parents[1]


def _utc(y,m,d,h,minute):
    return datetime(y,m,d,h,minute,tzinfo=timezone.utc)


def test_hf2_version_and_orders_remain_blocked():
    # Legacy RC4-HF2 behavioral coverage is retained under the current RC6 release identity.
    assert VERSION == "17.0.0-rc6"
    assert IMAGE == "porota-trading-bot:17.0.0-rc6"
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


def test_live_page_keeps_current_round_and_history_semantics_clean():
    src=(ROOT/"bg_paper_dashboard.py").read_text(encoding="utf-8")
    # RC6 /vivo is the current motor view: every OPEN position remains visible,
    # while closed/historical positions are included only when opened/closed today.
    assert 'all_positions=spot["open"]+spot["closed"]' in src
    assert 'p.get("status")=="OPEN" or _is_today(p.get("opened_at")) or _is_today(p.get("closed_at"))' in src
    assert 'previous=max(0,len(all_positions)-len(positions))' in src
    assert "operaciones anteriores no se mezclan con la rueda actual" in src
    assert "Sin operaciones simuladas del día." in src
    assert "Todas las operaciones de esta página son simuladas." in src
    assert "Nunca representan una orden enviada a PPI." in src
    assert "Fuente que habilita una nueva etiqueta" in src
    assert "Aprendizaje event-driven" in src
    assert '"EVENT-DRIVEN"' in src


def test_formalized_browser_runner_keeps_fail_closed_guards():
    src=(ROOT/"scripts/porota_contract_evidence_session_runner_rc4.py").read_text(encoding="utf-8")
    assert ".porota-secrets" in src
    assert "BLOCKED_AUTH_2FA_REQUIRED" in src
    assert "route.abort()" in src
    assert '"real_orders_sent":0' in src or '"real_orders_sent": 0' in src
    assert "PPI_WEB_PASSWORD=" not in src
    assert "PPI_WEB_USERNAME=" not in src
