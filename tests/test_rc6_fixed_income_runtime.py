"""Synthetic reviewed bundles only; no issuer/OOS or live readiness claims."""
import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from be_paper_engine import PaperBroker, PaperStore
from rc6_fixed_income_runtime import (
    FixedIncomeEvidenceError, MANIFEST_SCHEMA, RECEIPT_SCHEMA,
    contract_economics, fixed_income_strategy_inputs, quote_sha256,
)
from test_rc6_fixed_income_paper_strategy import fixture, AT, YESTERDAY


def dump(root, name, value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    (root / name).write_bytes(raw)
    return {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}


def reviewed_bundle(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    quote, _, terms, validation = fixture("OBLIGACIONES", symbol="SYNTHETIC_ON")
    quote = replace(quote, metadata_source="PPI_CATALOG:SYNTHETIC_TEST_ONLY",
                    last_kind="TRADE")
    issuer = dump(root, "issuer.bin", b"SYNTHETIC issuer document, not a market term")
    artifact = dump(root, "oos.bin", b"SYNTHETIC independent OOS, not a real result")
    dataset = dump(root, "dataset.bin", b"SYNTHETIC OOS dataset, not real trades")
    terms.update(source_sha256=issuer["sha256"], minimum_scenario_net_bps="100")
    validation.update(artifact_sha256=artifact["sha256"], dataset_sha256=dataset["sha256"])
    flow = {"obligation_id": "SYNTHETIC_COUPON", "kind": "COUPON",
            "due_at": (AT + timedelta(hours=1)).isoformat(),
            "cash_per_vn": "0.05", "payment_currency": "ARS",
            "source_reference": terms["source_reference"],
            "source_sha256": issuer["sha256"],
            "source_at": YESTERDAY.isoformat(), "known_at": YESTERDAY.isoformat()}
    receipt = {"schema": RECEIPT_SCHEMA, "identity": list(quote.contract.key),
               "known_at": YESTERDAY.isoformat(),
               "valid_until": (AT + timedelta(days=1)).isoformat(),
               "contract_economics": contract_economics(quote.contract),
               "terms": dump(root, "terms.json", terms),
               "validation": dump(root, "validation.json", validation),
               "issuer_document": issuer, "oos_artifact": artifact,
               "oos_dataset": dataset, "flows": [flow],
               "quantity_vn": "100", "market_exit_horizon_minutes": 120,
               "risk_limits": {"cash_available": "100000", "max_allocation": "1000",
                               "max_loss_budget": "3", "stress_bid_drop": "0.01",
                               "max_spread_bps": "50", "max_holding_days": 1,
                               "participation": "0.10", "slippage_fraction": "0.0002"}}
    manifest = {"schema": MANIFEST_SCHEMA, "paper_only": True, "real_routes_used": [],
                "review_authority": "RELEASE_REVIEWED_OFFICIAL_AND_OOS",
                "known_at": YESTERDAY.isoformat(),
                "valid_until": (AT + timedelta(days=1)).isoformat(),
                "receipts": [{"identity": list(quote.contract.key),
                              "proof": dump(root, "receipt.json", receipt)}]}
    pin = dump(root, "manifest.json", manifest)["sha256"]
    return quote, root, pin


def repin(root, name, mutate):
    value = json.loads((root / name).read_text())
    mutate(value)
    proof = dump(root, name, value)
    if name == "manifest.json":
        return proof["sha256"]
    if name in {"terms.json", "validation.json"}:
        receipt = json.loads((root / "receipt.json").read_text())
        receipt[name.removesuffix(".json")] = proof
        proof = dump(root, "receipt.json", receipt)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["receipts"][0]["proof"] = proof
    return dump(root, "manifest.json", manifest)["sha256"]


def load(quote, root, pin, *, at=AT):
    return fixed_income_strategy_inputs(SimpleNamespace(path=str(root / "unused.sqlite")),
                                        quote, at.isoformat(), directory=root,
                                        manifest_sha256=pin)


def no_event_bundle(tmp_path):
    quote, root, _ = reviewed_bundle(tmp_path)
    terms = json.loads((root / "terms.json").read_text())
    issuer_sha = hashlib.sha256((root / "issuer.bin").read_bytes()).hexdigest()
    next_event = {
        "identity": list(quote.contract.key), "obligation_id": "NEXT-COUPON",
        "kind": "COUPON", "due_on": "2027-03-30", "payment_currency": "USD",
        "source_reference": terms["source_reference"], "source_sha256": issuer_sha,
        "source_at": terms["source_at"], "known_at": terms["known_at"],
        "review_authority": "RELEASE_REVIEWED_ISSUER_NEXT_EVENT",
    }
    pin = repin(root, "receipt.json",
                lambda d: d.update(flows=[], next_event=next_event))
    return quote, root, pin


def test_release_pinned_next_event_wires_empty_window_without_cashflow_or_buy(tmp_path, monkeypatch):
    quote, root, pin = no_event_bundle(tmp_path)
    result = load(quote, root, pin)
    scenario = result["scenario"]
    assert scenario.status == "SHADOW_HYPOTHESIS"
    assert scenario.signal == "NONPOSITIVE_SCENARIO"
    assert scenario.projected_flows == scenario.projected_obligations == ()
    assert scenario.next_economic_event[:4] == (
        "NEXT-COUPON", "COUPON", "2027-03-30", "USD")
    assert scenario.scenario_net < 0
    assert scenario.entry_authority is False and scenario.real_routes_used == ()
    assert result["runtime_evidence"]["native_event_cash_lifecycle"] == "NO_VERIFICADO"
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    broker = PaperBroker(store, daily_loss_pct=None)
    monkeypatch.setenv("POROTA_FI_EVIDENCE_DIRECTORY", str(root))
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", pin)
    decision = broker._decide_fixed_income(quote, AT.isoformat())
    assert decision[0] == "HOLD"
    assert decision[3]["reason_code"] == "FI_SCENARIO_NOT_INDEPENDENT_ENTRY_PROOF"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


@pytest.mark.parametrize("mutation,error", [
    (lambda d: d.pop("next_event"), "FLOW_SET_INVALID"),
    (lambda d: d["next_event"].update(review_authority="SELF_ASSERTED"),
     "NEXT_EVENT_REVIEW_INVALID"),
    (lambda d: d["next_event"].update(source_sha256="a"*64),
     "NEXT_EVENT_PROVENANCE_MISMATCH"),
    (lambda d: d["next_event"].update(source_reference="BROKER_ANALYTICS"),
     "NEXT_EVENT_PROVENANCE_MISMATCH"),
    (lambda d: d["next_event"].update(identity=["OTHER", "OBLIGACIONES", "BYMA", "ARS", "A-24HS"]),
     "NEXT_EVENT_REVIEW_INVALID"),
    (lambda d: d["next_event"].update(known_at=(AT+timedelta(seconds=1)).isoformat()),
     "NEXT_EVENT_REVIEW_CLOCK_INVALID"),
    (lambda d: d.update(valid_until=(AT+timedelta(hours=1)).isoformat()),
     "NEXT_EVENT_REVIEW_WINDOW_EXPIRED"),
    (lambda d: d["next_event"].update(due_on=AT.date().isoformat()),
     "DUE_EVENT_OMITTED"),
    (lambda d: d["next_event"].update(due_on=(AT+timedelta(hours=2)).date().isoformat()),
     "DUE_EVENT_OMITTED"),
    (lambda d: d["next_event"].update(payment_currency="INVALID"),
     "INPUT_INVALID"),
    (lambda d: d.update(flows=[{"obligation_id": "DUP", "kind": "COUPON",
                                "due_at": (AT+timedelta(hours=1)).isoformat(),
                                "cash_per_vn": "0.05", "payment_currency": "ARS",
                                "source_reference": "fixture:independent-issuer-terms",
                                "source_sha256": "a"*64, "source_at": YESTERDAY.isoformat(),
                                "known_at": YESTERDAY.isoformat()}]),
     "NEXT_EVENT_CONFLICT"),
])
def test_next_event_cannot_hide_due_cash_or_bypass_source_release_and_identity(
        tmp_path, mutation, error):
    quote, root, _ = no_event_bundle(tmp_path)
    pin = repin(root, "receipt.json", mutation)
    with pytest.raises(FixedIncomeEvidenceError, match="^FI_EVIDENCE_" + error + "$"):
        load(quote, root, pin)


def test_unrepinned_next_event_byte_mutation_is_rejected(tmp_path):
    quote, root, pin = no_event_bundle(tmp_path)
    receipt = root / "receipt.json"
    receipt.write_bytes(receipt.read_bytes() + b" ")
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_HASH_MISMATCH"):
        load(quote, root, pin)


def test_missing_release_pin_never_reads_or_creates_evidence(tmp_path, monkeypatch):
    quote, *_ = fixture()
    monkeypatch.delenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", raising=False)
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_RELEASE_PIN_MISSING"):
        fixed_income_strategy_inputs(SimpleNamespace(path=str(tmp_path / "paper.sqlite")),
                                     quote, AT.isoformat())
    assert list(tmp_path.iterdir()) == []


def test_reviewed_static_bundle_binds_new_native_quotes_without_repinning(tmp_path):
    quote, root, pin = reviewed_bundle(tmp_path)
    first = load(quote, root, pin)
    later = AT + timedelta(minutes=10)
    changed = replace(quote, last=Decimal("100.01"),
                      observed_at=later.isoformat(), book_at=later.isoformat(),
                      trade_at=later.isoformat())
    second = load(changed, root, pin, at=later)
    assert first["scenario"].status == second["scenario"].status == "SHADOW_HYPOTHESIS"
    assert first["scenario"].signal == "POSITIVE_SCENARIO"
    assert first["runtime_evidence"]["manifest_sha256"] == pin
    assert second["runtime_evidence"]["manifest_sha256"] == pin
    assert first["runtime_evidence"]["quote_sha256"] != second["runtime_evidence"]["quote_sha256"]
    assert second["runtime_evidence"]["quote_sha256"] == quote_sha256(changed)
    assert second["scenario"].decision_at == later.isoformat()
    assert second["scenario"].planned_exit_at == (later + timedelta(hours=2)).isoformat()
    assert second["source_kind"] == "PPI_PRIMARY"
    assert second["scenario"].entry_authority is False


@pytest.mark.parametrize("file", ["manifest.json", "receipt.json", "terms.json",
                                 "validation.json", "issuer.bin", "oos.bin", "dataset.bin"])
def test_any_tampered_reviewed_byte_fails_closed(tmp_path, file):
    quote, root, pin = reviewed_bundle(tmp_path)
    with (root / file).open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_HASH_MISMATCH"):
        load(quote, root, pin)


@pytest.mark.parametrize("filename,mutation,error", [
    ("manifest.json", lambda d: d.update(paper_only=False), "RELEASE_ATTESTATION_INVALID"),
    ("manifest.json", lambda d: d["receipts"].append(d["receipts"][0]), "IDENTITY_AMBIGUOUS"),
    ("manifest.json", lambda d: d.update(valid_until=AT.isoformat()), "NOT_YET_KNOWN_OR_EXPIRED"),
    ("receipt.json", lambda d: d.update(known_at=(AT+timedelta(seconds=1)).isoformat()), "NOT_YET_KNOWN_OR_EXPIRED"),
    ("receipt.json", lambda d: d["identity"].__setitem__(3, "USD"), "RECEIPT_IDENTITY_MISMATCH"),
    ("receipt.json", lambda d: d["contract_economics"].update(cash_multiplier="1"), "CONTRACT_ECONOMICS_MISMATCH"),
    ("receipt.json", lambda d: d["terms"].update(file="../terms.json"), "PATH_INVALID"),
    ("receipt.json", lambda d: d["flows"][0].update(source_sha256="a"*64), "FLOW_PROVENANCE_MISMATCH"),
    ("receipt.json", lambda d: d["flows"].append(d["flows"][0]), "FLOW_OBLIGATION_DUPLICATE"),
    ("receipt.json", lambda d: d.update(market_exit_horizon_minutes=True), "EXIT_HORIZON_INVALID"),
    ("receipt.json", lambda d: d["flows"][0].update(due_at=(AT+timedelta(days=30)).isoformat()), "NO_FLOWS_IN_HOLDING_HORIZON"),
    ("terms.json", lambda d: d.update(source_authority="IOL_MCP"), "SOURCE_OR_OOS_ATTESTATION_INVALID"),
    ("validation.json", lambda d: d.update(artifact_sha256="a"*64), "REFERENCED_DOCUMENT_MISMATCH"),
])
def test_reapproved_malformed_or_contradictory_bundle_is_still_rejected(tmp_path, filename, mutation, error):
    quote, root, _ = reviewed_bundle(tmp_path)
    pin = repin(root, filename, mutation)
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_" + error):
        load(quote, root, pin)


def test_symlink_source_file_is_never_followed(tmp_path):
    quote, root, pin = reviewed_bundle(tmp_path)
    (root / "issuer.bin").rename(tmp_path / "outside.bin")
    (root / "issuer.bin").symlink_to(tmp_path / "outside.bin")
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_FILE_UNAVAILABLE"):
        load(quote, root, pin)


@pytest.mark.parametrize("change,error", [
    ({"metadata_source": "IOL_MCP"}, "NATIVE_PPI_PROVENANCE_REQUIRED"),
    ({"book_at": YESTERDAY.isoformat()}, "NATIVE_QUOTE_CLOCK_INVALID"),
    ({"trade_at": (AT+timedelta(seconds=1)).isoformat()}, "NATIVE_QUOTE_CLOCK_INVALID"),
    ({"contract": None}, "EXACT_CONTRACT_REQUIRED"),
])
def test_native_provenance_and_source_clocks_are_not_invented(tmp_path, change, error):
    quote, root, pin = reviewed_bundle(tmp_path)
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_"+error):
        load(replace(quote, **change), root, pin)


def test_engine_reports_precise_input_blocker_then_keeps_event_exit_unbound(tmp_path, monkeypatch):
    quote, root, pin = reviewed_bundle(tmp_path)
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    broker = PaperBroker(store, daily_loss_pct=None)
    monkeypatch.delenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", raising=False)
    missing = broker._decide_fixed_income(quote, AT.isoformat())
    assert missing[0] == "HOLD" and missing[3]["reason_code"] == "FI_EVIDENCE_RELEASE_PIN_MISSING"
    monkeypatch.setenv("POROTA_FI_EVIDENCE_DIRECTORY", str(root))
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", pin)
    calls = []
    original = store.fixed_income_strategy_inputs
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(store, "fixed_income_strategy_inputs", counted)
    result = broker._decide_fixed_income(quote, AT.isoformat())
    assert len(calls) == 1
    assert result[0] == "HOLD"
    assert result[3]["reason_code"] == "FI_NATIVE_EVENT_EXIT_ENGINE_NOT_BOUND"
    assert result[3]["fixed_income_runtime_evidence"]["manifest_sha256"] == pin
    # The A456 engine now has a narrowly bound LETRAS maturity callback.
    # It must still reject this ON MARKET_EXIT plan even with valid issuer/OOS
    # research inputs: no native market-sale admission is inferred.
    assert callable(getattr(broker, "fixed_income_native_exit_authorized", None))
    assert broker.fixed_income_native_exit_authorized(
        quote, {"mode": "FIXED_INCOME_EVENT_DRIVEN_MARKET_EXIT"}, AT.isoformat()) is False
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_duplicate_json_keys_are_rejected_even_with_matching_digest(tmp_path):
    quote, root, _ = reviewed_bundle(tmp_path)
    raw = (root / "manifest.json").read_bytes()
    raw = b'{"schema":"evil",' + raw[1:]
    pin = dump(root, "manifest.json", raw)["sha256"]
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_DUPLICATE_JSON_KEY"):
        load(quote, root, pin)
