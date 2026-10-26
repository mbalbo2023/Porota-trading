"""Prospective, read-only hypotheses for specialized PAPER families.

These calculations are scenario filters, not trained signals or broker authority.
Research is an explicit, exact-identity, point-in-time input. No provider or
broker is called, and a positive scenario remains NO_VERIFICADO until OOS and
an independent PAPER admission have been demonstrated.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import date
from zoneinfo import ZoneInfo

from rc6_dynamic_universe.common import identity, stamp
from rc6_performance.costs import paper_cost_contract

VERSION = "RC6_SPECIALIZED_FAMILY_HYPOTHESES_V1"
STRATEGIES = {
    "BONOS": "DURATION_CASHFLOW_SCENARIO",
    "LETRAS": "DISCOUNT_HOLD_TO_MATURITY",
    "OBLIGACIONES": "ISSUER_CREDIT_CASHFLOW_SCENARIO",
    "OPCIONES": "LONG_PREMIUM_UNDERLYING_SCENARIO",
    "FCI": "SURPLUS_CASH_REDEMPTION_SCENARIO",
}


def _decimal(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("INVALID_SCENARIO_NUMBER")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("INVALID_SCENARIO_NUMBER") from None
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError("INVALID_SCENARIO_NUMBER")
    return result


def _value(field, *, positive=False):
    if not hasattr(field, "get") or field.get("review_required") or field.get("value") is None:
        raise ValueError("REQUIRED_OBSERVATION_NO_VERIFICADO")
    return _decimal(field["value"], positive=positive)


def _clocked_research(key, research, at):
    if not isinstance(research, dict):
        raise ValueError("INDEPENDENT_SCENARIO_MISSING")
    try:
        if identity(research) != tuple(key):
            raise ValueError("SCENARIO_IDENTITY_MISMATCH")
        source, received, frozen = (stamp(research[name]) for name in
                                    ("source_at", "received_at", "model_frozen_at"))
        horizon = stamp(research["horizon_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(str(exc) if str(exc) == "SCENARIO_IDENTITY_MISMATCH" else
                         "SCENARIO_CLOCK_OR_IDENTITY_NO_VERIFICADO") from None
    if (research.get("schema") != VERSION or not research.get("model_id") or
            not research.get("model_provenance") or not frozen < source <= received <= at < horizon):
        raise ValueError("SCENARIO_NOT_PREREGISTERED_OR_CAUSAL")
    if (at-source).total_seconds() > 300:
        raise ValueError("SCENARIO_STALE")
    return horizon


def _book(observation, *, quantity_unit):
    quote = observation.get("quote") or {}
    if (quote.get("book_status") != "OBSERVED_FRESH" or quote.get("review_required") or
            quote.get("price_basis_status") != "EXPLICIT_COMPATIBLE_UNITS" or
            quote.get("quantity_unit") != quantity_unit):
        raise ValueError("EXECUTABLE_BOOK_OR_UNITS_NO_VERIFICADO")
    ask = _decimal(quote.get("ask"), positive=True)
    bid = _decimal(quote.get("bid"), positive=True)
    depth = min(_decimal(quote.get("ask_size"), positive=True),
                _decimal(quote.get("bid_size"), positive=True))
    if bid > ask or (ask-bid)/ask > Decimal("0.01"):
        raise ValueError("CROSSED_OR_WIDE_BOOK")
    return ask, depth


def _economics(key, at, *, ask, exit_bid, quantity, multiplier, cashflows=Decimal(0),
               opportunity=Decimal(0), stress=Decimal(0), exit_trade=True):
    cost = paper_cost_contract(key[1], key[3], at)
    if error := cost.binding_error(at):
        raise ValueError(error)
    buy = ask * quantity * multiplier
    sell = exit_bid * quantity * multiplier
    fees = cost.leg_cost(buy, decision_at=at)
    if exit_trade:
        fees += cost.leg_cost(sell, decision_at=at)
    # Quotes anchor the spread; the extra slippage and opportunity/stress
    # allowances are explicit scenario inputs, never a predicted return.
    net = sell + cashflows - buy - fees - opportunity - stress
    return {"buy_cash": str(buy), "scenario_exit_cash": str(sell),
            "scenario_cashflows": str(cashflows), "paper_fees": str(fees),
            "opportunity_cost": str(opportunity), "stress_allowance": str(stress),
            "scenario_net_after_costs": str(net), "positive_after_costs": net > 0,
            "cost_policy_version": cost.policy_version,
            "account_tariff": "NO_VERIFICADO"}


def evaluate_family_strategy(key, observation, research, *, as_of):
    """One exact instrument; every outcome is read-only and non-binding."""
    key = tuple(key)
    family = key[1]
    if family not in STRATEGIES:
        raise ValueError("SPECIALIZED_STRATEGY_FAMILY_REQUIRED")
    at = stamp(as_of)
    result = {"version": VERSION, "strategy": STRATEGIES[family], "status": "NO_VERIFICADO",
              "trigger": "HOLD", "reason_code": "INDEPENDENT_SCENARIO_MISSING",
              "entry_authority": False, "paper_auto_enabled": False,
              "real_order_routes": [], "decision_effect": "NONE", "oos": "NO_VERIFICADO"}
    try:
        horizon = _clocked_research(key, research, at)
        proposal = research.get("scenario")
        if not isinstance(proposal, dict):
            raise ValueError("SCENARIO_TERMS_MISSING")
        if family == "FCI":
            terms = observation.get("terms") or {}
            _value(terms.get("nav"), positive=True)
            for name in ("nav_date", "subscription_status", "redemption_term", "cutoff_time"):
                if not isinstance(terms.get(name), dict) or terms[name].get("value") is None or terms[name].get("review_required"):
                    raise ValueError("FUND_TERMS_NO_VERIFICADO")
            if str(terms["subscription_status"]["value"]).upper() != "OPEN":
                raise ValueError("FUND_SUBSCRIPTION_CLOSED")
            local = at.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
            nav_day = date.fromisoformat(str(terms["nav_date"]["value"]))
            if not 0 <= (local.date()-nav_day).days <= 3:
                raise ValueError("FUND_NAV_DATE_OUTSIDE_WINDOW")
            cutoff = str(terms["cutoff_time"]["value"])
            if len(cutoff) != 9 or cutoff[5:] != " ART" or cutoff[2] != ":":
                raise ValueError("FUND_CUTOFF_FORMAT_NO_VERIFICADO")
            hour, minute = int(cutoff[:2]), int(cutoff[3:5])
            if not 0 <= hour <= 23 or not 0 <= minute <= 59 or (local.hour, local.minute) >= (hour, minute):
                raise ValueError("FUND_CUTOFF_PASSED")
            cash = observation.get("cash") or {}
            if cash.get("status") != "OBSERVED_PAPER_CASH":
                raise ValueError("FUND_CASH_NO_VERIFICADO")
            free = _decimal(cash.get("value"), nonnegative=True)
            reserve = _decimal(proposal.get("committed_cash"), nonnegative=True)
            allocation = _decimal(proposal.get("subscription_cash"), positive=True)
            if allocation > free-reserve:
                raise ValueError("FUND_SURPLUS_CASH_INSUFFICIENT")
            if stamp(proposal["liquidity_needed_at"]) <= horizon or stamp(proposal["redeemable_at"]) > stamp(proposal["liquidity_needed_at"]):
                raise ValueError("FUND_REDEMPTION_AFTER_CASH_NEED")
            expected = _decimal(proposal.get("scenario_redemption_cash"), nonnegative=True)
            fees = _decimal(proposal.get("all_subscription_redemption_fees"), nonnegative=True)
            stress = _decimal(proposal.get("nav_loss_allowance"), nonnegative=True)
            opportunity = _decimal(proposal.get("opportunity_cost"), nonnegative=True)
            # NAV is descriptive; scenario proceeds are uncertain and never
            # derived from the last NAV as a guaranteed redemption amount.
            net = expected-allocation-fees-stress-opportunity
            economics = {"subscription_cash": str(allocation), "scenario_redemption_cash": str(expected),
                         "scenario_net_after_costs": str(net), "positive_after_costs": net > 0,
                         "fund_fee_source": research["model_provenance"], "account_tariff": "NO_VERIFICADO"}
        else:
            if family == "OPCIONES":
                if not observation.get("structurally_selected"):
                    raise ValueError("OPTION_STRUCTURE_OR_UNDERLYING_NO_VERIFICADO")
                option = observation.get("terms") or {}
                if option.get("option_right", {}).get("value") not in {"CALL", "PUT"}:
                    raise ValueError("OPTION_RIGHT_NO_VERIFICADO")
                underlying = observation.get("underlying_quote") or {}
                if underlying.get("book_status") != "OBSERVED_FRESH" or not underlying.get("tradeable"):
                    raise ValueError("OPTION_UNDERLYING_BOOK_NO_VERIFICADO")
                spot = _decimal(underlying.get("price"), positive=True)
                target = _decimal(proposal.get("underlying_target_price"), positive=True)
                if (proposal.get("underlying_price_unit") != underlying.get("price_unit") or
                        (option["option_right"]["value"] == "CALL" and target <= spot) or
                        (option["option_right"]["value"] == "PUT" and target >= spot)):
                    raise ValueError("OPTION_DIRECTION_OR_UNDERLYING_UNIT_NO_VERIFICADO")
                if horizon >= stamp(option["expires_at"]["value"]):
                    raise ValueError("OPTION_HORIZON_AT_OR_AFTER_EXPIRY")
                ask, depth = _book(observation, quantity_unit="CONTRACTS")
                multiplier = _decimal(proposal.get("contract_cash_multiplier"), positive=True)
                contract = observation.get("option_contract") or {}
                if (contract.get("status") != "VERIFIED_EXACT_CONTRACT" or
                        _decimal(contract.get("cash_multiplier"), positive=True) != multiplier or
                        contract.get("underlying") != option["underlying"]["value"] or
                        contract.get("option_right") != option["option_right"]["value"] or
                        stamp(contract["expires_at"]) != stamp(option["expires_at"]["value"])):
                    raise ValueError("OPTION_EXACT_LOT_AND_UNDERLYING_NO_VERIFICADO")
                # Long premium only. A contract multiplier must be sourced in
                # the research contract; no ticker or strike-based lot guess.
                quantity = _decimal(proposal.get("contracts"), positive=True)
                if quantity != quantity.to_integral_value() or proposal.get("side") != "LONG":
                    raise ValueError("LONG_WHOLE_OPTION_CONTRACTS_ONLY")
            else:
                if observation.get("nominal_contract_status") != "VERIFIED_NOMINAL_CONTRACT":
                    raise ValueError("FIXED_INCOME_NOMINAL_CONTRACT_NO_VERIFICADO")
                ask, depth = _book(observation, quantity_unit="NOMINALS")
                multiplier = _value(observation["terms"].get("cash_multiplier"), positive=True)
                quantity = _decimal(proposal.get("nominals"), positive=True)
                minimum = _value(observation["terms"].get("minimum_nominal"), positive=True)
                step = _value(observation["terms"].get("quantity_step_nominal"), positive=True)
                if quantity < minimum or quantity % step:
                    raise ValueError("FIXED_INCOME_QUANTITY_INVALID")
                maturity = observation.get("maturity", {}).get("value")
                if maturity and horizon > stamp(maturity):
                    raise ValueError("SCENARIO_AFTER_MATURITY")
                if family == "LETRAS" and (not maturity or horizon != stamp(maturity)):
                    raise ValueError("LETTER_MATURITY_REQUIRED")
                if family == "LETRAS":
                    flows = observation.get("analytics", {}).get("flows", {})
                    unit = observation.get("terms", {}).get("flow_unit", {})
                    native = flows.get("value") if not flows.get("review_required") else None
                    if (unit.get("value") != "CASH_PER_NOMINAL" or
                            not isinstance(native, list) or len(native) != 1 or
                            native[0].get("currency") != key[3] or
                            stamp(native[0]["payment_at"]) != horizon or
                            _decimal(native[0].get("amount"), positive=True)*quantity !=
                            _decimal(proposal.get("cashflows_cash"), positive=True)):
                        raise ValueError("LETTER_REDEMPTION_NATIVE_FLOW_NO_VERIFICADO")
                if family in {"BONOS", "OBLIGACIONES"}:
                    analytics = observation.get("analytics") or {}
                    duration = _value(analytics.get("duration"), positive=True)
                    curve = analytics.get("curve") or {}
                    if (curve.get("review_required") or not curve.get("value") or
                            proposal.get("curve_reference") != curve["value"] or
                            duration > _decimal(proposal.get("max_duration_years"), positive=True)):
                        raise ValueError("CURVE_OR_DURATION_RISK_NO_VERIFICADO")
                    shock = _decimal(proposal.get("rate_shock_fraction"), nonnegative=True)
                    allowance = _decimal(proposal.get("loss_allowance_cash"), nonnegative=True)
                    if allowance < shock*duration*ask*quantity*multiplier:
                        raise ValueError("DURATION_SHOCK_NOT_COVERED")
                if family == "OBLIGACIONES":
                    if not proposal.get("issuer") or proposal.get("credit_review") != "CURRENT":
                        raise ValueError("ISSUER_CREDIT_REVIEW_NO_VERIFICADO")
                    exposure = _decimal(proposal.get("issuer_exposure_cash"), nonnegative=True)
                    limit = _decimal(proposal.get("issuer_limit_cash"), positive=True)
                    if exposure + ask*quantity*multiplier > limit:
                        raise ValueError("ISSUER_LIMIT_EXCEEDED")
            if quantity > depth * Decimal("0.1"):
                raise ValueError("BOOK_PARTICIPATION_EXCEEDED")
            if proposal.get("price_unit") != observation["quote"].get("price_unit") or proposal.get("cash_currency") != key[3]:
                raise ValueError("SCENARIO_PRICE_UNIT_OR_CURRENCY_MISMATCH")
            exit_bid = _decimal(proposal.get("scenario_exit_bid"), nonnegative=True)
            cashflows = _decimal(proposal.get("cashflows_cash"), nonnegative=True)
            opportunity = _decimal(proposal.get("opportunity_cost"), nonnegative=True)
            stress = _decimal(proposal.get("loss_allowance_cash"), nonnegative=True)
            if family == "LETRAS":
                if proposal.get("cashflow_unit") != "CASH_FOR_EXACT_NOMINAL_QUANTITY" or not cashflows:
                    raise ValueError("LETTER_REDEMPTION_UNIT_NO_VERIFICADO")
                if exit_bid:
                    raise ValueError("LETTER_DOUBLE_COUNTED_EXIT_AND_REDEMPTION")
            economics = _economics(key, at, ask=ask, exit_bid=exit_bid, quantity=quantity,
                                   multiplier=multiplier, cashflows=cashflows,
                                   opportunity=opportunity, stress=stress,
                                   exit_trade=family != "LETRAS")
            if family == "OPCIONES":
                max_loss = ask*quantity*multiplier + _decimal(economics["paper_fees"])
                budget = _decimal(proposal.get("premium_risk_budget_cash"), positive=True)
                if max_loss > budget:
                    raise ValueError("LONG_PREMIUM_RISK_BUDGET_EXCEEDED")
        result.update({"reason_code": "SCENARIO_POSITIVE_OOS_REQUIRED" if economics["positive_after_costs"]
                       else "SCENARIO_AFTER_COSTS_NONPOSITIVE",
                       "trigger": "POSITIVE_HYPOTHESIS" if economics["positive_after_costs"] else "HOLD",
                       "scenario": economics, "model_id": str(research["model_id"])[:80],
                       "model_frozen_at": research["model_frozen_at"],
                       "horizon_at": horizon.isoformat()})
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        reason = str(exc)
        result["reason_code"] = reason if reason in {
            "SCENARIO_IDENTITY_MISMATCH", "INDEPENDENT_SCENARIO_MISSING",
            "SCENARIO_CLOCK_OR_IDENTITY_NO_VERIFICADO", "SCENARIO_NOT_PREREGISTERED_OR_CAUSAL",
            "SCENARIO_STALE", "SCENARIO_TERMS_MISSING", "REQUIRED_OBSERVATION_NO_VERIFICADO",
            "EXECUTABLE_BOOK_OR_UNITS_NO_VERIFICADO", "CROSSED_OR_WIDE_BOOK",
            "FUND_TERMS_NO_VERIFICADO", "FUND_SUBSCRIPTION_CLOSED", "FUND_CASH_NO_VERIFICADO",
            "FUND_SURPLUS_CASH_INSUFFICIENT", "FUND_REDEMPTION_AFTER_CASH_NEED",
            "FUND_NAV_DATE_OUTSIDE_WINDOW", "FUND_CUTOFF_FORMAT_NO_VERIFICADO", "FUND_CUTOFF_PASSED",
            "OPTION_STRUCTURE_OR_UNDERLYING_NO_VERIFICADO", "OPTION_RIGHT_NO_VERIFICADO",
            "OPTION_UNDERLYING_BOOK_NO_VERIFICADO", "OPTION_DIRECTION_OR_UNDERLYING_UNIT_NO_VERIFICADO",
            "OPTION_HORIZON_AT_OR_AFTER_EXPIRY", "OPTION_EXACT_LOT_AND_UNDERLYING_NO_VERIFICADO",
            "LONG_WHOLE_OPTION_CONTRACTS_ONLY",
            "FIXED_INCOME_NOMINAL_CONTRACT_NO_VERIFICADO", "FIXED_INCOME_QUANTITY_INVALID",
            "SCENARIO_AFTER_MATURITY", "LETTER_MATURITY_REQUIRED", "LETTER_REDEMPTION_NATIVE_FLOW_NO_VERIFICADO",
            "CURVE_OR_DURATION_RISK_NO_VERIFICADO", "DURATION_SHOCK_NOT_COVERED",
            "ISSUER_LIMIT_EXCEEDED", "BOOK_PARTICIPATION_EXCEEDED", "SCENARIO_PRICE_UNIT_OR_CURRENCY_MISMATCH",
            "LETTER_REDEMPTION_UNIT_NO_VERIFICADO", "LETTER_DOUBLE_COUNTED_EXIT_AND_REDEMPTION",
            "LONG_PREMIUM_RISK_BUDGET_EXCEEDED"} else "SCENARIO_FIELDS_NO_VERIFICADO"
    return result
