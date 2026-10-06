"""Atomic native receipts retain the signal cut and the actual PAPER linkage."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3

import pytest

from be_paper_engine import PaperBroker
from bq_exit_policy import PaperSessionPolicy
from rc6_performance.common import canonical, digest, stamp
from rc6_shadow_runtime.lab import _native_evidence, _register, _settings
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.test_rc6_shadow_lab_runtime import add_entry, clock, dump, source, tick, warm
from tests.test_rc6_shadow_runtime_wiring import make_store, quote, PRE


def envelope(payload, **changes):
    text = canonical(payload)
    return {"decision_key": payload["decision_key"], "captured_at": payload["captured_at"],
            "payload_sha256": hashlib.sha256(text.encode()).hexdigest(), "payload_json": text} | changes


def atomic_entry(path):
    position, payload = add_entry(path)
    opened, signal = stamp(position["opened_at"]), stamp(payload["signal_at"])
    payload["captured_at"] = (opened+timedelta(microseconds=200)).isoformat()
    payload["inputs_used"]["native_decision_key"] = payload["decision_key"]
    payload["inputs_used"]["entry_signal_inputs"] = {
        "schema": "rc6.native-entry-signal-input.v1", "samples": 1,
        "price_sample_status": "NATIVE_EXACT_VECTOR", "available_at": signal.isoformat(),
        "price_samples": [{**{key: position[key] for key in
            ("symbol", "asset_class", "settlement", "currency", "market")},
            "price": "99.9", "source": "PPI_NATIVE_OFFLINE_SYNTHETIC", "source_row_id": 1,
            "source_at": payload["quote_used"]["trade_at"], "received_at": signal.isoformat(),
            "known_at": signal.isoformat()}]}
    row = envelope(payload)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE decision_evidence_snapshots SET captured_at=?,payload_sha256=?,payload_json=?",
                           (row["captured_at"], row["payload_sha256"], row["payload_json"]))
    return position, payload


def phase_pair(path):
    position, payload = atomic_entry(path)
    payload.update(capture_phase="NATIVE_DECISION", captured_at=payload["quote_used"]["observed_at"])
    admission = deepcopy(payload)
    key = "PAPER_ADMISSION:"+payload["decision_key"]
    recorded = (stamp(position["opened_at"])+timedelta(microseconds=500)).isoformat()
    admission.update(capture_phase="ATOMIC_PAPER_ADMISSION", decision_key=key,
                     native_decision_key=payload["decision_key"], captured_at=recorded,
                     admission_at=position["opened_at"], entry_fill_recorded_at=recorded,
                     entry_fill_committed_at=None)
    admission["runtime"]["entry_fill_committed_at"] = None
    payload["inputs_used"].update(financial_admission_snapshot_key=key,
        financial_admission_snapshot_sha256=envelope(admission)["payload_sha256"])
    native_row, atomic_row = envelope(payload), envelope(admission)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE decision_evidence_snapshots SET captured_at=?,payload_sha256=?,payload_json=?",
                           (native_row["captured_at"], native_row["payload_sha256"], native_row["payload_json"]))
        connection.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?,?,?)",
                           (key, recorded, admission["schema"], atomic_row["payload_sha256"], atomic_row["payload_json"]))
    return position, payload, admission


def test_capture_after_signal_is_known_by_fill_and_registers_exact_original_link(tmp_path):
    path = source(tmp_path)
    checkpoint = warm(path)
    position, payload = atomic_entry(path)
    assert stamp(payload["signal_at"]) < stamp(payload["captured_at"])
    stages = [stamp(payload[name]) for name in ("signal_at", "decision_at", "intent_at", "entry_fill_committed_at")]
    assert stages == sorted(stages) and len(set(stages)) == 4
    before = dump(path)
    report, checkpoint = tick(path, clock(391), checkpoint)
    assert report["new_registrations"] == 1 and not report["rejections"]
    entry = report["entries"][0]
    assert entry["entry"]["paper_id"] == position["paper_id"]
    assert entry["native_decision_key"] == payload["decision_key"]
    assert entry["entry_evidence_sha256"] == envelope(payload)["payload_sha256"]
    assert entry["evidence_captured_at"] == payload["captured_at"]
    assert entry["signal_vector_status"] == "NATIVE_EXACT_VECTOR"
    assert entry["signal_input_sha256"] == digest(payload["inputs_used"]["entry_signal_inputs"])
    assert entry["native_clocks"]["signal_at"] == payload["signal_at"]
    assert dump(path) == before


@pytest.mark.parametrize("case,reason", [
    ("capture_after_fill", "ENTRY_FUTURE_OR_REVERSED_CLOCKS"),
    ("capture_column", "IMMUTABLE_DECISION_CAPTURE_MISMATCH"),
    ("envelope_key", "IMMUTABLE_DECISION_KEY_MISMATCH"),
    ("native_key", "IMMUTABLE_DECISION_KEY_MISMATCH"),
    ("missing_native_key", "IMMUTABLE_DECISION_KEY_UNAVAILABLE"),
    ("signal_clock", "ENTRY_SIGNAL_CLOCK_MISMATCH"),
    ("source_after_signal", "ENTRY_SIGNAL_INPUT_NOT_KNOWN_AT_SIGNAL"),
    ("received_after_signal", "ENTRY_SIGNAL_INPUT_NOT_KNOWN_AT_SIGNAL"),
    ("known_after_signal", "ENTRY_SIGNAL_INPUT_NOT_KNOWN_AT_SIGNAL"),
    ("available_after_signal", "ENTRY_SIGNAL_INPUT_NOT_KNOWN_AT_SIGNAL"),
    ("wrong_currency", "ENTRY_SIGNAL_IDENTITY_MISMATCH"),
    ("missing_vector", "ENTRY_NATIVE_SIGNAL_VECTOR_UNAVAILABLE"),
    ("policy_known_after_admission", "ENTRY_INPUT_POLICY_NOT_KNOWN_AT_ADMISSION"),
    ("policy_effective_after_admission", "ENTRY_INPUT_POLICY_NOT_KNOWN_AT_ADMISSION"),
])
def test_capture_receipt_never_authorizes_future_inputs_or_cross_decision_evidence(tmp_path, case, reason):
    path = source(tmp_path)
    _, payload = atomic_entry(path)
    vector = payload["inputs_used"]["entry_signal_inputs"]
    after_signal = (stamp(payload["signal_at"])+timedelta(microseconds=1)).isoformat()
    after_fill = (stamp(payload["entry_fill_committed_at"])+timedelta(microseconds=1)).isoformat()
    changes = {}
    if case == "capture_after_fill":
        payload["captured_at"] = after_fill
    elif case == "capture_column":
        changes["captured_at"] = after_fill
    elif case == "envelope_key":
        changes["decision_key"] = "OTHER_NATIVE_DECISION"
    elif case == "native_key":
        payload["inputs_used"]["native_decision_key"] = "OTHER_NATIVE_DECISION"
    elif case == "missing_native_key":
        del payload["inputs_used"]["native_decision_key"]
    elif case == "signal_clock":
        payload["runtime"]["signal_at"] = after_signal
    elif case in {"source_after_signal", "received_after_signal", "known_after_signal"}:
        sample = vector["price_samples"][0]
        sample[case.split("_after")[0]+"_at"] = after_signal
    elif case == "available_after_signal":
        vector["available_at"] = after_signal
    elif case == "wrong_currency":
        vector["price_samples"][0]["currency"] = "USD"
    elif case == "missing_vector":
        del payload["inputs_used"]["entry_signal_inputs"]
    else:
        name = "known_at" if case == "policy_known_after_admission" else "effective_at"
        payload["inputs_used"]["economics"] = {"cost_contract": {name: after_fill}}
    # Re-sealing these adversarial payloads exercises semantic guards, in
    # addition to the existing independent bad-hash regression.
    before = dump(path)
    with pytest.raises(ValueError, match="^"+reason+"$"):
        _native_evidence(envelope(payload, **changes), stamp(clock(391)))
    assert dump(path) == before


def test_policy_receipt_after_signal_but_at_admission_is_causal(tmp_path):
    path = source(tmp_path)
    _, payload = atomic_entry(path)
    payload["inputs_used"]["economics"] = {"cost_contract": {
        "known_at": payload["decision_at"], "effective_at": payload["decision_at"]}}
    assert _native_evidence(envelope(payload), stamp(clock(391)))["paper_id"] == "PAPER-new"


def test_two_phases_register_one_native_trade_even_across_a_one_row_read_budget(tmp_path):
    path = source(tmp_path)
    _, checkpoint = tick(path, clock(0), row_limit=1)
    position, payload, admission = phase_pair(path)
    before = dump(path)
    first, checkpoint = tick(path, clock(391), checkpoint, row_limit=1)
    assert not first["entries"] and first["rejections"]["IMMUTABLE_FINANCIAL_ADMISSION_UNAVAILABLE"] == 1
    report, checkpoint = tick(path, clock(392), checkpoint, row_limit=1)
    assert report["new_registrations"] == 1 and len(report["entries"]) == 1
    entry = report["entries"][0]
    assert entry["entry"]["paper_id"] == position["paper_id"]
    assert entry["financial_admission_snapshot_key"] == admission["decision_key"]
    assert entry["financial_admission_snapshot_hash"] == envelope(admission)["payload_sha256"]
    assert stamp(entry["entry_fill_recorded_at"]) < stamp(entry["native_clocks"]["entry_fill_committed_at"])
    assert entry["native_decision_key"] == payload["decision_key"]
    again, checkpoint = tick(path, clock(393), checkpoint, row_limit=1)
    assert again["new_registrations"] == 0 and len(again["entries"]) == 1
    assert dump(path) == before


@pytest.mark.parametrize("case,reason", [
    ("wrong_hash", "IMMUTABLE_FINANCIAL_ADMISSION_LINK_MISMATCH"),
    ("wrong_paper_id", "IMMUTABLE_FINANCIAL_ADMISSION_LINK_MISMATCH"),
    ("wrong_vector", "IMMUTABLE_FINANCIAL_ADMISSION_LINK_MISMATCH"),
    ("wrong_economics", "IMMUTABLE_FINANCIAL_ADMISSION_LINK_MISMATCH"),
    ("wrong_intent", "IMMUTABLE_FINANCIAL_ADMISSION_CLOCK_MISMATCH"),
    ("policy_after_admission", "ENTRY_INPUT_POLICY_NOT_KNOWN_AT_ADMISSION"),
    ("fabricated_commit", "IMMUTABLE_ADMISSION_COMMIT_CLOCK_FABRICATED"),
])
def test_two_phase_link_guards_hash_paper_vector_clocks_and_known_policy(tmp_path, case, reason):
    path = source(tmp_path)
    checkpoint = warm(path)
    _, native, admission = phase_pair(path)
    if case == "wrong_hash":
        native["inputs_used"]["financial_admission_snapshot_sha256"] = "0"*64
    elif case == "wrong_paper_id":
        admission["decision"]["paper_id"] = "PAPER-other"
    elif case == "wrong_vector":
        admission["inputs_used"]["entry_signal_inputs"]["price_samples"][0]["price"] = "77"
    elif case == "wrong_economics":
        admission["inputs_used"]["economics"] = {"cost_contract": {"policy_sha256": "0"*64}, "passed": True}
    elif case == "wrong_intent":
        admission["intent_at"] = (stamp(admission["intent_at"])+timedelta(microseconds=1)).isoformat()
    elif case == "policy_after_admission":
        native["inputs_used"]["economics"] = {"cost_contract": {
            "known_at": (stamp(admission["admission_at"])+timedelta(microseconds=1)).isoformat()}}
        admission["inputs_used"]["economics"] = deepcopy(native["inputs_used"]["economics"])
    else:
        admission["runtime"]["entry_fill_committed_at"] = native["entry_fill_committed_at"]
    if case != "wrong_hash":
        native["inputs_used"]["financial_admission_snapshot_sha256"] = envelope(admission)["payload_sha256"]
    if case == "fabricated_commit":
        with pytest.raises(ValueError, match="^"+reason+"$"):
            _native_evidence(envelope(admission), stamp(clock(391)))
        return
    a = _native_evidence(envelope(admission), stamp(clock(391)))
    n = _native_evidence(envelope(native), stamp(clock(391)))
    checkpoint["pending_admissions"] = {a["decision_key"]: a}
    with pytest.raises(ValueError, match="^"+reason+"$"):
        _register(dict(position_for(path)), n, checkpoint, *_settings({"slippage": "0", "participation": "0.1"}), stamp(clock(391)))


def position_for(path):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute("SELECT * FROM paper_positions").fetchone()


def test_same_identity_different_paper_id_cannot_borrow_the_snapshot(tmp_path):
    path = source(tmp_path)
    checkpoint = warm(path)
    position, payload = atomic_entry(path)
    position["paper_id"] = "PAPER-another-trade"
    settings, policy = _settings({"slippage": "0", "participation": "0.1"})
    evidence = _native_evidence(envelope(payload), stamp(clock(391)))
    with pytest.raises(ValueError, match="^ENTRY_PAPER_ID_MISMATCH$"):
        _register(position, evidence, checkpoint, settings, policy, stamp(clock(391)))


def test_legacy_early_capture_is_explicitly_unverified_without_fabricating_a_vector(tmp_path):
    path = source(tmp_path)
    _, payload = add_entry(path)
    evidence = _native_evidence(envelope(payload), stamp(clock(391)))
    assert evidence["signal_vector_status"] == "LEGACY_VECTOR_UNAVAILABLE"
    assert evidence["signal_input_sha256"] is None


def test_nonmapping_quote_payload_is_an_explicit_rejection(tmp_path):
    path = source(tmp_path)
    _, payload = atomic_entry(path)
    payload["quote_used"] = ["untrusted", "quote"]
    with pytest.raises(ValueError, match="^ENTRY_BOOK_PAYLOAD_INVALID$"):
        _native_evidence(envelope(payload), stamp(clock(391)))


def test_zero_frozen_multiplier_is_not_replaced_by_the_equity_default(tmp_path):
    path = source(tmp_path)
    checkpoint = warm(path)
    position, payload = atomic_entry(path)
    payload["inputs_used"]["contract_cash_multiplier"] = 0
    evidence = _native_evidence(envelope(payload), stamp(clock(391)))
    with pytest.raises(ValueError, match="^INVALID_NUMBER$"):
        _register(position, evidence, checkpoint, *_settings({"slippage": "0", "participation": "0.1"}), stamp(clock(391)))


def test_atomic_producer_worker_and_restart_preserve_frozen_inputs_after_mutable_revisions(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.setenv("PAPER_HISTORICAL_CANDLE_SHADOW", "OFF")
    store, assets = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path/"shadow", source_roots=[])
    worker.tick(PRE)
    at = datetime(2026, 10, 5, 15, 50, tzinfo=timezone.utc)
    native = [at]
    def native_clock():
        native[0] += timedelta(microseconds=1)
        return native[0].isoformat(timespec="microseconds")
    broker = PaperBroker(store, initial_cash="10000000", risk_pct=".005", participation=".1",
        max_position_pct="1", max_total_exposure_pct="1", clock_fn=native_clock,
        session_policy=PaperSessionPolicy(), require_supervisor=False, ai_mode="OFF",
        economics_mode="SHADOW", signal_min_samples=8)
    for index, price in enumerate(("100", "101", "100.5", "102", "101.5", "103", "103.5", "104")):
        when = at+timedelta(minutes=index+1)
        book = quote(assets[0], when, bid=price, ask=str(Decimal(price)+Decimal(".1")))
        store.add_quote(book)
        native[0] = when
        if index == 7:
            broker.on_quote(book)
        else:
            worker.tick(when+timedelta(seconds=1))
    with store.connect() as connection:
        position = dict(connection.execute("SELECT paper_id,opened_at,features_json FROM paper_positions").fetchone())
        snapshots = [dict(row) for row in connection.execute("SELECT decision_key,captured_at,payload_sha256,payload_json FROM decision_evidence_snapshots")]
        snapshot = next(row for row in snapshots if json.loads(row["payload_json"]).get("capture_phase") == "NATIVE_DECISION")
        atomic = next(row for row in snapshots if json.loads(row["payload_json"]).get("capture_phase") == "ATOMIC_PAPER_ADMISSION")
        payload = json.loads(snapshot["payload_json"])
        receipt = json.loads(atomic["payload_json"])
        stages = [stamp(payload[name]) for name in ("signal_at", "decision_at", "intent_at", "entry_fill_committed_at")]
        assert stages == sorted(stages) and len(set(stages)) == 4
        assert stamp(payload["intent_at"]) < stamp(receipt["entry_fill_recorded_at"]) < stamp(payload["entry_fill_committed_at"])
        assert stamp(receipt["admission_at"]) == stamp(position["opened_at"])
        assert receipt["entry_fill_committed_at"] is None
        assert all(stamp(sample["known_at"]) <= stamp(payload["signal_at"])
                   for sample in payload["inputs_used"]["entry_signal_inputs"]["price_samples"])
        mutable = json.loads(position["features_json"])
        mutable.update(native_decision_key="OTHER_TRADE", contract_cash_multiplier="999",
                       execution_style="SCALPING_PAPER", exit_policy={"mode": "LIVE"})
        connection.execute("UPDATE paper_positions SET features_json=?", (canonical(mutable),))
        connection.execute("UPDATE market_snapshots SET last='888'")
    before = dump(store.path)
    report = worker.tick(when+timedelta(seconds=2))
    lab = report["economic_exit_lab"]
    assert lab["new_registrations"] == 1 and len(lab["entries"]) == 1, lab
    entry = lab["entries"][0]
    assert entry["entry"]["paper_id"] == position["paper_id"]
    assert entry["native_decision_key"] == snapshot["decision_key"]
    assert entry["entry_evidence_sha256"] == snapshot["payload_sha256"]
    assert entry["signal_input_sha256"] == digest(payload["inputs_used"]["entry_signal_inputs"])
    assert entry["signal_vector_status"] == "NATIVE_EXACT_VECTOR"
    assert entry["financial_admission_snapshot_key"] == atomic["decision_key"]
    assert entry["financial_admission_snapshot_hash"] == atomic["payload_sha256"]
    assert entry["entry"]["contract_cash_multiplier"] == "1"
    assert dump(store.path) == before
    restarted = ShadowRuntime(store.path, evidence_root=tmp_path/"shadow", source_roots=[])
    again = restarted.tick(when+timedelta(seconds=3))
    assert again["checkpoint_reused"]
    preserved = again["economic_exit_lab"]["entries"][0]
    for field in ("entry", "baseline", "entry_evidence_sha256", "native_decision_key",
                  "native_clocks", "signal_input_sha256", "evidence_captured_at"):
        assert preserved[field] == entry[field]
    with store.connect() as connection:
        assert [dict(row) for row in connection.execute("SELECT decision_key,captured_at,payload_sha256,payload_json FROM decision_evidence_snapshots")] == snapshots
    assert dump(store.path) == before
    assert again["provider_requests"] == again["real_orders_sent"] == 0


def test_old_lab_checkpoint_is_invalidated_at_current_tail_without_reconstructing_entries(tmp_path):
    path = source(tmp_path)
    _, checkpoint = tick(path, clock(0))
    old = deepcopy(checkpoint)
    old["schema"] = "rc6.runtime-shadow-lab.v1"
    old["checkpoint_sha256"] = digest({key: value for key, value in old.items() if key != "checkpoint_sha256"})
    add_entry(path)
    before = dump(path)
    report, current = tick(path, clock(391), old)
    assert report["checkpoint_invalidation"] == "CHECKPOINT_SCHEMA_OR_DIGEST_INVALID"
    assert report["status"] == "START_AT_CURRENT_TAIL" and report["entries"] == []
    assert current["schema"] == "rc6.runtime-shadow-lab.v2"
    assert dump(path) == before
