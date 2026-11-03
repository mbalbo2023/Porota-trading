"""Fail-closed PAPER signal adapter for fixed-income financial families.

The existing immutable :mod:`rc6_fixed_income_strategy_shadow` computes a
point-in-time *scenario*, never a forecast or an entry authorization. This
adapter can consider that scenario only alongside independently authenticated
issuer terms, out-of-sample validation and a compatible native PAPER exit.

No data collection, broker route, network call, SQL mutation, fill or cashflow
booking occurs here. The owner of the PAPER store must provide a bounded,
read-only ``fixed_income_strategy_inputs`` producer and verify its receipts;
without that producer all decisions are HOLD.
"""
from __future__ import annotations

import os
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Mapping

from bs_instrument_contracts import aware_datetime, family_name
from rc6_fixed_income_strategy_shadow import ShadowScenario


PAPER_ONLY = True
REAL_ROUTES_USED = ()
STRATEGY_VERSION = "rc6-fixed-income-paper-signal-v1"
FAMILIES = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_VALID_BOOKS = frozenset({"PPI_PRIMARY", "IOL_MCP", "BYMA_PUBLIC"})
_MIN_OOS_OBSERVATIONS = 30
_ZERO = Decimal("0")
STRATEGY_IDS = {"BONOS": "FI_BONO_PAPER_V1",
                "LETRAS": "FI_LETRA_PAPER_V1",
                "OBLIGACIONES": "FI_ON_PAPER_V1"}


def _number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _hold(reason, family="", **facts):
    return ("HOLD", _ZERO, reason,
            {"family": family, "strategy_version": STRATEGY_VERSION,
             "strategy": STRATEGY_IDS.get(family, "FI_UNSUPPORTED_PAPER_V1"),
             "entry_authority": False,
             "signal_ready": False, "paper_only": True,
             "real_routes_used": [], "reason_code": reason, **facts})


def _proof_time(source, known, cutoff):
    try:
        source_at = aware_datetime(source)
        known_at = aware_datetime(known)
        return source_at <= known_at <= cutoff
    except (TypeError, ValueError, OverflowError):
        return False


def _age(value, at):
    try:
        return (at - aware_datetime(value)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None


def decide_fixed_income_from_inputs(quote, *, decision_at, scenario,
                                    terms, validation, native_exit_minutes=360):
    """Evaluate an externally evidenced, family-specific candidate.

    ``terms`` must be authenticated against an official issuer/contract source;
    ``validation`` must have been verified against an independent, SHA-bound
    out-of-sample artifact by its caller. Plain metadata or IOL's own pair of
    analytics/simulation calls is not independent issuer proof. The returned
    BUY is a *candidate signal*, still subject to the host's admission, risk,
    cash, order-unit and PAPER-only gates before any simulated position opens.

    A missing upstream producer is always HOLD. Supplying two arbitrary Python
    dictionaries is not itself issuer or OOS authentication: the upstream
    producer and deployment preflight own those trust boundaries.
    """
    try:
        family = family_name(quote.asset_class)
    except (ValueError, AttributeError):
        return _hold("FI_FAMILY_UNSUPPORTED")
    if family not in FAMILIES:
        return _hold("FI_FAMILY_UNSUPPORTED", family)
    try:
        at = aware_datetime(decision_at)
        key = (quote.symbol, family, quote.market, quote.currency,
               quote.settlement)
        if (quote.contract is None or quote.contract.key != key or
                quote.contract.minimum_quantity is None or
                quote.contract.cash_multiplier <= 0 or
                quote.contract.quantity_step <= 0):
            return _hold("FI_NOMINAL_CONTRACT_UNVERIFIED", family)
    except (ValueError, AttributeError, TypeError):
        return _hold("FI_NOMINAL_CONTRACT_UNVERIFIED", family)

    if not isinstance(terms, Mapping) or not isinstance(validation, Mapping):
        return _hold("FI_TERMS_OR_OOS_EVIDENCE_MISSING", family)
    if not isinstance(scenario, ShadowScenario):
        return _hold("FI_PIT_SCENARIO_MISSING", family)
    if (scenario.identity != key or scenario.status != "SHADOW_HYPOTHESIS" or
            scenario.signal != "POSITIVE_SCENARIO" or
            scenario.entry_cash is None or scenario.scenario_net is None or
            scenario.entry_cash <= 0 or scenario.scenario_net <= 0 or
            scenario.entry_authority is not False or scenario.real_routes_used or
            scenario.observed_book_source not in _VALID_BOOKS):
        return _hold("FI_SCENARIO_NOT_INDEPENDENT_ENTRY_PROOF", family)
    try:
        if aware_datetime(scenario.decision_at) != at:
            return _hold("FI_SCENARIO_CLOCK_MISMATCH", family)
        exit_at = aware_datetime(scenario.planned_exit_at)
        if exit_at <= at:
            return _hold("FI_EXIT_HORIZON_INVALID", family)
    except (ValueError, TypeError):
        return _hold("FI_SCENARIO_CLOCK_MISMATCH", family)

    # Both the PPI exact identity and the independently proved economic terms
    # must resolve to this SAME 5-tuple; ticker-only IOL matches cannot pass.
    if (tuple(terms.get("identity") or ()) != key or
            terms.get("source_authority") != "OFFICIAL_ISSUER_VERIFIED" or
            not isinstance(terms.get("source_reference"), str) or
            not terms["source_reference"].strip() or
            not _SHA256.fullmatch(str(terms.get("source_sha256") or "")) or
            not _proof_time(terms.get("source_at"), terms.get("known_at"), at) or
            terms.get("cashflows_verified") is not True or
            terms.get("price_basis_verified") is not True or
            terms.get("currency") != key[3]):
        return _hold("FI_OFFICIAL_TERMS_UNVERIFIED", family)
    # Official contract terms are stable, but a revised document known only
    # after the decision must never be read from the future.
    if tuple(validation.get("identity") or ()) != key or (
        validation.get("status") != "OOS_VERIFIED_BY_INDEPENDENT_PIPELINE" or
        validation.get("instrument_family") != family or
        validation.get("strategy_version") != STRATEGY_VERSION or
        validation.get("verification_is_independent") is not True or
        validation.get("artifact_attested_by_release") is not True or
        not _SHA256.fullmatch(str(validation.get("artifact_sha256") or "")) or
        not _SHA256.fullmatch(str(validation.get("dataset_sha256") or "")) or
        not _proof_time(validation.get("source_at"), validation.get("known_at"), at)
    ):
        return _hold("FI_OOS_INDEPENDENT_PROOF_MISSING", family)
    try:
        sample = validation.get("out_of_sample_closed_trades")
        if type(sample) is not int or sample < _MIN_OOS_OBSERVATIONS:
            return _hold("FI_OOS_SAMPLE_INSUFFICIENT", family)
        if (not aware_datetime(validation["train_until"]) <
                aware_datetime(validation["test_from"]) <=
                aware_datetime(validation["test_until"]) <=
                aware_datetime(validation["known_at"])):
            return _hold("FI_OOS_TEMPORAL_LEAKAGE", family)
        edge = _number(validation.get("net_after_cost_bps"))
        drawdown = _number(validation.get("max_drawdown_bps"))
        if edge is None or drawdown is None or edge <= 0 or drawdown < 0 or drawdown > 200:
            return _hold("FI_OOS_EDGE_OR_DRAWDOWN_RED", family)
    except (TypeError, ValueError, KeyError, OverflowError):
        return _hold("FI_OOS_TEMPORAL_LEAKAGE", family)

    gain_bps = scenario.scenario_net / scenario.entry_cash * Decimal(10000)
    min_gain_bps = _number(terms.get("minimum_scenario_net_bps"))
    if (min_gain_bps is None or min_gain_bps <= 0 or min_gain_bps > 2000 or
            gain_bps < min_gain_bps):
        return _hold("FI_NET_EDGE_AFTER_FEES_TOO_LOW", family)
    if (scenario.stress_loss is None or scenario.stress_loss < 0 or
            scenario.stress_loss > scenario.entry_cash):
        return _hold("FI_RISK_SCENARIO_UNVERIFIED", family)

    if scenario.exit_mode == "MARKET_EXIT":
        if (type(native_exit_minutes) is not int or native_exit_minutes <= 0 or
                exit_at - at > timedelta(minutes=native_exit_minutes) or
                terms.get("native_market_exit_supported") is not True or
                terms.get("native_exit_contract") !=
                "FIXED_INCOME_MARKET_EXIT_WITH_ECONOMIC_EVENT_LEDGER_V1" or
                terms.get("native_exit_proof_verified") is not True):
            return _hold("FI_NATIVE_EXIT_LIFECYCLE_MISMATCH", family)
    elif scenario.exit_mode == "MATURITY_REDEMPTION":
        # Current spot PAPER exit is time/price-based, not a VN amortization
        # lifecycle. Even an asserted receipt field is not native ledger
        # support: wait for a separate production-native owner and tests.
        return _hold("FI_NATIVE_MATURITY_LIFECYCLE_MISSING", family)
    else:
        return _hold("FI_EXIT_MODE_UNSUPPORTED", family)

    if family == "BONOS":
        duration = _number(terms.get("duration_years"))
        if (duration is None or not 0 < duration <= 5 or
                terms.get("coupon_calendar_verified") is not True or
                terms.get("amortization_calendar_verified") is not True or
                scenario.exit_mode != "MARKET_EXIT"):
            return _hold("FI_BOND_DURATION_OR_COUPON_EVIDENCE_RED", family)
        # Coupon income may not be projected as a guaranteed offset to a
        # stressed future bid. Require a bounded downside separately.
        if scenario.stress_loss > scenario.entry_cash * Decimal("0.05"):
            return _hold("FI_BOND_DOWNSIDE_BUDGET_RED", family)
    elif family == "LETRAS":
        # A floating CER/indexed future fixing is not a known face amount.
        if (terms.get("redemption_basis") != "FIXED_KNOWN_FACE" or
                terms.get("indexation") != "NONE" or
                terms.get("redemption_face_official") is not True or
                scenario.exit_mode != "MATURITY_REDEMPTION" or
                not _proof_time(terms.get("redemption_source_at"),
                                terms.get("redemption_known_at"), at) or
                terms.get("maturity_at") != exit_at.isoformat()):
            return _hold("FI_LETTER_FIXED_DISCOUNT_REDEMPTION_RED", family)
    elif family == "OBLIGACIONES":
        credit_stress = _number(terms.get("issuer_credit_stress_bps"))
        if (scenario.exit_mode != "MARKET_EXIT" or
                terms.get("issuer_credit_status") != "IN_GOOD_STANDING_VERIFIED" or
                terms.get("coupon_calendar_verified") is not True or
                not isinstance(terms.get("issuer_id"), str) or not terms["issuer_id"].strip() or
                credit_stress is None or credit_stress <= 0 or
                gain_bps <= credit_stress):
            return _hold("FI_ON_CREDIT_OR_CARRY_EVIDENCE_RED", family)
        if scenario.stress_loss > scenario.entry_cash * Decimal("0.03"):
            return _hold("FI_ON_DOWNSIDE_BUDGET_RED", family)

    # The score is a conservative, bounded quantitative candidate. It is not
    # an assertion that static-bid scenario return is an actual future PnL.
    score = min(Decimal(1), gain_bps / Decimal(10000))
    features = {"family": family, "strategy_version": STRATEGY_VERSION,
                "strategy": STRATEGY_IDS[family],
                "entry_authority": False,
                "signal_ready": True, "paper_only": True,
                "real_routes_used": [], "reason_code": "FI_PAPER_CANDIDATE_VALIDATED",
                "identity": list(key), "scenario_net_bps": str(gain_bps),
                "oos_net_after_cost_bps": str(edge),
                "oos_max_drawdown_bps": str(drawdown),
                "scenario_type": scenario.exit_mode,
                "scenario_is_forecast": False,
                "external_authentication_required": True,
                "native_exit_minutes": native_exit_minutes,
                "fixed_income_exit_plan": {
                    "mode": "FIXED_INCOME_EVENT_DRIVEN_MARKET_EXIT",
                    "verified": True,
                    "strategy_id": STRATEGY_IDS[family],
                    "max_hold_minutes": native_exit_minutes,
                    "exit_at": exit_at.isoformat(),
                    "coupon_ledger_required": True,
                    "nominal_rebase_required": False,
                    "economic_event_policy": "EXACT_NOMINAL_EVENT_REDUCER_V1",
                },
                "source_authority": "PPI_EXACT_IDENTITY; OFFICIAL_ISSUER_VERIFIED",
                "risk_scope": "PAPER_ONLY_INDEPENDENT_ADMISSION_REQUIRED"}
    return "BUY", score, "FI_PAPER_CANDIDATE_VALIDATED", features


def decide_fixed_income(quote, engine, at):
    """PaperEngine-compatible adapter, read-only and HOLD by default.

    No implicit network call, ticker-only lookup, mock cashflow, guessed yield
    or synthetic backtest is allowed. In particular, today no authoritative
    producer is present in the reference engine: this is fail-closed.
    """
    try:
        family = family_name(quote.asset_class)
    except (ValueError, AttributeError):
        return _hold("FI_FAMILY_UNSUPPORTED")
    if family not in FAMILIES:
        return _hold("FI_FAMILY_UNSUPPORTED", family)
    store = getattr(engine, "store", None)
    producer = getattr(store, "fixed_income_strategy_inputs", None)
    if not callable(producer):
        return _hold("FI_INPUT_PRODUCER_UNAVAILABLE", family)
    try:
        inputs = producer(quote, at)
        if not isinstance(inputs, Mapping):
            return _hold("FI_INPUT_PRODUCER_INCOMPLETE", family)
        if (inputs.get("upstream_authenticated") is not True or
                inputs.get("receipt_attested_by_release") is not True or
                inputs.get("source_kind") not in _VALID_BOOKS):
            return _hold("FI_SOURCE_ATTESTATION_MISSING", family)
        max_minutes = int(os.getenv("PAPER_MAX_HOLD_MINUTES", "360"))
        candidate = decide_fixed_income_from_inputs(
            quote, decision_at=at, scenario=inputs.get("scenario"),
            terms=inputs.get("terms"), validation=inputs.get("validation"),
            native_exit_minutes=max_minutes)
        if candidate[0] != "BUY":
            return candidate
        # This is a runtime engine capability, not a source-provided string.
        # Current engine has no fixed-income event-aware exit owner and must
        # hold even when a mock/invalid source asserts native exit support.
        exit_authorizer = getattr(engine, "fixed_income_native_exit_authorized", None)
        if not callable(exit_authorizer) or exit_authorizer(
                quote, candidate[3]["fixed_income_exit_plan"], at) is not True:
            return _hold("FI_NATIVE_EVENT_EXIT_ENGINE_NOT_BOUND", family)
        return candidate
    except (OSError, TypeError, ValueError, OverflowError, AttributeError, KeyError):
        return _hold("FI_INPUT_PRODUCER_FAILED_CLOSED", family)
