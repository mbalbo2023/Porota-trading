"""Synthetic reviewed bundles only; no issuer/OOS or live readiness claims."""
import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Event
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from be_paper_engine import PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor
from bq_exit_policy import PaperSessionPolicy
from bv_paper_runtime import _collect_iol_exit_round
from rc6_fixed_income_event_ledger import snapshot_at
from rc6_fixed_income_iol_evidence import (
    ECONOMICS_AUTHORITY, ECONOMICS_SCHEMA, POLICY_PIN, POLICY_SCHEMA,
    iol_entry_market_evidence, iol_reviewed_policy,
)
from rc6_fixed_income_runtime import (
    FixedIncomeEvidenceError, MANIFEST_SCHEMA, RECEIPT_SCHEMA,
    contract_economics, fixed_income_strategy_inputs, quote_sha256,
)
from rc6_fixed_income_sale_authority import attest_market_sale_book2, sale_release_anchor
from rc6_fixed_income_sale_witness import ExitReaderSaleWitness, read_witness
from rc6_iol_market_gateway import IOLGatewayMarketReader
import rc6_iol_market_gateway as gateway
import rc6_paper_causal_decision_receipt as causal
from test_rc6_fixed_income_paper_strategy import fixture, AT, YESTERDAY
from test_rc6_iol_migration_offhost import open_fixture_iol_gateway, register


def dump(root, name, value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    (root / name).write_bytes(raw)
    return {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}


def reviewed_bundle(tmp_path, family="OBLIGACIONES", *, settlement=None, symbol=None):
    root = tmp_path / "evidence"
    root.mkdir()
    symbols = {"BONOS": "SYNTHETIC_BOND", "LETRAS": "SYNTHETIC_LETRA",
               "OBLIGACIONES": "SYNTHETIC_ON"}
    quote, _, terms, validation = fixture(family, symbol=symbol or symbols[family])
    quote = replace(quote, metadata_source="PPI_CATALOG:SYNTHETIC_TEST_ONLY",
                    last_kind="TRADE")
    if settlement is not None:
        quote = replace(quote, settlement=settlement,
                        contract=replace(quote.contract, settlement=settlement))
        terms["identity"] = validation["identity"] = quote.contract.key
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
    loss_budget = "3"
    if family == "LETRAS":
        maturity = AT + timedelta(days=1)
        terms.update(native_maturity_redemption_supported=True, maturity_at=maturity.isoformat(),
                     ex_at=(AT + timedelta(hours=2)).isoformat(),
                     record_at=(AT + timedelta(hours=3)).isoformat())
        flow.update(obligation_id="SYNTHETIC_MATURITY", kind="AMORTIZATION",
                    due_at=maturity.isoformat(), cash_per_vn="1.05")
        loss_budget = "110"
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
                               "max_loss_budget": loss_budget, "stress_bid_drop": "0.01",
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


@pytest.mark.parametrize("family", ["BONOS", "OBLIGACIONES"])
def test_engine_reports_precise_input_blocker_then_keeps_event_exit_unbound(tmp_path, monkeypatch, family):
    quote, root, pin = reviewed_bundle(tmp_path, family)
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
    assert broker.fixed_income_native_exit_authorized(quote, {}, AT.isoformat()) is False
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def letter_broker(tmp_path, monkeypatch, *, settlement="INMEDIATA", symbol=None, **broker_config):
    quote, root, pin = reviewed_bundle(tmp_path, "LETRAS", settlement=settlement, symbol=symbol)
    monkeypatch.setenv("POROTA_FI_EVIDENCE_DIRECTORY", str(root))
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", pin)
    sector_map = tmp_path / "reviewed-sectors.csv"
    sector_map.write_text(
        "ticker,family,market,currency,settlement,sector,source,author,effective_at,reviewed\n"
        + ",".join((*quote.contract.key, "SYNTHETIC_SOVEREIGN", "fixture:reviewed-sector",
                    "SYNTHETIC_TEST", YESTERDAY.isoformat(), "true")) + "\n",
        encoding="utf-8")
    monkeypatch.setenv("POROTA_SECTOR_MAP_PATH", str(sector_map))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")
    return quote, root, PaperBroker(PaperStore(str(tmp_path / "paper.sqlite")),
                                    economics_mode="BINDING", **broker_config)


def test_reviewed_letter_opens_exact_vn_and_closes_only_after_the_matched_payment(tmp_path, monkeypatch):
    quote, evidence, broker = letter_broker(tmp_path, monkeypatch, daily_soft_stop_pct="0.015")
    assert_reviewed_letter_roundtrip(quote, evidence, broker)


def assert_reviewed_letter_roundtrip(quote, evidence, broker, *, fresh_quote_at=None):
    from bm_exit_supervisor import PositionExitSupervisor
    from dh_paper_dynamic_risk_gate_hf6 import open_stop_risk, portfolio_capacity
    from rc6_fixed_income_native_redemption import normalized_issuer_schedule_sha256
    from rc6_fixed_income_portfolio_bridge import position_at
    from tests.test_rc6_fixed_income_native_redemption import NativeRedemptionReviewTest

    quote = replace(quote, bid_size=Decimal(1))  # No sale lot is needed for a redemption.
    store = broker.store
    # The scanner persists the exact executable quote before broker admission.
    store.add_quote(quote)
    broker.on_quote(quote)
    broker.on_quote(quote)  # Same BUY can only be committed once.
    position, = store.open_positions()
    assert position["symbol"] == quote.symbol
    paper_id = position["paper_id"]
    features = json.loads(position["features_json"])
    plan = features["fixed_income_exit_plan"]
    outlay = Decimal(position["entry_price"]) * quote.contract.cash_multiplier * 100 + Decimal(position["entry_cost"])
    assert Decimal(position["quantity"]) == Decimal(plan["quantity_vn"]) == 100
    assert features["exit_policy"]["market_sale"] is False
    assert Decimal(features["candidate_stop_risk"]) == outlay
    assert Decimal(features["economics"]["full_principal_risk"]) == outlay
    assert features["economics"]["modeled_tariff"] == "FULL_ENTRY_LEG_ONLY"
    cost_receipt = features["economics"]["cost_contract"]
    if quote.metadata_source == "IOL_REST":
        assert cost_receipt["market_data"]["identity"] == list(quote.contract.key)
        assert cost_receipt["schedule_sha256"] == plan["fee_policy_sha256"]
    else:
        assert cost_receipt["policy_sha256"] == plan["fee_policy_sha256"]
    assert not broker._close(position, quote, "SYNTHETIC_SELL_IS_BLOCKED")
    with store.connect() as db:
        assert position_at(db, paper_id, AT).residual_nominal == 100
        assert open_stop_risk(broker, "ARS", AT, connection=db) == outlay
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 0

    restarted = PaperBroker(PaperStore(store.path), daily_soft_stop_pct="0.015")
    next_at = AT + timedelta(minutes=5)
    next_stamp = next_at.isoformat()
    next_quote = (fresh_quote_at(next_at) if fresh_quote_at else replace(
        quote, observed_at=next_stamp, book_at=next_stamp, trade_at=next_stamp))
    capacity = portfolio_capacity(restarted, "ARS", next_at, candidate_risk=outlay,
                                  quotes={quote.symbol: next_quote})
    assert capacity.open_stop_risk == outlay
    assert capacity.soft_stop_budget >= outlay > capacity.remaining_before_candidate
    assert not capacity.admitted and capacity.reason == "CANDIDATE_EXCEEDS_REMAINING_CONCURRENT_RISK"

    at = AT + timedelta(days=1, minutes=5)
    supervisor = PositionExitSupervisor(broker, clock_fn=lambda: at.isoformat())
    assert supervisor.supervise(position, None, at.isoformat()).reason == "FI_MATURITY_OFFICIAL_EVENT_RECEIPT_PENDING"

    fixture = NativeRedemptionReviewTest()
    fixture.store, fixture.family = store, "LETRAS"
    identity = list(quote.contract.key)
    economic = fixture._economic_receipt()
    economic["identity"] = identity
    economic["event"].update(paper_id=paper_id, obligation_id=plan["reviewed_obligation_id"],
                             effective_at=plan["exit_at"], recorded_at=at.isoformat(),
                             source_at=at.isoformat(), cash_amount=plan["redemption_cash"])
    economic["cash_per_nominal"] = "1.05"
    economic["entitlement"].update(ex_at=plan["ex_at"], record_at=plan["record_at"],
                                   known_at=at.isoformat(), paper_settled_at=position["opened_at"],
                                   settlement_basis="PAPER_CI_AT_TRADE")
    review = fixture._review()
    review.update(paper_id=paper_id, identity=identity,
                  paid_obligation_ids=[plan["reviewed_obligation_id"]], final_principal_cash="105",
                  expected_principal_by_currency=[["ARS", "105"]],
                  expected_cash_by_currency=[["ARS", "105"]])
    schedule = review["issuer_schedule"]
    schedule.update(identity=identity, maturity_at=plan["exit_at"],
                    source_sha256=plan["issuer_source_sha256"],
                    source_reference=plan["issuer_source_reference"],
                    final_obligation_id=plan["reviewed_obligation_id"])
    schedule["obligations"] = [{key: economic["event"][key] for key in schedule["obligations"][0]}]
    economic["event"]["obligation_id"] = "DIFFERENT_OFFICIAL_OBLIGATION"
    with pytest.raises(ValueError, match="FI_LEDGER_OPENING_OBLIGATION_MISMATCH"):
        fixture._book(economic=economic, at=at.isoformat())
    economic["event"]["obligation_id"] = plan["reviewed_obligation_id"]
    fixture._book(economic=economic, at=at.isoformat())
    fixture._book(economic=economic, at=at.isoformat())
    assert supervisor.supervise(position, None, at.isoformat()).state == "OPEN"

    fixture._apply(review, issuer_bytes=(evidence / "issuer.bin").read_bytes(),
                   approved_issuer_sha256=plan["issuer_source_sha256"],
                   approved_schedule_sha256=normalized_issuer_schedule_sha256(schedule),
                   at=at.isoformat())
    before = broker._cash(as_of=at.isoformat(), currency="ARS")
    assert supervisor.supervise(position, None, at.isoformat()).state == "CLOSED"
    assert broker._cash(as_of=at.isoformat(), currency="ARS") == before == 1_000_000 - outlay + 105
    assert not store.open_positions()
    with store.connect() as db:
        assert open_stop_risk(broker, "ARS", at, connection=db) == 0
        assert [row[0] for row in db.execute("SELECT side FROM paper_fills")] == ["BUY_SIMULATED"]
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0] == 1
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state").fetchone()) == (
            "PRODUCTION_PAPER", 0)


def test_letter_needs_documented_entitlement_and_the_entire_reviewed_risk_lot(tmp_path, monkeypatch):
    quote, root, broker = letter_broker(tmp_path, monkeypatch, settlement="A-24HS")
    action, _, _, features = broker.decide(quote)
    assert action == "HOLD"
    assert features["reason_code"] == "FI_NATIVE_MATURITY_ENTITLEMENT_UNVERIFIED"
    maturity_at = (AT + timedelta(days=4)).isoformat()
    repin(root, "terms.json", lambda terms: terms.update(
        ex_at=(AT + timedelta(days=2, hours=1)).isoformat(),
        record_at=(AT + timedelta(days=2, hours=2)).isoformat(), maturity_at=maturity_at))
    def move_maturity(receipt):
        receipt["flows"][0]["due_at"] = maturity_at
        receipt["risk_limits"]["max_holding_days"] = 4
    pin = repin(root, "receipt.json", move_maturity)
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", pin)
    assert broker.decide(quote)[0] == "BUY"
    ci_path = tmp_path / "ci"
    ci_path.mkdir()
    quote, _, broker = letter_broker(ci_path, monkeypatch, risk_pct="0.00005")
    action, score, _, features = broker.decide(quote)
    assert action == "BUY"
    assert broker._open(quote, score, features) == (
        False, "FI_NATIVE_MATURITY_REVIEWED_LOT_EXCEEDS_CAPACITY", None)
    assert not broker.store.open_positions()


def test_letter_candidate_cannot_change_its_reviewed_plan_before_fill(tmp_path, monkeypatch):
    quote, _, broker = letter_broker(tmp_path, monkeypatch)
    action, score, reason, features = broker.decide(quote)
    assert action == "BUY"
    identity = ":".join((quote.symbol, quote.asset_class, quote.settlement, quote.currency, quote.market))
    key = f"{features['strategy']}:{identity}:{quote.observed_at[:16]}:BUY"
    features["native_decision_key"] = key
    assert broker.store.record_decision(key, quote, action, score, reason, features)
    features["fixed_income_exit_plan"]["redemption_cash"] = "106"
    assert broker._open(quote, score, features) == (False, "FI_NATIVE_SIGNAL_CHANGED_BEFORE_ADMISSION", None)
    assert not broker.store.open_positions()
    with broker.store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_letter_opening_ledger_failure_rolls_back_trade_and_nominal_together(tmp_path, monkeypatch):
    quote, _, broker = letter_broker(tmp_path, monkeypatch)
    with broker.store.connect() as db:
        db.execute("""CREATE TRIGGER fail_reviewed_lot BEFORE INSERT ON paper_fi_lots
          BEGIN SELECT RAISE(ABORT,'SYNTHETIC_LOT_INSERT_FAILURE'); END""")
    broker.on_quote(quote)
    with broker.store.connect() as db:
        for table in ("paper_positions", "paper_fills", "paper_fi_lots", "paper_fi_cash_entries"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("""SELECT count(*) FROM trade_gate_evaluations
          WHERE reason='FI_NATIVE_MATURITY_OPENING_LEDGER_REJECTED'""").fetchone()[0] == 1


def test_letter_requires_healthy_exit_workers_and_existing_tables_before_any_debit(tmp_path, monkeypatch):
    from bm_exit_supervisor import PositionExitSupervisor

    quote, _, broker = letter_broker(tmp_path, monkeypatch, require_supervisor=True)
    at = AT.isoformat()
    action, score, _, features = broker._decide_fixed_income(quote, at)
    assert action == "BUY"
    assert broker._open(quote, score, features) == (False, "EXIT_SUPERVISOR_UNAVAILABLE", None)
    PositionExitSupervisor(broker, clock_fn=lambda: at).heartbeat(at)
    assert broker._open(quote, score, features) == (False, "EXIT_READER_UNAVAILABLE", None)
    with broker.store.connect() as db:
        db.execute("INSERT OR REPLACE INTO paper_exit_reader_state VALUES(1,?,'READY','SYNTHETIC_TEST')", (at,))
    assert broker.decide(quote)[0] == "BUY"
    with broker.store.connect() as db:
        db.execute("DROP TABLE paper_fi_native_redemption_reviews")
    broker.on_quote(quote)
    with broker.store.connect() as db:
        decision = db.execute("SELECT action, features_json FROM paper_decisions").fetchone()
        assert decision[0] == "HOLD"
        assert json.loads(decision[1])["reason_code"] == "FI_NATIVE_EVENT_EXIT_ENGINE_NOT_BOUND"
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fi_lots").fetchone()[0] == 0


def test_duplicate_json_keys_are_rejected_even_with_matching_digest(tmp_path):
    quote, root, _ = reviewed_bundle(tmp_path)
    raw = (root / "manifest.json").read_bytes()
    raw = b'{"schema":"evil",' + raw[1:]
    pin = dump(root, "manifest.json", raw)["sha256"]
    with pytest.raises(FixedIncomeEvidenceError, match="FI_EVIDENCE_DUPLICATE_JSON_KEY"):
        load(quote, root, pin)


def reviewed_market_units(monkeypatch, root, contract, *, units="VN"):
    build, tree = "a" * 40, "b" * 40
    release = sale_release_anchor(build, tree)
    monkeypatch.setenv("POROTA_BUILD_SHA", build)
    monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", tree)
    monkeypatch.setenv("POROTA_RC6_MARKET_PROVIDER", "IOL_REST")
    monkeypatch.setenv("POROTA_FI_EVIDENCE_DIRECTORY", str(root))
    provider_proof = b"Synthetic IOL documentation of depth and price units"
    source = {"schema": ECONOMICS_SCHEMA, "identity": list(contract.key),
              "provider": "IOL_REST", "price_basis": "CONTRACT_QUOTE_PRICE",
              "depth_size_unit": units, "order_quantity_unit": "VN",
              "provider_evidence_sha256": causal._sha(provider_proof),
              "known_at": YESTERDAY.isoformat(), "valid_until": (AT + timedelta(days=2)).isoformat()}
    source.update({field: str(getattr(contract, field)) for field in
                   ("cash_multiplier", "minimum_quantity", "quantity_step")})
    proof = dump(root, "units.json", source)
    (root / proof["file"]).rename(root / f"iol-economics-{proof['sha256']}.json")
    dump(root, f"iol-source-{causal._sha(provider_proof)}.bin", provider_proof)
    doc = {"schema": POLICY_SCHEMA, "source_authority": ECONOMICS_AUTHORITY,
           "paper_only": True, "real_routes_used": [], "release_sha256": release,
           "known_at": YESTERDAY.isoformat(), "valid_until": (AT + timedelta(days=2)).isoformat(),
           "instruments": [{"identity": list(contract.key), "economics_sha256": proof["sha256"]}]}
    monkeypatch.setenv(POLICY_PIN, dump(root, "iol-market-policy.json", doc)["sha256"])
    return release


def scanner_quote(server, client, transport, contract, *, at=AT):
    transport.now = at
    register(client, "scanner")
    trade = {"trade_at": (at - timedelta(seconds=2)).isoformat(),
             "received_at": (at - timedelta(seconds=1)).isoformat(),
             "price": "100", "shares": "100"}
    server.trade_stream = SimpleNamespace(state="SUBSCRIBED", want=lambda key: None,
                                         observation=lambda key: (trade, ""))
    reader = IOLGatewayMarketReader(client, consumer="scanner", clock=lambda: transport.now)
    return reader.observe(contract.key, contract=contract, priority="HOT").quote


@pytest.mark.parametrize(("family", "symbol"), [("BONOS", "AL30"), ("OBLIGACIONES", "YMCJO")])
def test_iol_buy_and_first_signed_post_ack_sell(tmp_path, tmp_path_factory, monkeypatch, family, symbol):
    fixture, root, manifest_pin = reviewed_bundle(tmp_path, family, settlement="INMEDIATA", symbol=symbol)
    contract = fixture.contract
    release = reviewed_market_units(monkeypatch, root, contract)
    sectors = tmp_path / "sectors.csv"
    sectors.write_text(
        "ticker,family,market,currency,settlement,sector,source,author,effective_at,reviewed\n"
        + ",".join((*contract.key, "SYNTHETIC", "fixture:sector", "SYNTHETIC",
                    YESTERDAY.isoformat(), "true")) + "\n")
    monkeypatch.setenv("POROTA_SECTOR_MAP_PATH", str(sectors))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")
    monkeypatch.setenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", manifest_pin)
    signer = Ed25519PrivateKey.generate()
    public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(causal, "PINNED_RELEASE_SHA256", release)
    monkeypatch.setattr(causal, "PINNED_DECISION_SIGNER_KEY_SHA256", causal._sha(public))

    store = PaperStore(str(tmp_path / "paper.sqlite"))
    with open_fixture_iol_gateway(tmp_path, tmp_path_factory, contract.key) as (server, client, transport, _):
        opening = scanner_quote(server, client, transport, contract)
        assert iol_entry_market_evidence(opening, contract.key, transport.now) == (
            opening.iol_native_rest_book["receipt_sha256"])
        broker = PaperBroker(store, initial_cash="1000000", clock_fn=lambda: transport.now.isoformat())
        store.add_quote(opening)
        broker.on_quote(opening)
        position, = store.open_positions()
        paper_id = position["paper_id"]
        assert len(transport.calls) == 4

        register(client, "exit_reader")
        exit_reader = IOLGatewayMarketReader(client, consumer="exit_reader", clock=lambda: transport.now)
        monkeypatch.setattr(gateway, "IOLGatewayClient", type(client))
        monkeypatch.setattr(gateway, "iol_gateway_socket_path", lambda _: server.socket_path)
        witness = ExitReaderSaleWitness(store, exit_signer=signer)
        supervisor = PositionExitSupervisor(broker, clock_fn=lambda: transport.now.isoformat())
        transport.now = AT + timedelta(hours=2)

        def round_status():
            return _collect_iol_exit_round(
                exit_reader, store, PaperSessionPolicy(), transport.now.isoformat(), Event(),
                beat=lambda: None, deadline_seconds=120, sale_witness=witness)[0]

        monkeypatch.setattr(causal, "_now", lambda: transport.now.isoformat())
        assert round_status() == "READY"
        book1_quote = store.latest_quote(position)
        assert book1_quote.iol_native_rest_book["consumer"] == "exit_reader"
        assert book1_quote.trade_at is None
        pending = supervisor.supervise(position, book1_quote, transport.now.isoformat())
        assert pending.reason == "FI_NATIVE_SIGNED_BOOK2_PENDING"
        transport.now += timedelta(milliseconds=10)
        ack_at = transport.now
        key = witness.acknowledge(paper_id)

        wire = len(transport.calls)
        with pytest.raises(ValueError, match="IOL_FI_BOOK2_NOT_YET_CAPTURED"):
            attest_market_sale_book2(store.path, decision_key=key, release=release, exit_signer=signer)
        assert len(transport.calls) == wire

        transport.now = ack_at + timedelta(milliseconds=500)
        early = exit_reader.observe(contract.key, contract=contract, priority="EXIT_CRITICAL", exit_only=True)
        assert early.quote.book_at < ack_at.isoformat()
        transport.now = ack_at + timedelta(seconds=2)
        transport.price = 101.0
        first = exit_reader.observe(contract.key, contract=contract, priority="EXIT_CRITICAL", exit_only=True)
        transport.now = ack_at + timedelta(seconds=3)
        transport.price = 110.0
        assert round_status() == "READY"
        proof = read_witness(store, key)
        assert proof["payload"]["book2"] == first.quote.iol_native_rest_book
        assert proof["payload"]["book2"]["receipt_sha256"] != early.quote.iol_native_rest_book["receipt_sha256"]
        detached = {**book1_quote.iol_native_rest_book, "receipt_sha256": "f" * 64}
        assert client.first_sale_book2(detached, cutoff_at=ack_at, cutoff_ns=1)["state"] == (
            "IOL_FI_BOOK1_NOT_RETAINED")
        assert transport.calls[4:] == ["token", *([symbol, "depth"] * 4)]

        final = supervisor.supervise(position, store.latest_quote(position), transport.now.isoformat())
        assert final.state == "CLOSED"
        assert not store.open_positions()
        with store.connect() as db:
            assert snapshot_at(db, paper_id, transport.now).residual_nominal == 0
            assert [row[0] for row in db.execute("SELECT side FROM paper_fills ORDER BY id")] == [
                "BUY_SIMULATED", "SELL_SIMULATED"]
            assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
            assert db.execute("SELECT event_json FROM paper_fi_economic_events").fetchone()[0].count(
                "IOL_LIVE_BOOK") == 1


def test_iol_letter_keeps_reviewed_maturity_and_refuses_unproven_units(tmp_path, tmp_path_factory, monkeypatch):
    opening, root, broker = letter_broker(tmp_path, monkeypatch, symbol="S30N6", daily_soft_stop_pct="0.015")
    contract = opening.contract
    reviewed_market_units(monkeypatch, root, contract, units="LOTS")
    with open_fixture_iol_gateway(tmp_path, tmp_path_factory, contract.key) as (server, client, transport, _):
        transport.overrides["depth"] = {"bids": [{"price": 99.9, "size": 1}]}
        quote = scanner_quote(server, client, transport, contract)
        error = broker._decide_fixed_income(quote, AT.isoformat())
        assert error[0] == "HOLD"
        assert error[3]["reason_code"] == "FI_IOL_PRICE_OR_DEPTH_UNITS_UNVERIFIED"
        assert not broker.store.open_positions()

        reviewed_market_units(monkeypatch, root, contract)
        assert iol_reviewed_policy(broker.store, contract.key, contract, AT)[0]["economic_policy_authority"] == (
            ECONOMICS_AUTHORITY)
        changed = {**quote.iol_trade_evidence, "gateway_generation": "c" * 64}
        mismatch = broker._decide_fixed_income(replace(quote, iol_trade_evidence=changed), AT.isoformat())
        assert mismatch[0] == "HOLD" and mismatch[3]["reason_code"] == "FI_IOL_ENTRY_TRADE_GENERATION_UNVERIFIED"
        stale = broker._decide_fixed_income(quote, (AT + timedelta(days=1)).isoformat())
        assert stale[0] == "HOLD" and stale[3]["reason_code"] == "FI_IOL_ENTRY_BOOK_OR_TRADE_STALE"
        assert_reviewed_letter_roundtrip(
            quote, root, broker, fresh_quote_at=lambda at: scanner_quote(server, client, transport, contract, at=at))
