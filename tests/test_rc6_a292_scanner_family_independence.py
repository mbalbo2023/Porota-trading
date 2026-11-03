"""PAPER sampling cannot borrow an unrelated Spot focus as family authority."""
from datetime import datetime
from math import floor

from bf_production_paper_observer import _scanner_opening_capacity
from rc6_performance.scanner import sampling_plan


NOW = datetime.fromisoformat("2026-10-30T11:30:00-03:00")
SPECIALIZED = tuple(
    ("TEST" + family, family, "A-24HS") for family in
    ("BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"))
BONO, *OTHER_SPECIALIZED = SPECIALIZED
SPOT_WITHOUT_PIT = ("GGAL", "ACCIONES", "A-24HS")


def plan(catalog, opened=(), *, limit=4, cycle_seconds=60, window_seconds=5400):
    selected, _, _, active = sampling_plan(
        catalog, opened, (), limit=limit, cycle_seconds=cycle_seconds,
        required_samples=6, window_seconds=window_seconds, now=NOW)
    return selected, active


def capacity(identity, active):
    return _scanner_opening_capacity(
        identity, focus={"matched": []},
        feasibility={"feasible": active["feasible"], "active_plan": active})


def test_unrelated_spot_pit_absence_cannot_veto_budgeted_family():
    selected, active = plan((*SPECIALIZED, SPOT_WITHOUT_PIT), limit=12)
    # Independent arithmetic oracle: the basket has eight slots and 90
    # samples per 90-minute window, well above the required six.
    assert len(selected) == 8
    assert floor(5400 / 60) >= 6
    assert active["active_budget"]["status"] == "FEASIBLE"
    assert active["focus_feasible"] is False
    assert active["feasible"] is True
    for identity in SPECIALIZED:
        # This grants only a signal sampling budget, never an entry.
        assert capacity(identity, active) == (True, "")
    # Scanner capacity is not entry authority: Spot still needs its exact
    # point-in-time identity and gets no permission from the budget alone.
    assert capacity(SPOT_WITHOUT_PIT, active) == (
        False, "PAPER_SPOT_EXACT_PIT_NOT_READY")


def test_no_warm_slot_or_signal_budget_still_fails_closed():
    selected, active = plan((BONO, OTHER_SPECIALIZED[0]), (BONO,), limit=1)
    assert selected == (BONO,)
    assert active["warm"] == []
    assert active["feasible"] is False
    assert capacity(OTHER_SPECIALIZED[0], active) == (False, "SCANNER_INFEASIBLE")

    selected, active = plan((BONO, OTHER_SPECIALIZED[0]), cycle_seconds=1000,
                            window_seconds=3000)
    assert selected == ()
    assert active["warm"] == []
    assert active["feasible"] is False
    assert capacity(BONO, active) == (False, "SCANNER_INFEASIBLE")
