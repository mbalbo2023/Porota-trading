"""A catalog generation is incomplete until discovery and policy finish."""
from datetime import datetime, timedelta, timezone

import pytest

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import cr_contract_evidence_v2_mass_hf6 as mass
from be_paper_engine import PaperStore
from scripts import rc6_zero_known_error_runtime_audit as audit
from tests.test_ws_integ_03_runtime_audit import _snapshot


def prepared(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    with store.connect() as connection:
        connection.execute("CREATE TABLE candidate_refresh_state(id INTEGER PRIMARY KEY, state TEXT, checked_at TEXT)")
        connection.execute("INSERT INTO candidate_refresh_state VALUES(1,'COMPLETE',?)",
                           (datetime.now(timezone.utc).isoformat(),))
    monkeypatch.setattr(observer, "_candidate_universe", lambda: [
        ("FIXTURE", "ACCIONES", "A-24HS", "BYMA", False, "ACCIONES")])
    monkeypatch.setattr(observer, "CATALOG_QUERY_SLEEP_SECONDS", 0)
    monkeypatch.setattr(observer, "complementary_discovery", lambda *_args: [])
    return store


def state(store):
    with store.connect() as connection:
        return connection.execute("SELECT state FROM candidate_refresh_state WHERE id=1").fetchone()[0]


@pytest.mark.parametrize("phase", ("provider", "policy"))
def test_old_complete_marker_is_not_visible_during_catalog_generation(tmp_path, monkeypatch, phase):
    store = prepared(tmp_path, monkeypatch)
    seen = {}
    class Reader:
        def search_instruments(self, *_args, **_kwargs):
            seen["provider"] = state(store)
            return []
    def policy(*_args, **_kwargs):
        seen["policy"] = state(store)
        return {"catalog_available": 0, "real_routes_used": 0}
    monkeypatch.setattr(mass, "collect", policy)
    assert observer._download_catalog(Reader(), store) == 0
    assert seen[phase] == "RUNNING"
    assert state(store) == "COMPLETE"


def test_complement_revalidation_is_running_before_final_projection(tmp_path, monkeypatch):
    store = prepared(tmp_path, monkeypatch)
    original = catalog.sync_candidate_universe
    seen = []
    def project(connection, checked_at, **kwargs):
        seen.append(state(store))
        return original(connection, checked_at, **kwargs)
    monkeypatch.setattr(catalog, "sync_candidate_universe", project)
    observer._reconcile_complementary_catalog(store)
    assert seen == ["RUNNING"]
    assert state(store) == "COMPLETE"


def test_policy_exception_leaves_failed_generation_not_previous_complete(tmp_path, monkeypatch):
    store = prepared(tmp_path, monkeypatch)
    class Reader:
        def search_instruments(self, *_args, **_kwargs):
            return []
    def fail(*_args, **_kwargs):
        raise RuntimeError("POLICY_FIXTURE_FAILURE")
    monkeypatch.setattr(mass, "collect", fail)
    with pytest.raises(RuntimeError, match="POLICY_FIXTURE_FAILURE"):
        observer._download_catalog(Reader(), store)
    assert state(store) == "FAILED"


@pytest.mark.parametrize("value,age,error", (
    ("RUNNING", 0, RuntimeError), ("RUNNING", 301, AssertionError),
    ("FAILED", 0, AssertionError),
))
def test_incomplete_generation_cannot_be_certified_even_above_all_ready_floors(monkeypatch, value, age, error):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    snapshot = _snapshot()
    snapshot["database"]["readiness"]["refresh"] = {
        "state": value, "checked_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()}
    with pytest.raises(error, match="CANDIDATE_REFRESH"):
        audit._assert_snapshot(snapshot)


def test_first_runtime_baseline_is_taken_after_generation_completes(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    pending = _snapshot()
    pending["database"]["readiness"]["refresh"] = {
        "state": "RUNNING", "checked_at": datetime.now(timezone.utc).isoformat()}
    complete = _snapshot()
    complete["database"]["readiness"]["refresh"] = {
        "state": "COMPLETE", "checked_at": datetime.now(timezone.utc).isoformat()}
    snapshots = iter((pending, pending, complete))
    monkeypatch.setattr(audit, "collect", lambda _label: next(snapshots))
    monkeypatch.setattr(audit.time, "sleep", lambda _seconds: None)
    assert audit.consistent_snapshot("immediate", attempts=3) is complete


def test_completed_generation_still_cannot_lower_a_family_floor(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    snapshot = _snapshot()
    snapshot["database"]["readiness"]["refresh"] = {
        "state": "COMPLETE", "checked_at": datetime.now(timezone.utc).isoformat()}
    snapshot["database"]["readiness"]["by_family"]["FCI"] -= 1
    with pytest.raises(AssertionError):
        audit._assert_snapshot(snapshot)
