"""Catalog publication and scanner recovery preserve gateway custody."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import bv_paper_runtime as runtime
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_ppi_gateway_reader_adapter as gateway
from be_paper_engine import PaperStore
from rc6_ppi_shared_feed import SharedFeedUnavailable
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


@pytest.mark.parametrize(("rotation", "prior_state"), [
    ("configuration", None),
    ("configuration", "FAILED"),
    ("search", "COMPLETE"),
])
def test_owner_change_retries_without_publishing_partial_catalog(
        tmp_path, monkeypatch, rotation, prior_state):
    store = prepared(tmp_path, monkeypatch)
    now = datetime(2026, 11, 26, 0, 30, tzinfo=observer.TZ)
    old = (now - timedelta(days=1)).isoformat()
    monkeypatch.setattr(observer, "now_iso", now.isoformat)
    source = "PPI_PRODUCTION_CATALOG"
    reason = "PPI_GATEWAY_OWNER_CHANGED"
    old_config = {"instrument_types": ["ACCIONES"], "markets": ["BYMA"], "settlements": [],
                  "quantity_types": [], "operation_terms": [], "operation_types": [], "operations": []}
    record = {"type": "ACCIONES", "currency": "ARS", "market": "BYMA", "settlement": "A-24HS"}
    with store.connect() as connection:
        if prior_state is None:
            connection.execute("DELETE FROM candidate_refresh_state")
        else:
            connection.execute("UPDATE candidate_refresh_state SET state=?,checked_at=?",
                               (prior_state, old))
        catalog.persist(connection, catalog.normalize_record(
            dict(record, ticker="PREVIOUS"), "A-24HS", old, "previous"))
        catalog.persist_family_coverage(connection, old_config, [], [], "previous", old)
        connection.execute("INSERT INTO broker_market_configuration VALUES('markets',?,?)",
                           (old, '["BYMA"]'))
        connection.execute("INSERT INTO source_sync VALUES(?,'VERDE',?,?,1,'previous')",
                           (source, old, old))
    monkeypatch.setattr(observer, "_candidate_universe", lambda: [
        (ticker, "ACCIONES", "A-24HS", "BYMA", False, "ACCIONES")
        for ticker in ("FIRST", "SECOND", "THIRD")])
    monkeypatch.setattr(observer, "_history_daily_sync_due", lambda *args: True)
    calls = []

    class Reader:
        def market_configuration(self):
            assert state(store) == "RUNNING"
            calls.append("configuration")
            if rotation == "configuration":
                raise SharedFeedUnavailable(reason)
            return dict(old_config, markets=["BYMA", "NASDAQ"])

        def search_instruments(self, ticker, *_args, **_kwargs):
            calls.append(ticker)
            if ticker == "SECOND":
                raise SharedFeedUnavailable(reason)
            return [dict(record, ticker=ticker)]

        def close(self):
            calls.append("close")

    monkeypatch.setattr(observer, "_download_histories",
                        lambda *args, **kwargs: pytest.fail("History used the obsolete reader"))
    assert observer._daily_sync(Reader(), store, now) is True
    assert calls == (["configuration", "close"] if rotation == "configuration"
                     else ["configuration", "FIRST", "SECOND", "close"])
    with store.connect() as connection:
        marker = connection.execute("SELECT state,checked_at FROM candidate_refresh_state").fetchone()
        assert (tuple(marker) if marker else None) == ((prior_state, old) if prior_state else None)
        assert connection.execute("SELECT ticker FROM financial_instrument_catalog").fetchone()[0] == "PREVIOUS"
        assert connection.execute("SELECT COUNT(*) FROM financial_instrument_catalog").fetchone()[0] == 1
        assert connection.execute("SELECT run_id FROM catalog_family_coverage").fetchone()[0] == "previous"
        assert tuple(connection.execute("SELECT checked_at,payload_json FROM broker_market_configuration "
                                        "WHERE name='markets'").fetchone()) == (old, '["BYMA"]')
        assert connection.execute("SELECT COUNT(*) FROM catalog_query_results").fetchone()[0] == 0
        assert tuple(connection.execute(
            "SELECT status,last_success_at,detail FROM source_sync WHERE source=?", (source,)
        ).fetchone()) == ("DEFERRED_GATEWAY_REATTACH", old, reason)
    retry = timedelta(seconds=observer.GATEWAY_LOCAL_ATTACH_RETRY_SECONDS)
    assert not observer._source_sync_due(store, source, now + retry - timedelta(seconds=1))
    assert observer._source_sync_due(store, source, now + retry)


def test_preopen_rechecks_the_scanner_pin_before_publishing_ready(tmp_path, monkeypatch):
    store = prepared(tmp_path, monkeypatch)
    reason = "PPI_GATEWAY_OWNER_CHANGED"
    start = 100_000
    clock = [start]
    attachments = []
    ready = []

    class Reader:
        def __init__(self, obsolete):
            self.obsolete = obsolete
            self.closed = False

        @property
        def metrics(self):
            if self.obsolete:
                raise SharedFeedUnavailable(reason)
            return {"authenticated": True}

        def close(self):
            self.closed = True

    readers = [Reader(obsolete=True), Reader(obsolete=False)]

    def attach(database, *, consumer):
        assert (database, consumer) == (store.path, "scanner")
        attachments.append(clock[0])
        return readers[len(attachments) - 1]

    def sleep(seconds):
        clock[0] += seconds
        assert clock[0] <= start + 60
        with store.connect() as connection:
            item = connection.execute("""SELECT process_state,ppi_auth,real_orders_sent
                FROM observer_state WHERE id=1""").fetchone()
        if item[0] == "READY_PREOPEN":
            ready.append(tuple(item))
            monkeypatch.setattr(observer, "STOP", True)

    monkeypatch.delenv("POROTA_RUNTIME_SCHEMA_READY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(observer, "STOP", False)
    monkeypatch.setattr(observer, "runtime_store", lambda _: store)
    monkeypatch.setattr(observer, "_market_phase", lambda: "PREOPEN")
    monkeypatch.setattr(observer, "_daily_sync", lambda *_: None)
    monkeypatch.setattr(observer, "_public_probe", lambda *_: None)
    monkeypatch.setattr(observer, "_periodic_complementary_reconcile", lambda *args: args[2])
    monkeypatch.setattr(observer.operations, "service_tick", lambda *_: None)
    monkeypatch.setattr(observer.signal, "signal", lambda *_: None)
    monkeypatch.setattr(observer, "time", SimpleNamespace(
        time=lambda: clock[0], monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(gateway, "connect_paper_gateway_reader", attach)
    monkeypatch.setattr(runtime, "broker_from_environment", lambda *args, **kwargs:
                        SimpleNamespace(settle_cauciones=lambda: None))

    assert observer.run() == 0
    assert readers[0].closed
    assert attachments == [start, start + observer.GATEWAY_LOCAL_ATTACH_RETRY_SECONDS]
    assert ready == [("READY_PREOPEN", "OK", 0)]
    with store.connect() as connection:
        assert connection.execute("""SELECT detail FROM paper_events
            WHERE event_type='PPI_GATEWAY_REATTACH_REQUIRED'""").fetchone()[0] == reason


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
