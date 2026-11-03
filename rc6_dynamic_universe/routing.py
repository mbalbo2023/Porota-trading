"""Explicit family strategy routing, independent of contract READY status."""
from bs_instrument_contracts import family_name
from .common import identity, stamp, number

EQUITY_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS"})
PIPELINE = ("CATALOG_READY", "STRATEGY_ELIGIBLE", "TRADEABLE", "OBSERVABLE",
            "SIGNAL_READY", "ECONOMICS", "RISK", "PAPER")
LIFECYCLES = {
    "BONOS": ("FIXED_INCOME_ANALYTICS", "YIELD_DURATION_CASHFLOW", 300),
    "LETRAS": ("FIXED_INCOME_ANALYTICS", "DISCOUNT_MATURITY", 300),
    "OBLIGACIONES": ("FIXED_INCOME_ANALYTICS", "CREDIT_CASHFLOW", 300),
    "OPCIONES": ("OPTION_STRUCTURE", "UNDERLYING_EXPIRY_STRIKE", 60),
    "FUTUROS": ("FUTURES_LIFECYCLE", "DELEGATE_WS_MOTOR_16", None),
    "CAUCIONES": ("TREASURY", "CASH_RATE_TERM_EVENT", None),
    "FCI": ("FUND_NAV", "NAV_SUBSCRIPTION_REDEMPTION", 86400),
}
FAMILY_POLICY_VERSION = "RC6_FAMILY_OBSERVATION_POLICY_V2"
SOURCE_AUTHORITY = "PPI_PRIMARY_IDENTITY_CONTRACT; EXACT_IDENTITY_COMPLEMENTS_WITH_PROVENANCE"


def _policy(family, engine, strategy, *, scalping=False):
    """Observation policy is explicit even for an empty family inventory.

    This contract describes the SHADOW observer. Existing factual admission and
    exit lifecycles keep their owners; no observer policy grants PAPER authority.
    """
    equity = family in EQUITY_FAMILIES
    owner = {
        "ACCIONES": "EQUITY_SPOT_EXISTING_RUNTIME",
        "CEDEARS": "EQUITY_SPOT_CEDEAR_FEATURES_EXISTING_RUNTIME",
        "ETFS": "EQUITY_SPOT_ETF_FEATURES_EXISTING_RUNTIME",
        "BONOS": "FIXED_INCOME_NOMINAL_CONTRACT_ANALYTICS",
        "LETRAS": "FIXED_INCOME_DISCOUNT_MATURITY_ANALYTICS",
        "OBLIGACIONES": "FIXED_INCOME_CREDIT_CASHFLOW_ANALYTICS",
        "OPCIONES": "OPTION_CONTRACT_LIFECYCLE_EXISTING_RUNTIME",
        "FUTUROS": "WS_MOTOR_FUTURES_PAPER_16_ISSUE_453",
        "CAUCIONES": "CAUCION_TREASURY_LIFECYCLE_EXISTING_RUNTIME",
        "FCI": "FUND_NAV_SUBSCRIPTION_REDEMPTION_OBSERVER",
    }.get(family, "UNSUPPORTED_FAMILY_FAIL_CLOSED")
    cadence = ("SCALPING_HOT_WARM_DISCOVERY" if scalping else "EQUITY_INTERMEDIATE") if equity else {
        "BONOS": "FIXED_INCOME_PERIODIC", "LETRAS": "FIXED_INCOME_PERIODIC",
        "OBLIGACIONES": "FIXED_INCOME_PERIODIC", "OPCIONES": "OPTION_STRUCTURE_PERIODIC",
        "FUTUROS": "FUTURES_OWN_LIFECYCLE", "CAUCIONES": "EVENT_DRIVEN",
        "FCI": "EVENT_DRIVEN_NAV_PUBLICATION",
    }.get(family, "NO_CADENCE")
    selection = ("POINT_IN_TIME_TRADEABILITY_WITHIN_FAMILY_MARKET_CURRENCY" if equity else {
        "BONOS": "WITHIN_FAMILY_LIQUIDITY_MATURITY_ANALYTICS_OBSERVE_ONLY",
        "LETRAS": "WITHIN_FAMILY_LIQUIDITY_DISCOUNT_MATURITY_OBSERVE_ONLY",
        "OBLIGACIONES": "WITHIN_FAMILY_LIQUIDITY_CREDIT_CASHFLOW_OBSERVE_ONLY",
        "OPCIONES": "UNDERLYING_EXPIRY_MONEYNESS_LIQUIDITY_OBSERVE_ONLY",
        "FUTUROS": "DELEGATE_EXACT_CONTRACT_TO_453",
        "CAUCIONES": "CASH_RATE_TERM_MATURITY_EVENT",
        "FCI": "NAV_SUBSCRIPTION_REDEMPTION_HORIZON_EVENT",
    }.get(family, "NOT_APPLICABLE"))
    return {"policy_version": FAMILY_POLICY_VERSION, "strategy_owner": owner,
            "lifecycle_owner": owner, "strategy_status": "SHADOW" if equity else "STRATEGY_NOT_VALIDATED",
            "observation_policy": selection, "discovery_policy": (
                "ADAPTIVE_DISCOVERY_SEPARATE_FROM_DEEP_ANALYSIS" if equity else selection),
            "cadence_class": cadence, "source_authority": SOURCE_AUTHORITY,
            "tradeability_selection_policy": selection,
            "deep_analysis_eligibility": "SHADOW_TRADEABILITY_CAPACITY_GATED" if equity else "SPECIALIZED_OBSERVATION_ONLY",
            "equity_scanner_eligible": equity, "scalping_scanner_eligible": equity,
            "entry_authority": False, "financial_family_quotas": False}


def strategy_route(family, *, scalping=False):
    try:
        family = family_name(family)
    except ValueError:
        return {"family": str(family), "engine": "UNSUPPORTED", "strategy": None,
                "generic_equity": False, "status": "OBSERVE_ONLY",
                "reason_codes": ["STRATEGY_NOT_VALIDATED"],
                **_policy(str(family), "UNSUPPORTED", None)}
    if family in EQUITY_FAMILIES:
        return {"family": family, "engine": "SCALPING" if scalping else "EQUITY_SPOT",
                "strategy": "INTRADAY_SCALPING_HF5" if scalping else "EQUITY_SPOT_BASELINE",
                "generic_equity": True, "status": "SHADOW",
                "cadence_seconds": 30 if scalping else 120, "reason_codes": [],
                **_policy(family, "SCALPING" if scalping else "EQUITY_SPOT", None, scalping=scalping)}
    engine, strategy, cadence = LIFECYCLES[family]
    return {"family": family, "engine": engine, "strategy": strategy,
            "generic_equity": False, "status": "OBSERVE_ONLY",
            "cadence_seconds": cadence,
            "reason_codes": ["SPECIALIZED_LIFECYCLE", "STRATEGY_NOT_VALIDATED"],
            **_policy(family, engine, strategy)}


def dispatch_observation(record, handlers, *, as_of, scalping=False):
    """Specialized observation handlers take precedence; never a BUY fallback.

    A lifecycle may inspect an observation. Admission/execution stays with its
    existing owner and is deliberately not accepted as a callback here.
    """
    stamp(as_of)
    route = strategy_route(identity(record)[1], scalping=scalping)
    handler = handlers.get(route["engine"])
    if handler is None:
        return route | {"handler_result": None, "entry_authority": False}
    return route | {"handler_result": handler(dict(record), as_of=as_of),
                    "entry_authority": False}


def option_observation_universe(series, underlyings, *, as_of, max_moneyness=0.1,
                                max_days_to_expiry=90, max_spread_bps=100):
    """Observe economically related series, never blind ticker rotation.

    Input prices/terms require exact identity and both clocks. IV/Greeks/OI
    remain unknown unless explicitly supplied; liquidity is not directional edge.
    """
    at = stamp(as_of)
    band = number(max_moneyness)
    expiry_days = number(max_days_to_expiry, minimum=1)
    spread_limit = number(max_spread_bps, minimum=0.0000001)
    if band > 1:
        raise ValueError("INVALID_MONEYNESS_BAND")
    accepted, excluded = [], []
    for row in series:
        key = identity(row)
        reason = ""
        underlying = underlyings.get(row.get("underlying"))
        try:
            if underlying:
                underlying_key = identity(underlying)
            if key[1] != "OPCIONES" or not underlying or not underlying.get("observable"):
                reason = "SOURCE_UNAVAILABLE"
            elif underlying_key[1] not in EQUITY_FAMILIES or underlying_key[2:4] != key[2:4]:
                reason = "UNDERLYING_IDENTITY_OR_CURRENCY_MISMATCH"
            elif underlying_key[0] != str(row["underlying"]).strip().upper():
                reason = "UNDERLYING_IDENTITY_MISMATCH"
            elif underlying.get("tradeable") is not True:
                reason = "LOW_LIQUIDITY"
            elif row.get("strike_unit") != underlying.get("price_unit") or not row.get("strike_unit"):
                reason = "STRIKE_PRICE_BASIS_NO_VERIFICADO"
            elif any(stamp(obj["source_at"]) > stamp(obj["received_at"]) for obj in (row, underlying)):
                reason = "SOURCE_RECEIPT_CLOCK_MISMATCH"
            elif any(not 0 <= (at-stamp(obj[k])).total_seconds() <= 120
                     for obj in (row, underlying) for k in ("source_at", "received_at")):
                reason = "STALE_QUOTES"
            elif stamp(row["expires_at"]) <= at:
                reason = "OPTION_EXPIRED"
            elif (stamp(row["expires_at"])-at).total_seconds() > expiry_days*86400:
                reason = "OPTION_EXPIRY_OUTSIDE_RELEVANT_WINDOW"
            else:
                spot = number(underlying["price"], minimum=0.0000001)
                moneyness = abs(number(row["strike"], minimum=0.0000001)/spot-1)
                if moneyness > band:
                    reason = "OPTION_OUTSIDE_ECONOMIC_ZONE"
                elif number(row["bid"], minimum=0.0000001) > number(row["ask"], minimum=0.0000001):
                    reason = "CROSSED_BOOK"
                elif (number(row["ask"])-number(row["bid"]))/((number(row["ask"])+number(row["bid"]))/2)*10000 > spread_limit:
                    reason = "SPREAD_TOO_WIDE"
                elif min(number(row["bid_size"]), number(row["ask_size"])) <= 0:
                    reason = "INSUFFICIENT_DEPTH"
        except (ValueError, TypeError, KeyError):
            reason = "SOURCE_UNAVAILABLE"
        if reason:
            excluded.append({"identity": key, "reason_codes": [reason]})
        else:
            def ancillary(field):
                evidence = row.get(field+"_evidence", {})
                try:
                    source, received = stamp(evidence["source_at"]), stamp(evidence["received_at"])
                    verified = (row.get(field) is not None and source <= received <= at and
                                0 <= (at-source).total_seconds() <= 120)
                except (KeyError, ValueError, TypeError):
                    verified = False
                return {"value": row.get(field) if verified else None,
                        "status": "OBSERVED_FRESH" if verified else "NO_VERIFICADO"}
            accepted.append({"identity": key, "underlying": row["underlying"],
                             "expires_at": row["expires_at"], "moneyness": moneyness,
                             "spread_bps": (number(row["ask"])-number(row["bid"]))/((number(row["ask"])+number(row["bid"]))/2)*10000,
                             "book_depth_raw": min(number(row["bid_size"]), number(row["ask_size"])),
                             "book_depth_contract_units": min(number(row["bid_size"]), number(row["ask_size"])) if (row.get("quantity_unit") or row.get("depth_unit")) == "CONTRACTS" else None,
                             "book_depth_unit": row.get("quantity_unit") or row.get("depth_unit") or "NO_VERIFICADO",
                             "iv": ancillary("iv"), "greeks": ancillary("greeks"),
                             "open_interest": ancillary("open_interest"),
                             "volume": ancillary("volume"),
                             "entry_authority": False})
    # Compare related series only. Ticker is a deterministic last tie-break,
    # never financial priority. Optional IV/Greeks cannot override structure.
    accepted.sort(key=lambda r: (r["identity"][2:5], r["underlying"], stamp(r["expires_at"]),
                                 r["moneyness"], r["spread_bps"],
                                 -r["book_depth_contract_units"] if r["book_depth_contract_units"] is not None else 0, r["identity"]))
    ranks = {}
    for row in accepted:
        scope = (row["identity"][2:5], row["underlying"])
        ranks[scope] = ranks.get(scope, 0) + 1
        row.update(structural_priority=ranks[scope], selection_policy_version="RC6_OPTION_STRUCTURE_PRIORITY_V2",
                   rank_components={key: row[key] for key in ("underlying", "expires_at", "moneyness", "spread_bps", "book_depth_raw", "book_depth_contract_units", "book_depth_unit")},
                   ranking_scope="UNDERLYING_MARKET_CURRENCY_SETTLEMENT", entry_authority=False)
    return {"status": "OBSERVE_ONLY", "selected": accepted, "excluded": excluded,
            "hypotheses": {"max_moneyness": band, "max_days_to_expiry": expiry_days, "max_spread_bps": spread_limit},
            "threshold_status": "SHADOW_OOS_HYPOTHESIS", "real_orders_sent": 0}
