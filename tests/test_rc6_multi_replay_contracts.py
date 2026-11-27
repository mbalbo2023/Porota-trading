"""Synthetic per-instrument PAPER replays for futures, repos and fund classes."""

from datetime import datetime, timezone

import pytest

from iol_rest_readonly_rc6 import IOLRestError
import rc6_paper_admission_gate as admission
from tests import test_rc6_ppi_gateway_family_ready as native
from tests.test_rc6_fund_attested_settlement import (
    OPEN as FUND_OPEN,
    make_paper_fund,
    replay_attested_fund_settlement,
)
from tests.test_rc6_iol_migration_offhost import reader as iol_reader
from tests.test_rc6_ppi_gateway_family_ready import gateway_socket  # pytest fixture
from tests.test_ws_caucion_12_live_ppi_wiring import (
    reconcile_synthetic_caucion,
    replay_caucion_sweep,
)


def _assert_iol_market_hold(tmp_path, identity):
    market, transport = iol_reader(tmp_path, identity)
    with pytest.raises(IOLRestError, match="^IOL_MARKET_IDENTITY_UNSUPPORTED$"):
        market.observe(identity)
    assert transport.calls == []


@pytest.mark.parametrize(("symbol", "session_day"), [
    pytest.param("DLR/OCT26", "2026-10-23", id="FUTUROS:DLR/OCT26"),
    pytest.param("DLR/NOV26", "2026-11-24", id="FUTUROS:DLR/NOV26"),
    pytest.param("DLR/DIC26", "2026-11-24", id="FUTUROS:DLR/DIC26"),
])
def test_futures_gateway_trade_book_go_open_and_close(tmp_path, monkeypatch, gateway_socket,
                                                      symbol, session_day):
    identity = (symbol, "FUTUROS", "A3", "ARS", "INMEDIATA")
    _assert_iol_market_hold(tmp_path, identity)
    replay_at = datetime.fromisoformat(session_day + "T14:30:00+00:00")
    monkeypatch.setattr(native, "AT", replay_at)
    monkeypatch.setattr(native.ReplayClock, "value", replay_at)
    store = native.replay_native_family(
        tmp_path, monkeypatch, gateway_socket, symbol, "FUTUROS", "A3", "INMEDIATA", 0, 0, False
    )
    with store.connect() as db:
        assert [row[0] for row in db.execute(
            "SELECT DISTINCT lifecycle.instrument FROM paper_family_lifecycle_events events "
            "JOIN paper_family_lifecycle lifecycle USING (lifecycle_id) "
            "WHERE events.family='FUTUROS'"
        )] == [symbol]


@pytest.mark.parametrize(("symbol", "term_days"), [
    pytest.param("PESOS1", 1, id="CAUCIONES:PESOS1"),
    pytest.param("PESOS2", 2, id="CAUCIONES:PESOS2"),
    pytest.param("PESOS7", 7, id="CAUCIONES:PESOS7"),
])
def test_caucion_fresh_placing_book_go_open_and_maturity(tmp_path, monkeypatch, symbol, term_days):
    _assert_iol_market_hold(tmp_path, (symbol, "CAUCIONES", "BYMA", "ARS", "INMEDIATA"))
    store, at, offers = reconcile_synthetic_caucion(
        tmp_path, monkeypatch, symbol, term_days, "CURRENT_STATIC", None)
    monkeypatch.setattr(native.ReplayClock, "value", native.AT)
    native._preopen(tmp_path, monkeypatch)
    native.ReplayClock.value = at.astimezone(timezone.utc)
    assert admission.admission_status() == (True, "")
    replay_caucion_sweep(store, offers, at, liquidity_deadline_override=term_days > 1)


@pytest.mark.parametrize(("symbol", "class_id"), [
    pytest.param("FCI.FIXTURE.A", "900002", id="FCI:FCI.FIXTURE.A"),
    pytest.param("FCI.FIXTURE.B", "900001", id="FCI:FCI.FIXTURE.B"),
    pytest.param("FCI.FIXTURE.C", "900003", id="FCI:FCI.FIXTURE.C"),
])
def test_fund_class_go_subscription_nav_and_signed_redemption(tmp_path, monkeypatch, symbol, class_id):
    _assert_iol_market_hold(tmp_path, (symbol, "FCI", "BYMA", "ARS", "INMEDIATA"))
    # The offline allocator refuses to write to its configured runtime path.
    # The synthetic fund book stays separate from that path during subscription.
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "unused-runtime.db"))
    monkeypatch.setattr(native.ReplayClock, "value", native.AT)
    native._preopen(tmp_path, monkeypatch)
    native.ReplayClock.value = datetime.fromisoformat(FUND_OPEN).astimezone(timezone.utc)

    def require_modeled_go(_connection):
        allowed, reason = admission.admission_status()
        assert allowed, reason
        return reason

    fund = make_paper_fund(tmp_path, monkeypatch, symbol, class_id,
                          admission_guard=require_modeled_go)
    replay_attested_fund_settlement(fund, monkeypatch)
