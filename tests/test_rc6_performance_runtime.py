"""Behavioral regressions for the previously blocked spot/scanner components."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from be_paper_engine import D, PaperBroker, PaperStore, Quote
from rc6_performance import lineage
from rc6_performance.capture import BoundedCapture, EvidenceStore
from rc6_performance.costs import ledger_leg_cost, price_sensitivity_fees, smaller_leg_rebate
from rc6_performance.metrics import decision_funnel
from rc6_performance.report import evidence_report
from rc6_performance.scanner import sampling_plan

AT = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)


def quote(price="100", at=AT):
    value = D(price)
    text = at.isoformat()
    return Quote("GGAL", "ACCIONES", "A-24HS", value, value-D(".01"), value+D(".01"),
                 D(10000), D(10000), text, currency="ARS", market="BYMA",
                 metadata_source="TEST", book_at=text, trade_at=text, last_kind="TRADE")


def snapshots(store):
    with store.connect() as c:
        rows = c.execute("SELECT payload_json,payload_sha256 FROM decision_evidence_snapshots ORDER BY rowid").fetchall()
    for payload, expected in rows:
        assert hashlib.sha256(payload.encode()).hexdigest() == expected
    # Financial admission receipts share the canonical store, but represent a
    # different phase and cannot substitute for the native decision clocks.
    return [value for row in rows if (value := json.loads(row[0])).get("capture_phase") != "ATOMIC_PAPER_ADMISSION"]


def test_native_decision_precedes_intent_and_committed_fill(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.setenv("PAPER_HISTORICAL_CANDLE_SHADOW", "OFF")
    store = PaperStore(str(tmp_path/"paper.db"))
    evidence = EvidenceStore(tmp_path/"evidence.db")
    capture = BoundedCapture(store.path, evidence)
    capture.read_batch()
    tick = [AT + timedelta(seconds=1)]
    def clock():
        tick[0] += timedelta(microseconds=100)
        return tick[0].isoformat()
    broker = PaperBroker(store, clock_fn=clock, signal_min_samples=3,
                         score_threshold=".50", ai_mode="OFF", daily_loss_pct="100")
    for seconds, price in ((-150, "95"), (-120, "96"), (-90, "97"), (-60, "98"), (-30, "99"), (0, "100")):
        q = quote(price, AT+timedelta(seconds=seconds))
        store.add_quote(q)
    broker.on_quote(q)
    data = snapshots(store)[0]
    assert data["decision"]["final_result"] == "OPENED_SIMULATED"
    assert data["captured_at"] == q.observed_at
    times = [datetime.fromisoformat(data[k]) for k in
             ("signal_at", "decision_at", "intent_at", "entry_fill_committed_at")]
    assert times == sorted(times) and len(set(times)) == 4
    assert times[0] > datetime.fromisoformat(q.observed_at)
    with store.connect() as connection:
        raw = connection.execute("SELECT payload_json,payload_sha256 FROM decision_evidence_snapshots ORDER BY rowid").fetchall()
    assert len(raw) == 2
    receipt = json.loads(raw[0][0])
    assert receipt["capture_phase"] == "ATOMIC_PAPER_ADMISSION"
    assert receipt["decision_key"] == "PAPER_ADMISSION:" + data["decision_key"]
    assert receipt["entry_fill_committed_at"] is receipt["runtime"]["entry_fill_committed_at"] is None
    assert datetime.fromisoformat(receipt["intent_at"]) <= datetime.fromisoformat(receipt["entry_fill_recorded_at"]) <= times[-1]
    assert data["inputs_used"]["financial_admission_snapshot_key"] == receipt["decision_key"]
    assert data["inputs_used"]["financial_admission_snapshot_sha256"] == raw[0][1]
    assert data["inputs_used"]["entry_signal_inputs"] == receipt["inputs_used"]["entry_signal_inputs"]
    assert decision_funnel([json.loads(row[0]) for row in raw])["stages"]["OPENED"] == 1
    assert data["runtime"]["git_sha"] is None
    assert len(data["runtime"]["configuration_fingerprint"]) == 64
    row = decision_funnel([data])["lineage"][0]
    assert row["strategy_id"] == "SPOT_MOMENTUM_BASELINE"
    assert row["reason_code_source"] == "NATIVE_GATE"
    assert row["paper_id"] == store.open_positions()[0]["paper_id"]
    position = store.open_positions()[0]
    tick[0] = AT + timedelta(minutes=1)
    assert broker._close(position, quote("110", tick[0]), "TEST")
    capture.read_batch()
    joined = evidence_report(evidence.path)["funnel"]["lineage"][0]
    assert joined["exit_pnl_status"] == "LEDGER_RECONCILED"
    assert joined["closed_at"] and joined["first_exit_fill_at"]
    assert joined["realized_pnl"]["currency"] == "ARS"


def test_event_time_simulation_never_fabricates_native_clock(tmp_path):
    store = PaperStore(str(tmp_path/"paper.db"))
    broker = PaperBroker(store)
    broker.on_quote(quote())
    data = snapshots(store)[0]
    assert data["signal_at"] is None and data["decision_at"] is None
    assert data["runtime"]["clock_mode"] == "EVENT_TIME_SIMULATION_UNVERIFIED"


def test_resolved_fingerprint_stable_across_stores_and_sensitive_to_constructor(tmp_path):
    a = PaperBroker(PaperStore(str(tmp_path/"a.db")), signal_min_samples=6)
    b = PaperBroker(PaperStore(str(tmp_path/"b.db")), signal_min_samples=6)
    c = PaperBroker(b.store, signal_min_samples=9)
    assert lineage.resolved_configuration(a) == lineage.resolved_configuration(b)
    assert lineage.resolved_configuration(a) != lineage.resolved_configuration(c)


def test_environment_inputs_are_hashed_and_not_disclosed(tmp_path, monkeypatch):
    broker = PaperBroker(PaperStore(str(tmp_path/"paper.db")))
    before = lineage.resolved_configuration(broker)
    monkeypatch.setenv("PAPER_MAX_HOLD_MINUTES", "123")
    after = lineage.resolved_configuration(broker)
    assert before != after
    assert set(after) == {"configuration_fingerprint", "configuration_scope", "opaque_configuration"}


def test_frozen_source_rejects_stale_manifest_and_undeclared_source(tmp_path):
    root = tmp_path/"app"; root.mkdir()
    meta = root/"data/deploy"; meta.mkdir(parents=True)
    code = root/"be_paper_engine.py"; code.write_text("pass\n")
    (meta/"porota-frozen-candidate.json").write_text(json.dumps({"candidate_sha": "a"*40}))
    manifest = {"status": "GREEN", "file_count": 1, "files": [{"path": code.name,
                "bytes": code.stat().st_size, "sha256": hashlib.sha256(code.read_bytes()).hexdigest()}]}
    (meta/"porota-deploy-bundle-v2-manifest.json").write_text(json.dumps(manifest))
    control = root/".github/workflows"; control.mkdir(parents=True)
    (control/"control_plane.py").write_text("pass\n")
    assert lineage.frozen_source(root)["git_sha"] == "a"*40
    code.write_text("changed\n"); lineage._cache.clear()
    assert lineage.frozen_source(root)["git_sha"] is None
    code.write_text("pass\n"); (root/"untracked.py").write_text("pass\n"); lineage._cache.clear()
    assert lineage.frozen_source(root)["git_sha"] is None


def test_pre_signal_rejection_preserves_missing_signal_and_native_reason(tmp_path):
    store = PaperStore(str(tmp_path/"paper.db"))
    broker = PaperBroker(store, clock_fn=lambda: (AT+timedelta(seconds=1)).isoformat())
    broker.on_quote(quote(), allow_new_openings=False,
                    opening_block_reason="COLD_DISCOVERY_NO_SIGNAL_BUDGET")
    data = snapshots(store)[0]
    assert data["signal_at"] is None and data["decision_at"]
    assert data["decision"]["reason_code"] == "COLD_DISCOVERY_NO_SIGNAL_BUDGET"
    assert decision_funnel([data])["stages"].get("BUY_CANDIDATE", 0) == 0
    assert store.open_positions() == []


@pytest.mark.parametrize("family", ["ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"])
def test_factual_cost_rounding_matches_existing_authority(family):
    import au_fee_schedule as fees
    price, qty = D("100.0123"), D("137")
    expected = (price*qty*D(fees.costo_por_tramo(family))).quantize(D(".01"))
    assert ledger_leg_cost(price, qty, family) == expected


@pytest.mark.parametrize("exit_price", ["99", "100", "105"])
def test_expected_smaller_leg_cost_matches_ledger_rate_sensitivity(exit_price):
    full, low = D(".007865"), D(".000605")
    entry, sell = D(100), D(exit_price)
    old = entry*low+sell*full if sell >= entry else entry*full+sell*low
    assert price_sensitivity_fees(entry, sell, full, low) == old
    assert smaller_leg_rebate(entry, sell, full, low, rounded=True) == (min(entry,sell)*(full-low)).quantize(D(".01"))


def plan(previous=None, cursor=0, now=AT, **overrides):
    catalog = [(f"S{i}", "ACCIONES", "A-24HS") for i in range(7614)]
    values = dict(limit=20, cycle_seconds=140, required_samples=6,
                  window_seconds=5400, now=now, cursor=cursor, previous=previous)
    values.update(overrides)
    return sampling_plan(catalog, [("OPEN", "ACCIONES", "A-24HS")], catalog[:8], **values)


def test_historical_scanner_becomes_feasible_without_deleting_catalog():
    selected, _, _, p = plan()
    assert len(selected) == 20
    assert p["feasible"] and p["catalog_effect"] == "NONE"
    assert p["full_catalog_budget"]["status"] == "INFEASIBLE"
    assert p["warm_slots"] == 10 and len(p["warm"]) == 60
    assert p["active_budget"]["estimated_samples_per_window"] >= 6
    assert set(p["cold"]).isdisjoint(p["opening_identities"])
    assert ("OPEN", "ACCIONES", "A-24HS") in selected


def test_active_basket_survives_json_checkpoint_restart_and_rotates_samples():
    first, _, after, p = plan()
    second, _, _, q = plan(json.loads(json.dumps(p)), after, AT+timedelta(seconds=140))
    assert q["basket_reused"] and p["warm"] == q["warm"]
    assert set(first)-set(p["pinned"]) != set(second)-set(q["pinned"])
    _, _, _, next_window = plan(q, now=AT+timedelta(seconds=5400))
    assert not next_window["basket_reused"]
    assert set(map(tuple,p["warm"])).isdisjoint(next_window["warm"])


def test_no_opening_authority_when_focus_sampling_budget_is_impossible():
    selected, _, _, p = plan(cycle_seconds=1000)
    assert not p["feasible"] and p["opening_identities"] == []
    assert ("OPEN", "ACCIONES", "A-24HS") in selected


def test_budget_and_configuration_change_invalidates_old_basket():
    _, _, _, p = plan()
    _, _, _, changed = plan(p, cycle_seconds=240, now=AT+timedelta(seconds=1))
    assert not changed["basket_reused"]
    assert len(changed["warm"]) < len(p["warm"])


def test_sampling_gate_cannot_block_existing_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    store = PaperStore(str(tmp_path/"paper.db"))
    broker = PaperBroker(store)
    assert broker._open(quote(), D(".8"), {})[0]
    broker.on_quote(quote("110", AT+timedelta(minutes=1)), allow_new_openings=False,
                    opening_block_reason="SCANNER_INFEASIBLE")
    assert store.open_positions() == []
    assert len(store.recent_closed()) == 1
