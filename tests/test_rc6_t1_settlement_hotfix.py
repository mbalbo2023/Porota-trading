import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from be_paper_engine import D, PaperBroker, PaperStore, Quote
from bt_caucion_paper import pending_proceeds
from cf_sale_settlement import (
    conservative_unconfirmed_availability,
    modeled_sale_settlement,
    modeled_sale_settlement_date,
    t1_full_date_release_enabled,
    validated_sale_settlement,
)


def quote(at, *, price="100", settlement="A-24HS", bid_size="1000", ask_size="1000"):
    price = D(price)
    return Quote(
        symbol="GGAL",
        asset_class="ACCIONES",
        settlement=settlement,
        last=price,
        bid=price - D("0.10"),
        ask=price + D("0.10"),
        bid_size=D(bid_size),
        ask_size=D(ask_size),
        observed_at=at,
        currency="ARS",
        market="BYMA",
        metadata_source="TEST_FIXTURE",
        book_at=at,
        trade_at=at,
        last_kind="TRADE",
    )


def closed_t1_broker(tmp_path, opened_at, closed_at):
    broker = PaperBroker(
        PaperStore(str(tmp_path / (opened_at[:10] + ".db"))),
        initial_cash="10000",
        daily_loss_pct="100",
    )
    opening = quote(opened_at, price="100")
    opened, reason, _ = broker._open(opening, D("0.8"), {})
    assert opened, reason
    position = broker.store.open_positions()[0]
    closing = quote(closed_at, price="110", bid_size="10000", ask_size="10000")
    assert broker._close(position, closing, "TEST_T1")
    with broker.store.connect() as c:
        receipt = dict(c.execute("SELECT * FROM paper_sale_receivables").fetchone())
    assert receipt["available_at"] is None
    assert receipt["basis"] == "PENDING_CONFIRMATION"
    return broker, receipt


def test_t1_policy_is_fail_closed_by_default_and_ambiguous(monkeypatch):
    monkeypatch.delenv("PAPER_T1_FULL_DATE_RELEASE", raising=False)
    assert t1_full_date_release_enabled() is False
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "garbage")
    assert t1_full_date_release_enabled() is False
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    assert t1_full_date_release_enabled() is True
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "false")
    assert t1_full_date_release_enabled() is False


def test_t1_date_and_boundary_do_not_invent_broker_hour(monkeypatch):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    traded = "2026-09-03T16:10:56+00:00"  # jueves 13:10 AR
    assert modeled_sale_settlement_date("A-24HS", traded) == "2026-09-04"
    assert modeled_sale_settlement("A-24HS", traded) is None
    boundary = conservative_unconfirmed_availability("A-24HS", traded)
    assert boundary.isoformat() == "2026-09-05T00:00:00-03:00"
    assert validated_sale_settlement("A-24HS", traded, None, "PENDING_CONFIRMATION") == boundary


def test_t1_weekend_and_byma_holiday_are_respected(monkeypatch):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    # Viernes 04/09 -> T+1 BYMA lunes 07/09; se libera recién martes 08/09 00:00 AR.
    friday = "2026-09-04T16:00:00+00:00"
    assert modeled_sale_settlement_date("T+1", friday) == "2026-09-07"
    assert conservative_unconfirmed_availability("T+1", friday).isoformat() == "2026-09-08T00:00:00-03:00"
    # Viernes 09/10 -> lunes 12/10 es no operativo BYMA; T+1 pasa al martes 13/10.
    before_holiday = "2026-10-09T16:00:00+00:00"
    assert modeled_sale_settlement_date("A-24HS", before_holiday) == "2026-10-13"
    assert conservative_unconfirmed_availability("A-24HS", before_holiday).isoformat() == "2026-10-14T00:00:00-03:00"


def test_pending_receivable_releases_only_after_full_t1_date(tmp_path, monkeypatch):
    broker, receipt = closed_t1_broker(
        tmp_path,
        "2026-09-03T15:00:00+00:00",
        "2026-09-03T16:00:00+00:00",
    )
    amount = D(receipt["net_proceeds"])

    # Kill-switch/default histórico: nada se libera sin autoridad explícita.
    monkeypatch.delenv("PAPER_T1_FULL_DATE_RELEASE", raising=False)
    assert pending_proceeds(broker.store, "2026-09-07T13:30:00+00:00") == amount

    # Hotfix PAPER: la venta del jueves ya superó íntegramente su viernes T+1.
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    assert pending_proceeds(broker.store, "2026-09-04T23:59:59-03:00") == amount
    assert pending_proceeds(broker.store, "2026-09-05T00:00:00-03:00") == 0
    closed = broker.store.recent_closed()[0]
    assert broker._cash(as_of="2026-09-07T13:30:00+00:00") == D("10000") + D(closed["net_pnl"])


def test_sale_on_friday_stays_pending_through_monday_settlement_day(tmp_path, monkeypatch):
    broker, receipt = closed_t1_broker(
        tmp_path,
        "2026-09-04T15:00:00+00:00",
        "2026-09-04T16:00:00+00:00",
    )
    amount = D(receipt["net_proceeds"])
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    # Labor Day de EE.UU. no cierra BYMA: lunes 07/09 es T+1 local, pero no liberamos intradía.
    assert pending_proceeds(broker.store, "2026-09-07T20:59:59+00:00") == amount
    assert pending_proceeds(broker.store, "2026-09-08T02:59:59+00:00") == amount
    assert pending_proceeds(broker.store, "2026-09-08T03:00:00+00:00") == 0


def test_pending_confirmation_never_accepts_fabricated_available_at(monkeypatch):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    with pytest.raises(ValueError, match="acreditación no confirmada"):
        validated_sale_settlement(
            "A-24HS",
            "2026-09-03T16:00:00+00:00",
            "2026-09-04T12:00:00-03:00",
            "PENDING_CONFIRMATION",
        )


def test_unknown_settlement_remains_blocked(monkeypatch):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    traded = "2026-09-03T16:00:00+00:00"
    assert modeled_sale_settlement_date("PLAZO-DESCONOCIDO", traded) is None
    assert modeled_sale_settlement("PLAZO-DESCONOCIDO", traded) is None
    assert validated_sale_settlement("PLAZO-DESCONOCIDO", traded, None, "PENDING_CONFIRMATION") is None


def test_ci_is_unchanged_by_hotfix(monkeypatch):
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    traded = "2026-09-03T16:00:00+00:00"
    assert modeled_sale_settlement("CI", traded) == "2026-09-03T13:00:00-03:00"


def test_observer_runtime_explicitly_enables_t1_policy(tmp_path, monkeypatch):
    import porota_mode_manager as manager

    monkeypatch.setattr(manager, "DATA", tmp_path)
    (tmp_path / "diagnosticos").mkdir()
    path = manager.observer_runtime_env()
    text = path.read_text(encoding="utf-8")
    assert "PAPER_T1_FULL_DATE_RELEASE=true\n" in text
    assert path.stat().st_mode & 0o777 == 0o600
