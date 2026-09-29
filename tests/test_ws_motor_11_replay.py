import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "ws_motor_11_replay.py"
SPEC = importlib.util.spec_from_file_location("ws_motor_11_replay", SCRIPT)
replay_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay_module)


def test_controlled_replay_is_per_instrument_and_never_mutates_source():
    result = replay_module.replay()
    assert result["original_snapshot_unchanged"] is True
    assert result["productive_db_mutations"] == 0
    assert result["real_orders_sent"] == 0
    assert result["real_order_routes_used"] == []
    assert len(result["instruments"]) == result["before"]["total_catalog"]
    assert all(row["ticker"] and row["after"] for row in result["instruments"])


def test_replay_never_extrapolates_between_witnesses():
    result = replay_module.replay()
    matrix = {(r["ticker"], r["field"]): r for r in result["field_matrix"]}
    assert matrix[("GD30", "quantity_step")]["status_after"] == "EVIDENCED"
    assert matrix[("AL30", "quantity_step")]["status_after"] == "UNRESOLVED"
    assert matrix[("DLR/DIC26", "cash_multiplier")]["value"] == 1000
    assert matrix[("DLR/ENE27", "cash_multiplier")]["value"] is None


def test_executor_axis_is_independent_from_data_axis():
    result = replay_module.replay()
    rows = {row["ticker"]: row for row in result["instruments"]}
    assert "BLOCKED_DATA" in rows["IOLCAMA"]["blocker_axes"]
    assert "BLOCKED_EXECUTOR" in rows["IOLCAMA"]["blocker_axes"]
    assert "BLOCKED_EXECUTOR" in rows["DLR/DIC26"]["blocker_axes"]
    assert "BLOCKED_EXECUTOR" not in rows["GD30"]["blocker_axes"]


def test_evidence_candidates_cross_the_real_catalog_gate():
    result = replay_module.replay()
    rows = {row["ticker"]: row for row in result["instruments"]}
    assert rows["GD30"]["after_evidence"] == "READY_PAPER_CANDIDATE"
    assert rows["GD30"]["after"] == "READY_PAPER_SPOT"
    assert rows["GFGC6000OC"]["after_evidence"] == "READY_PAPER_CANDIDATE"
    assert rows["GFGC6000OC"]["after"] == "READY_PAPER_OPTION_LONG"
    assert result["after"]["ready_by_instrument"] == 2


def test_matrix_has_one_row_per_required_field_and_provenance_columns():
    result = replay_module.replay()
    gd30 = [row for row in result["field_matrix"] if row["ticker"] == "GD30"]
    required = (replay_module.rules.FAMILY_CONTRACT_FIELDS["BONOS"] |
                replay_module.rules.FAMILY_DYNAMIC_FIELDS["BONOS"] |
                replay_module.rules.EVENT_CONDITIONAL_FIELDS["BONOS"])
    assert {row["field"] for row in gd30} == required
    assert all("provider_timestamp" in row and "capture_timestamp" in row for row in gd30)
