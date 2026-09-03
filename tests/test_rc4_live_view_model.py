from __future__ import annotations

from decimal import Decimal

import rc4_live_view_model as live


NOW="2026-09-03T16:30:00+00:00"


def _position(paper_id="P1", *, status="OPEN", currency="ARS", style="STANDARD_PAPER",
              entry="100", qty="10", entry_cost="10", close_reason=None,
              net_pnl=None, closed_at=None):
    return {
        "paper_id":paper_id,"symbol":"TEST","asset_class":"ACCIONES",
        "currency":currency,"market":"BYMA","settlement":"A-24HS",
        "status":status,"quantity":qty,"entry_price":entry,"entry_cost":entry_cost,
        "stop_price":"95","target_price":"110",
        "opened_at":"2026-09-03T15:00:00+00:00","closed_at":closed_at,
        "net_pnl":net_pnl,"close_reason":close_reason,"max_favorable":"8",
        "max_adverse":"-4",
        "features_json":'{"execution_style":"%s","contract_cash_multiplier":"1",'
                        '"risk_budget":"15000","concurrent_risk_locked":{"admitted":true}}' % style,
    }


def test_positive_and_negative_pnl_include_modeled_exit_cost():
    p=_position()
    gain=live.position_pnl(p,{"mark_price":"103"})
    loss=live.position_pnl(p,{"mark_price":"99"})
    assert gain["state"] == "POSITIVE"
    assert loss["state"] == "NEGATIVE"
    # Entry notional 1000, entry cost 10. At mark 103 exit cost is 10.3.
    assert gain["value"] == Decimal("9.7")
    assert loss["value"] == Decimal("-29.9")


def test_stale_open_mark_never_looks_current_even_if_pnl_can_be_calculated():
    item=live.operation_item(
        _position(),
        mark={"paper_id":"P1","mark_price":"105",
              "book_at":"2026-09-03T16:00:00+00:00",
              "marked_at":"2026-09-03T16:00:02+00:00"},
        now=NOW,
    )
    assert item["mark_freshness"] == "STALE"
    assert item["pnl"] is not None
    assert item["pnl_state"] == "STALE"


def test_fresh_mark_exposes_exact_book_and_persist_timestamp():
    item=live.operation_item(
        _position(),
        mark={"paper_id":"P1","mark_price":"101",
              "book_at":"2026-09-03T16:29:30+00:00",
              "marked_at":"2026-09-03T16:29:32+00:00"},
        now=NOW,
    )
    assert item["mark_freshness"] == "FRESH"
    assert item["mark_at"] == "2026-09-03T16:29:30+00:00"
    assert item["marked_at"] == "2026-09-03T16:29:32+00:00"
    assert item["pnl_state"] in {"POSITIVE","NEGATIVE","NEUTRAL"}


def test_closed_operation_has_evidence_bounded_lesson():
    p=_position(status="CLOSED",net_pnl="-40",close_reason="EOD_PAPER",
                closed_at="2026-09-03T19:45:00+00:00")
    lesson=live.deterministic_lesson(
        p,{"paper_id":"P1","outcome":"LOSS","net_return_pct":"-0.04",
           "duration_minutes":285})
    assert lesson["state"] == "AVAILABLE"
    assert lesson["code"] == "EOD_PAPER"
    assert "fin de rueda" in lesson["text"]
    assert "replay" in lesson["text"]
    assert lesson["duration_minutes"] == 285


def test_open_operation_does_not_get_fake_final_lesson():
    lesson=live.deterministic_lesson(_position())
    assert lesson["state"] == "NOT_APPLICABLE_OPEN"


def test_gate_rationale_and_dynamic_risk_are_in_drilldown_payload():
    p=_position()
    item=live.operation_item(
        p,
        mark={"paper_id":"P1","mark_price":"101","book_at":"2026-09-03T16:29:50+00:00"},
        gate={"paper_id":"P1","technical_gate":"APPROVE","ai_gate":"OFF",
              "patrimonial_gate":"APPROVE","final_result":"OPENED_SIMULATED",
              "reason":"Todos los portones aprobaron","evaluated_at":"2026-09-03T15:00:00+00:00"},
        now=NOW,
    )
    assert item["gate"]["final_result"] == "OPENED_SIMULATED"
    assert item["gate"]["reason"] == "Todos los portones aprobaron"
    assert item["risk"]["concurrent_locked"]["admitted"] is True


def test_live_section_order_and_funnel_removal_are_contractual():
    model=live.build_live_model(positions=[])
    assert model["section_order"] == [
        "open_operations","closed_operations","decisions","scalping","workers"]
    assert model["rejection_funnel_enabled"] is False


def test_pnl_is_aggregated_only_within_each_currency():
    positions=[
        _position("ARS",currency="ARS",entry="100",qty="10",entry_cost="0"),
        _position("USD",currency="USD_MEP",entry="10",qty="2",entry_cost="0"),
    ]
    marks=[
        {"paper_id":"ARS","mark_price":"101","book_at":"2026-09-03T16:29:55+00:00"},
        {"paper_id":"USD","mark_price":"11","book_at":"2026-09-03T16:29:55+00:00"},
    ]
    model=live.build_live_model(positions=positions,marks=marks,now=NOW)
    assert model["open_pnl_by_currency"] == {"ARS":Decimal("10"),"USD_MEP":Decimal("2")}


def test_scalping_is_separate_and_visible_in_live_model():
    p=_position("S1",style="SCALPING_PAPER")
    model=live.build_live_model(
        positions=[p],
        marks=[{"paper_id":"S1","mark_price":"100","book_at":"2026-09-03T16:29:59+00:00"}],
        scalping_candidates=[{"action":"BUY_CANDIDATE"},{"action":"HOLD"}],
        now=NOW,
    )
    assert model["scalping"]["evaluated"] == 2
    assert model["scalping"]["buy_candidates"] == 1
    assert model["scalping"]["open_positions"] == 1
