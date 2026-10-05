"""UX470-I01..I05 / AUD15..16 over canonical offline writers and readers."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys

from bs4 import BeautifulSoup
import pytest

import rc6_annual_instrument_analysis as annual
from rc6_dynamic_universe.promotion import DEFAULT_POLICY
from rc6_paper_family_lifecycle import FamilyPaperExecutor, future_positions, future_risk_snapshot
from rc6_shadow_runtime.persistence import read_committed_generation, shadow_evidence_root
from rc6_trader_dashboard.datasets import instrument_market, policy_state, shadow_rows
from rc6_trader_dashboard.navigation import CANONICAL_PATHS, LEGACY
from rc6_trader_dashboard.projection import Projection, Store, funnel_cohort_id, freshness
from rc6_trader_dashboard.routes import build_page
from rc6_trader_dashboard.view_common import committed_funnel
from tests.rc6_dashboard_native_fixture import AS_OF, native_fixture
from tests.test_rc6_future_programming_complete import dlr
from tests.test_rc6_ppi_capacity_benchmark import wire
from tests.test_rc6_capacity_promotion import approved


@pytest.fixture
def native(tmp_path):
    return native_fixture(tmp_path)


def inventory(path):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        member = Path(str(path)+suffix)
        if member.exists():
            info = member.stat()
            fd = os.open(member, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                digest = hashlib.sha256(stream.read()).hexdigest()
            result[suffix] = [digest, info.st_size, info.st_ino, info.st_mtime_ns, info.st_atime_ns]
    return result


def test_native_worker_reader_and_all_ui_datasets_share_default_v2_cut(native, monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("Rendering attempted network I/O")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    native_connect = sqlite3.connect
    def no_source_sqlite(database, *args, **kwargs):
        assert str(database).split("?", 1)[0] not in {str(native.database), native.database.as_uri()}, "SQLite opened the source"
        return native_connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", no_source_sqlite)
    before = inventory(native.database)
    trace = []
    with Store(native.database, now=native.as_of, trace=trace.append) as store:
        projection = Projection(store)
        assert native.root == shadow_evidence_root(native.database)
        assert projection.shadow["state"] == "COMMITTED_COHERENT_SHADOW"
        assert projection.shadow["pointer"] == native.cut["pointer"]
        for kind in ("signals", "experiments", "exits", "families", "capacity", "opportunities"):
            page = shadow_rows(projection, kind)
            assert page.state == "AVAILABLE", (kind, page.reason)
            assert page.total and page.rows, kind
            assert len(page.rows) <= 10
            assert all(row.get("entry_authority") is False for row in page.rows if kind != "capacity")
        assert policy_state(projection) == "OFF"
        capacity = shadow_rows(projection, "capacity")
        assert len(capacity.rows) == 2
        assert all(row["safe_capacity"] is None and row["open_evidence"] == "NO_VERIFICADO" for row in capacity.rows)
        projection.positions()
        assert not store.errors
    assert inventory(native.database) == before
    assert not any("quick_check" in query.lower() or "SELECT *" in query.upper() for query in trace)


@pytest.mark.parametrize("mode,expected", [("OFF", "OFF"), ("SHADOW", "SHADOW"), ("APPROVED", "BASELINE_FAIL_CLOSED")])
def test_native_capacity_modes_are_explicit_and_never_infer_open(tmp_path, monkeypatch, mode, expected):
    policy = deepcopy(DEFAULT_POLICY)
    policy["mode"] = mode
    policy_path = tmp_path/"capacity-policy.json"
    policy_path.write_text(json.dumps(policy))
    monkeypatch.setenv("POROTA_CAPACITY_POLICY_PATH", str(policy_path))
    fixture = native_fixture(tmp_path, with_future=False, with_spot=False)
    with Store(fixture.database, now=fixture.as_of) as store:
        projection = Projection(store)
        assert policy_state(projection) == expected
        page = shadow_rows(projection, "capacity")
        assert page.rows and all(row["policy_state"] == expected for row in page.rows)
        assert all(row["safe_capacity"] is None for row in page.rows)
    with Store(fixture.database, now=fixture.as_of+timedelta(seconds=31)) as store:
        assert policy_state(Projection(store)) == "NO_VERIFICADO"


def test_approved_capacity_uses_benchmark_sdk_and_native_writer_not_adaptor_mock(tmp_path, monkeypatch, wire):
    policy, recommendation, report, approval, at = approved(wire)
    # The SDK is exercised through its existing offline test transport. Only
    # the native benchmark output and explicitly reviewed config are published.
    for name, payload in (("policy", policy), ("recommendation", recommendation), ("report", report), ("approval", approval)):
        path = tmp_path/(name+".json")
        path.write_text(json.dumps(payload))
        monkeypatch.setenv("POROTA_CAPACITY_"+name.upper()+"_PATH", str(path))
    fixture = native_fixture(tmp_path, as_of=at, with_future=False, with_spot=False)
    with Store(fixture.database, now=fixture.as_of) as store:
        projection = Projection(store)
        assert policy_state(projection) == "APPROVED_DYNAMIC"
        page = shadow_rows(projection, "capacity")
        assert page.rows and all(row["policy_state"] == "APPROVED_DYNAMIC" for row in page.rows)
        if projection.shadow["report"].get("capacity_open_status") != "OPEN_EVIDENCE_VERIFIED":
            assert all(row["safe_capacity"] is None for row in page.rows)
        assert projection.positions().total == 0


def test_native_funnel_cohort_selection_is_exact_and_cards_match_widget(native):
    groups = native.cut["report"]["operational_funnel"]["cohorts"]
    selected = next(row for row in groups if row["channel"] == "NATIVE_FACTUAL" and row["stages"].get("PAPER_OPENED"))
    filters = {"currency": selected["currency"], "channel": selected["channel"], "cohort": funnel_cohort_id(selected)}
    with Store(native.database, now=native.as_of) as store:
        projection = Projection(store, filters)
        assert projection.funnel_scope["selected"] == selected
        assert projection.counts()["PAPER"] == selected["stages"]["PAPER_OPENED"]
        assert "<b>"+str(projection.counts()["PAPER"])+"</b>" in committed_funnel(projection)
        assert selected["symbol"] in projection.funnel_scope["label"]
        wrong = Projection(store, {**filters, "cohort": "f"*64})
        assert not wrong.funnel_scope.get("selected") and wrong.funnel_scope["counts"] == {}
        assert wrong.funnel_scope["reason"] == "FUNNEL_SCOPE_NOT_PUBLISHED"
        wrong_currency = Projection(store, {**filters, "currency": "USD_CCL"})
        assert wrong_currency.funnel_scope["counts"] == {}


def test_active_future_with_zero_spot_is_visible_in_every_risk_surface(tmp_path):
    fixture = native_fixture(tmp_path, with_spot=False)
    with Store(fixture.database, now=fixture.as_of) as store:
        projection = Projection(store)
        page = projection.positions()
        assert page.total == 1 and len(page.rows) == 1
        row = page.rows[0]
        assert row["symbol"] == "DLR/OCT26" and row["status"] == "ACTIVE"
        assert row["ledger"] == "paper_future_positions" and row["unit"] == "CONTRACTS"
        assert projection.counts()["open_positions"] == 1
        canonical = future_positions(None, connection=store.connection, as_of=fixture.as_of, lifecycle_ids=["FUT-UI"])[0]
        for key in ("margin_reserved", "collateral", "cash_effect", "exposure", "variation_realized", "unrealized_pnl"):
            assert row[key] == canonical[key]
        risk = future_risk_snapshot(None, "ARS", fixture.as_of, connection=store.connection)
        assert Decimal(row["collateral"]) == risk["collateral"]
        exact = Projection(store, {"identity": row["identity"]}).positions()
        assert exact.total == 1 and exact.rows[0]["lifecycle_id"] == "FUT-UI"
        another_currency = json.loads(row["identity"])
        another_currency[3] = "USD"
        assert Projection(store, {"identity": json.dumps(another_currency)}).positions().total == 0
    for path in ("/", "/en-vivo", "/en-vivo/posiciones", "/riesgo/posiciones", "/riesgo/exposicion", "/riesgo/liquidez"):
        html, _ = build_page(path, {}, fixture.database, now=fixture.as_of)
        assert "DLR/OCT26" in html, path
        assert "Spot OPEN + FUTUROS ACTIVE" in html or "paper_future_positions" in html


def test_future_closed_after_cut_stays_active_at_cut_and_closes_once_with_decimal_pnl(tmp_path):
    fixture = native_fixture(tmp_path, with_spot=False)
    close = fixture.as_of+timedelta(microseconds=1)
    executor = FamilyPaperExecutor(fixture.store)
    executor.close_future(dlr(), lifecycle_id="FUT-UI", event_id="FUT-UI:CLOSE", exit_price="1520",
                          exit_cost="100", book_at=close.isoformat(), occurred_at=close.isoformat(), reason="EOD")
    with Store(fixture.database, now=fixture.as_of) as store:
        projection = Projection(store)
        assert projection.positions().rows[0]["status"] == "ACTIVE"
        assert projection.counts()["open_positions"] == 1
        assert projection.positions(closed=True).total == 0
        assert projection.performance().rows == []
    with Store(fixture.database, now=close) as store:
        projection = Projection(store)
        assert projection.positions().total == 0
        row = projection.positions(closed=True).rows[0]
        canonical = future_positions(None, connection=store.connection, as_of=close, lifecycle_ids=["FUT-UI"])[0]
        summary = projection.performance().rows[0]
        assert row["net_pnl"] == canonical["realized_pnl"] == summary["net_pnl"]
        assert summary["gross_pnl"] == canonical["gross_realized_pnl"] == "20000"
        assert summary["costs"] == "200"
        assert summary["currency"] == "ARS" and summary["family"] == "FUTUROS"
        assert row["collateral"] == "0" and row["margin_reserved"] == "1500000"


def test_pending_wal_without_source_shm_is_read_from_copy_including_future_mark(native):
    import shutil
    destination = native.database.parent/"wal-source.db"
    # Checkpoint an owned fixture before constructing a durable pending WAL.
    with native.store.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(native.database, destination)
    writer = sqlite3.connect(destination)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("UPDATE paper_future_positions SET last_mark_price='1511' WHERE lifecycle_id='FUT-UI'")
    writer.commit()
    # Unlink only this test-owned process-local sidecar. Store must recreate it
    # in scratch; the durable main+WAL remain untouched in this source tree.
    Path(str(destination)+"-shm").unlink()
    before = inventory(destination)
    try:
        with Store(destination, now=native.as_of) as store:
            assert store.query("SELECT last_mark_price FROM paper_future_positions WHERE lifecycle_id=?", ("FUT-UI",))[0]["last_mark_price"] == "1511"
        assert inventory(destination) == before
        assert not Path(str(destination)+"-shm").exists()
    finally:
        writer.close()


@pytest.mark.parametrize("family", ["BONOS", "LETRAS", "ON", "OBLIGACIONES", "OBLIGACIONES_NEGOCIABLES"])
def test_fixed_income_annual_price_variation_excludes_flows_and_no_total_return_claim(family):
    bars = [dict(date="2025-12-30", close=100, source="PPI_API", adjusted=0),
            dict(date="2026-09-30", close=80, source="PPI_API", adjusted=0)]
    html = annual._render_report((family, "FIXED", "BYMA", "ARS", "A-24HS", "RAW", "RAW_NO_ADJUSTMENT"), bars, 2026)
    assert "Variación de precio 2026 (sin flujos)" in html and "Performance 2026" not in html
    assert "excluye cupones, amortizaciones, interés corrido y reinversión" in html
    assert "Retorno total / TIR / interés corrido" in html and "NO_VERIFICADO" in html
    assert "-20,00%" in html or "-20.00%" in html


def test_historical_revisions_currency_basis_and_retry_clocks_are_causal(tmp_path, monkeypatch):
    from be_paper_engine import PaperStore
    from cu_history_store_v2_hf6 import Candle, append_many
    from dataclasses import replace
    path = tmp_path/"history.db"
    writer = PaperStore(str(path))
    # Native history rejects observations later than the host's real clock.
    # Use a completed past session while keeping the correction after the cut.
    history_cut = datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)
    first = history_cut-timedelta(minutes=2)
    candle = Candle("COLLIDE", "BONOS", "BYMA", "A-24HS", "2026-09-30", 100, 101, 99, 100, 50,
                    "PPI_API", observed_at=first.isoformat(), currency="ARS")
    append_many(writer, [candle, replace(candle, currency="USD", open=20, high=21, low=19, close=20)])
    append_many(writer, [replace(candle, observed_at=(first+timedelta(minutes=1)).isoformat())])
    # A later correction cannot remove the earlier known version at the cut.
    append_many(writer, [replace(candle, close=80, low=80, observed_at=(history_cut+timedelta(microseconds=1)).isoformat())])
    monkeypatch.setenv("HIST_DB_PATH", str(path))
    identity = ("BONOS", "COLLIDE", "BYMA", "ARS", "A-24HS", "RAW", "RAW_NO_ADJUSTMENT")
    bars = annual._bars(identity, history_cut)
    assert len(bars) == 1 and bars[0]["close"] == 100
    assert bars[0]["observed_at"] == bars[0]["version_known_at"]
    assert bars[0]["last_checked_at"] != bars[0]["observed_at"] and bars[0]["last_checked_at"] is not None
    with Store(path, now=history_cut) as store:
        page = Projection(store, {"currency": "ARS", "price_basis": "RAW"}).history()
        assert page.total == 1 and page.rows[0]["row_count"] == 1
        assert page.rows[0]["observed_at"] == bars[0]["observed_at"]
        assert page.rows[0]["last_checked_at"] != bars[0]["observed_at"]
        assert page.rows[0]["decision_input_authority"].startswith("NONE")
    with pytest.raises(ValueError, match="FULL_IDENTITY"):
        annual._bars(identity[:4], history_cut)


def test_navigation_scope_preserves_exact_contract():
    assert len(CANONICAL_PATHS) == 49 and len(LEGACY) == 22
    assert "rc6_trader_dashboard" in Path("o_dashboard.py").read_text()


def test_ttl_boundaries_never_round_stale_or_future_clocks_into_fresh():
    assert freshness(AS_OF.isoformat(), AS_OF+timedelta(seconds=30), 30) == "FRESH"
    assert freshness(AS_OF.isoformat(), AS_OF+timedelta(seconds=30, microseconds=1), 30) == "STALE"
    assert freshness((AS_OF+timedelta(microseconds=1)).isoformat(), AS_OF, 30) == "NO_VERIFICADO"


def test_native_risk_and_valuation_updates_after_cut_do_not_retrofit_old_panel(native):
    import gc
    from bw_daily_risk import DailyRisk
    with Store(native.database, now=native.as_of) as store:
        before = {row["currency"]: row["equity"] for row in Projection(store).balances().rows}
    later = native.as_of + timedelta(microseconds=1)
    native.broker.mark_equity({}, as_of=later.isoformat())
    DailyRisk(native.broker, "1").evaluate(later.isoformat())
    gc.collect()
    with Store(native.database, now=native.as_of) as store:
        projection = Projection(store)
        balances = projection.balances()
        assert {row["currency"]: row["equity"] for row in balances.rows} == before
        assert all(row["valuation_state"] == "NO_VERIFICADO" for row in balances.rows)
        assert projection.risk().total == 0
    with Store(native.database, now=later) as store:
        risk = Projection(store).risk()
        assert risk.total and risk.rows
        assert all(row["evaluated_at"] == later.isoformat() for row in risk.rows)


def test_expired_read_discards_body_and_never_reopens_generation_for_error_shell(native, monkeypatch):
    from contextlib import contextmanager
    import rc6_audit_evidence.sqlite_snapshot as snapshot
    import rc6_shadow_runtime.persistence as persistence
    before = inventory(native.database)
    original_copy, original_reader = snapshot.readonly_copy, persistence.read_committed_generation
    calls = []
    @contextmanager
    def expires_on_exit(*args, **kwargs):
        with original_copy(*args, **kwargs) as connection:
            yield connection
            raise snapshot.SnapshotError("TIME_BUDGET_EXHAUSTED")
    def counted_reader(*args, **kwargs):
        calls.append(1)
        return original_reader(*args, **kwargs)
    monkeypatch.setattr(snapshot, "readonly_copy", expires_on_exit)
    monkeypatch.setattr(persistence, "read_committed_generation", counted_reader)
    html, _ = build_page("/instrumentos", {}, native.database, now=native.as_of)
    assert calls == [1]
    assert "Corte de lectura no disponible dentro del presupuesto" in html
    assert not BeautifulSoup(html, "html.parser").select("tr[data-row]")
    assert inventory(native.database) == before


@pytest.mark.parametrize("corruption", ["planner_rows", "capacity_object", "lab_rows", "typed_safety"])
def test_adapter_unknown_shapes_and_typed_safety_fail_closed(native, corruption):
    # Negative adapter fuzzing derives from a real writer/read bundle. Resign
    # decoded payload digests to isolate shape validation from hash validation.
    cut = deepcopy(native.cut)
    engine = next(iter(cut["report"]["engines"].values()))
    kind = "capacity"
    if corruption == "planner_rows":
        engine["telemetry"] = {"unexpected": []}
    elif corruption == "capacity_object":
        engine["capacity"] = []
    elif corruption == "lab_rows":
        cut["report"]["entry_signal_lab"]["experiments"] = {"unexpected": []}
        kind = "experiments"
    else:
        cut["report"]["safety"]["provider_requests"] = False
    for role in ("report", "checkpoint", "status"):
        encoded = json.dumps(cut[role], sort_keys=True, separators=(",", ":"), default=str).encode()
        cut["manifest"]["files"][role]["payload_digest"] = hashlib.sha256(encoded).hexdigest()
    encoded_manifest = json.dumps(cut["manifest"], sort_keys=True, separators=(",", ":"), default=str).encode()
    cut["pointer"]["manifest_sha256"] = hashlib.sha256(encoded_manifest).hexdigest()
    with Store(native.database, now=native.as_of) as store:
        projection = Projection(store, generation_reader=lambda *_args, **_kwargs: cut)
        page = shadow_rows(projection, kind)
        assert not page.rows and page.total is None
        assert page.state == ("NO_VERIFICADO" if corruption == "typed_safety" else "CONTRACT_ERROR")


def dynamic_worker(projection):
    return next(row for row in projection.workers().rows if row["worker"] == "Selector dinámico SHADOW")


def test_runtime_native_child_health_is_independent_from_generation_freshness_and_source(native, monkeypatch):
    from bv_paper_runtime import ChildProcesses, publish_child_health
    monkeypatch.setenv("POROTA_BUILD_SHA", "a"*40)
    monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", "b"*40)
    children = ChildProcesses({"dynamic_shadow": [sys.executable, "-c", "import signal; signal.pause()"]},
                              startup_grace_seconds=0)
    try:
        with Store(native.database, now=native.as_of) as store:
            missing = dynamic_worker(Projection(store))
            assert missing["state"] == "NO_VERIFICADO"
            assert missing["generation_freshness"] == "FRESH"
        children.poll()
        publish_child_health(native.store, children, recorded_at=native.as_of.isoformat())
        with Store(native.database, now=native.as_of) as store:
            live = dynamic_worker(Projection(store))
            assert live["state"] == "RUNNING" and live["pid"] == children.processes["dynamic_shadow"].pid
            assert live["generation_state"] == "COMMITTED_COHERENT_SHADOW"
            assert live["generation_freshness"] == "FRESH"
            assert live["source_sha"] == "a"*40 and live["operational_readiness"] == "NO_VERIFICADO"
        later = native.as_of+timedelta(seconds=30, microseconds=1)
        publish_child_health(native.store, children, recorded_at=later.isoformat())
        with Store(native.database, now=later) as store:
            stale_cut = dynamic_worker(Projection(store))
            assert stale_cut["state"] == "RUNNING" and stale_cut["freshness"] == "FRESH"
            assert stale_cut["generation_freshness"] == "STALE"
            assert stale_cut["operational_readiness"] == "NO_VERIFICADO"
        monkeypatch.setenv("POROTA_BUILD_SHA", "c"*40)
        with Store(native.database, now=later) as store:
            assert dynamic_worker(Projection(store))["state"] == "NO_VERIFICADO"
    finally:
        children.close()


def test_runtime_native_spawn_failure_and_preopen_are_visible(tmp_path):
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    from cf_intraday_scalping import init_schema
    from bv_paper_runtime import ChildProcesses, publish_child_health
    from rc6_shadow_runtime.worker import ShadowRuntime
    store = PaperStore(str(tmp_path/"preopen.db"))
    _support_schema(store)
    init_schema(store)
    at = AS_OF.replace(hour=13, minute=20)
    runtime = ShadowRuntime.from_environment(store.path, source_roots=[])
    runtime.tick(at)
    children = ChildProcesses({"dynamic_shadow": [str(tmp_path/"absent-child")]}, startup_grace_seconds=0)
    try:
        children.poll()
        publish_child_health(store, children, recorded_at=at.isoformat())
        with Store(store.path, now=at) as copied:
            row = dynamic_worker(Projection(copied))
            assert row["state"] == "SPAWN_FAILED" and row["pid"] is None
            assert row["spawn_failures"] == 1 and row["last_error"] == "CHILD_SPAWN_FAILED"
            assert row["generation_freshness"] == "FRESH"
            assert row["operational_readiness"] == "PREOPEN_NON_OPERATIONAL"
    finally:
        children.close()


def test_native_future_supervision_intent_survives_restart_and_is_visible_without_spot(tmp_path):
    from be_paper_engine import PaperBroker
    fixture = native_fixture(tmp_path, with_spot=False)
    fixture.broker.supervise_futures(fixture.as_of.isoformat())
    with Store(fixture.database, now=fixture.as_of) as store:
        first = Projection(store).positions().rows[0]
        assert first["exit_state"] == "WATCH_NO_QUOTE" and first["exit_freshness"] == "FRESH"
        assert first["exit_cause"] is None and first["exit_attempts"] == 0
    due = fixture.as_of.replace(hour=17, minute=50)
    fixture.broker.supervise_futures(due.isoformat())
    with Store(fixture.database, now=due) as store:
        pending = Projection(store).positions().rows[0]
        assert pending["exit_state"] == "EXIT_PENDING_NO_QUOTE"
        assert pending["exit_cause"] == "EOD_PAPER" and pending["exit_due_at"] == due.isoformat()
        assert pending["status"] == "ACTIVE" and pending["last_mark_at"] == first["last_mark_at"]
    restart_at = due+timedelta(seconds=1)
    restarted = PaperBroker(fixture.store, clock_fn=lambda: restart_at.isoformat(), ai_mode="OFF", economics_mode="SHADOW")
    restarted.supervise_futures(restart_at.isoformat())
    with Store(fixture.database, now=restart_at) as store:
        durable = Projection(store).positions().rows[0]
        assert durable["exit_state"] == "EXIT_PENDING_NO_QUOTE"
        assert durable["exit_cause"] == pending["exit_cause"] and durable["exit_due_at"] == pending["exit_due_at"]
        assert durable["exit_attempts"] == 0
        assert not Projection(store).positions(closed=True).rows
        assert store.query("SELECT COUNT(*) n FROM paper_future_marks")[0]["n"] == 1
    html, _ = build_page("/riesgo/posiciones", {}, fixture.database, now=restart_at)
    assert "EXIT_PENDING_NO_QUOTE" in html and "EOD_PAPER" in html


def test_native_decision_and_atomic_receipt_are_distinct_linked_causal_and_read_only(native):
    before = inventory(native.database)
    trace = []
    with Store(native.database, now=native.as_of, trace=trace.append) as store:
        page = Projection(store).decisions()
        assert page.total == 1 and len(page.rows) == 1
        row = page.rows[0]
        assert row["evidence_phase"] == "NATIVE_DECISION"
        assert row["evidence_state"] == "DIGEST_VERIFIED_IMMUTABLE"
        assert row["admission_state"] == "DIGEST_VERIFIED_LINKED_ATOMIC_RECEIPT"
        assert row["admission_snapshot_key"] == "PAPER_ADMISSION:"+row["decision_key"]
        assert row["admission_at"] < row["entry_fill_recorded_at"] < row["entry_fill_committed_at"]
        assert row["admission_captured_at"] == row["entry_fill_recorded_at"]
        assert store.query("SELECT COUNT(*) n FROM decision_evidence_snapshots")[0]["n"] == 2
        link = row["admission_snapshot_key"]
    assert inventory(native.database) == before
    assert not any("SELECT *" in query.upper() for query in trace)
    # Corruption is injected only into this test-owned SQLite source. Native
    # writers never rewrite immutable receipt hashes.
    with native.store.connect() as connection:
        connection.execute("UPDATE decision_evidence_snapshots SET payload_sha256=? WHERE decision_key=?", ("f"*64, link))
    with Store(native.database, now=native.as_of) as store:
        invalid = Projection(store).decisions().rows[0]
        assert invalid["admission_state"] == "NO_VERIFICADO"
        assert invalid["admission_snapshot_key"] is None
        assert invalid["admission_reason"] == "RECEIPT_NOT_AVAILABLE_AT_CUT_OR_REJECTED"
