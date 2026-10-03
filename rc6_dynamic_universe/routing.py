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


def strategy_route(family, *, scalping=False):
    try:
        family = family_name(family)
    except ValueError:
        return {"family": str(family), "engine": "UNSUPPORTED", "strategy": None,
                "generic_equity": False, "status": "OBSERVE_ONLY",
                "reason_codes": ["STRATEGY_NOT_VALIDATED"]}
    if family in EQUITY_FAMILIES:
        return {"family": family, "engine": "SCALPING" if scalping else "EQUITY_SPOT",
                "strategy": "INTRADAY_SCALPING_HF5" if scalping else "EQUITY_SPOT_BASELINE",
                "generic_equity": True, "status": "SHADOW",
                "cadence_seconds": 30 if scalping else 120, "reason_codes": []}
    engine, strategy, cadence = LIFECYCLES[family]
    return {"family": family, "engine": engine, "strategy": strategy,
            "generic_equity": False, "status": "OBSERVE_ONLY",
            "cadence_seconds": cadence,
            "reason_codes": ["SPECIALIZED_LIFECYCLE", "STRATEGY_NOT_VALIDATED"]}


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
                             "iv": ancillary("iv"), "greeks": ancillary("greeks"),
                             "open_interest": ancillary("open_interest"),
                             "volume": ancillary("volume"),
                             "entry_authority": False})
    accepted.sort(key=lambda r: (r["underlying"], stamp(r["expires_at"]), r["moneyness"], r["identity"]))
    return {"status": "OBSERVE_ONLY", "selected": accepted, "excluded": excluded,
            "hypotheses": {"max_moneyness": band, "max_days_to_expiry": expiry_days, "max_spread_bps": spread_limit},
            "threshold_status": "SHADOW_OOS_HYPOTHESIS", "real_orders_sent": 0}
