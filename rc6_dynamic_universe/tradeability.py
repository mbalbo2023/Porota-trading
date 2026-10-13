"""Causal, non-directional EquitySpot attention hypotheses (SHADOW/OOS).

Daily history needs an audited session and both observation/publication clocks.
Volumes require explicit units; monetary turnover requires the identity currency.
Missing evidence remains unknown. This module cannot grant an entry or send orders.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import json
from statistics import median, stdev

from bs_instrument_contracts import family_name
from .common import digest, identity, number, percentile, stamp

VERSION = "WS-PERF-03-TRADEABILITY-v1"
EQUITY_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS"})
# These are prospective hypotheses, deliberately unrelated to historical P&L.
DEFAULT_CONFIG = {
    "hypothesis_version": VERSION,
    "evaluation": "SHADOW/OOS",
    "min_activity5": 0.6,
    "min_activity20": 0.5,
    "max_spread_bps": 50.0,
    "min_depth_paper_multiple": 1.0,
    "max_quote_age_seconds": 90.0,
    "min_useful_fraction": 0.8,
    "min_observations": 3,
    "observation_window_seconds": 900.0,
    "min_liquidity_percentile": 0.1,
    "min_profile_sessions": 5,
    "anomaly_window_minutes": 5,
    "rvol_threshold": 2.0,
    "acceleration_threshold": 2.0,
    "price_shock_sigma": 3.0,
    "spread_compression_fraction": 0.25,
    "depth_improvement_fraction": 1.0,
}
IDENTITY_KEYS = ("ticker", "instrument_type", "market", "currency", "settlement")


def _configuration(config):
    result = dict(DEFAULT_CONFIG)
    if config:
        if set(config) - set(result):
            raise ValueError("UNKNOWN_SHADOW_HYPOTHESIS")
        result.update(config)
    if result["evaluation"] != "SHADOW/OOS" or not result["hypothesis_version"]:
        raise ValueError("SHADOW_OOS_REQUIRED")
    for name, default in DEFAULT_CONFIG.items():
        if isinstance(default, (int, float)):
            value = number(result[name])
            if name in {"min_observations", "min_profile_sessions", "anomaly_window_minutes"}:
                if value < 1 or value != int(value):
                    raise ValueError("POSITIVE_INTEGER_HYPOTHESIS_REQUIRED")
                result[name] = int(value)
            elif ("fraction" in name or "percentile" in name or name.startswith("min_activity")) and value > 1:
                raise ValueError("FRACTION_OUT_OF_RANGE")
            else:
                result[name] = value
    return result


def _value(row, key):
    try:
        return number(row[key]) if row.get(key) is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _available(row, cutoff):
    """Require two real clocks; never substitute retrieval time for event time."""
    try:
        observed, published = stamp(row["observed_at"]), stamp(row["published_at"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return False, "SOURCE_TIMESTAMP_UNVERIFIED"
    if observed > cutoff or published > cutoff:
        return False, "FUTURE_INFORMATION_REJECTED"
    if published < observed:
        return False, "SOURCE_CLOCK_ORDER_INVALID"
    return True, None


def _session(value):
    result = date.fromisoformat(str(value))
    if str(value) != result.isoformat():
        raise ValueError("AUDITED_SESSION_REQUIRED")
    return result.isoformat()


def _family(record, key):
    """The exact instrument identity owns routing; metadata cannot reclassify it."""
    try:
        canonical = family_name(key[1])
    except ValueError:
        canonical = key[1]
    declared = record.get("family")
    if not declared:
        return canonical, False
    try:
        declared = family_name(declared)
    except ValueError:
        declared = str(declared).strip().upper()
    return canonical, declared != canonical


def _partition(records, cutoff, allowed=None):
    by_identity = defaultdict(list)
    rejected = defaultdict(int)
    for row in records:
        ok, reason = _available(row, cutoff)
        if not ok:
            rejected[reason] += 1
            continue
        try:
            key = identity(row)
            session = _session(row["session"])
        except (KeyError, ValueError, TypeError):
            rejected["INVALID_IDENTITY_OR_SESSION"] += 1
            continue
        if _family(row, key)[1]:
            rejected["FAMILY_IDENTITY_MISMATCH"] += 1
            continue
        if session > cutoff.date().isoformat():
            rejected["FUTURE_INFORMATION_REJECTED"] += 1
            continue
        if allowed is not None and session not in allowed:
            rejected["SESSION_OUTSIDE_AUDITED_CUT"] += 1
            continue
        by_identity[key].append(row)
    return by_identity, dict(rejected)


def _volume(row):
    value = _value(row, "volume")
    unit = str(row.get("volume_unit") or "").upper()
    # NOMINAL is deliberately excluded: its financial meaning is not verified.
    return (value, unit) if value is not None and unit in {"SHARES", "UNITS"} else (None, None)


def _turnover(row, currency):
    value = _value(row, "turnover")
    unit = str(row.get("turnover_currency") or "").upper()
    return value if value is not None and unit == currency else None


def _metric(values, unit=None):
    return {"value": median(values) if values else None,
            "p50": median(values) if values else None, "p95": percentile(values, 0.95), "samples": len(values),
            "unit": unit, "status": "VERIFIED_INPUT" if values else "NO_VERIFICADO"}


def _activity(by_session, sessions, window):
    recent = sessions[-window:]
    known = []
    for session in recent:
        row = by_session.get(session)
        if row is None:
            continue
        trades = _value(row, "trades")
        volume, _ = _volume(row)
        if trades is not None or volume is not None:
            known.append(bool((trades or 0) > 0 or (volume or 0) > 0))
    complete = len(recent) == window and len(known) == window
    return {"value": sum(known) / window if complete else None,
            "active_sessions": sum(known), "observed_sessions": len(known),
            "required_sessions": window,
            "status": "VERIFIED_INPUT" if complete else "NO_VERIFICADO"}


def _cedear_features(record, observations, cutoff):
    result = {}
    # Each ancillary feature carries its own source clocks, never the quote's.
    for name in ("underlying_us_session", "ratio", "ccl", "local_divergence"):
        candidates = [(row.get("cedear_features") or {}).get(name) for row in [record, *observations]
                      if isinstance(row.get("cedear_features") or {}, dict)]
        valid = [row for row in candidates if isinstance(row, dict) and _available(row, cutoff)[0]
                 and row.get("value") is not None and row.get("source")]
        latest = max(valid, key=lambda row: stamp(row["observed_at"])) if valid else None
        age = (cutoff - stamp(latest["observed_at"])).total_seconds() if latest else None
        max_age = _value(latest, "max_age_seconds") if latest else None
        result[name] = {"value": latest["value"] if latest else None,
                        "source": latest.get("source") if latest else None,
                        "observed_at": latest.get("observed_at") if latest else None,
                        "published_at": latest.get("published_at") if latest else None,
                        "source_age_seconds": age,
                        "freshness_status": "NO_VERIFICADO" if max_age is None else ("FRESH" if age <= max_age else "STALE"),
                        "status": "VERIFIED_INPUT" if latest else "NO_VERIFICADO"}
    return result


def rank_tradeability(catalog, history, observations, *, cutoff, sessions, paper_size=1, config=None):
    """Rank attention inside comparable groups; preserve every catalog identity.

    ``sessions`` is the explicit audited calendar, not a guessed weekday list.
    Daily rows need session/volume/volume_unit and may supply currency-qualified
    turnover/trades/interarrival/range. Quotes may supply spread_bps/depth_units,
    usable and source. Later publications of earlier sessions remain excluded.
    """
    cut = stamp(cutoff)
    cfg = _configuration(config)
    size = number(paper_size, minimum=0.000000001)
    audited = sorted(set(_session(value) for value in sessions))
    if any(day > cut.date().isoformat() for day in audited):
        raise ValueError("AUDITED_SESSION_AFTER_CUTOFF")
    catalog, history, observations = list(catalog), list(history), list(observations)
    daily, rejected_history = _partition(history, cut, set(audited))
    quotes, rejected_quotes = _partition(observations, cut)
    rows = []
    seen = set()
    for record in catalog:
        key = identity(record)
        if key in seen:
            raise ValueError("DUPLICATE_CATALOG_IDENTITY")
        seen.add(key)
        family, family_mismatch = _family(record, key)
        group = (family, key[2], key[3])
        # Select the latest *available* revision, never the eventual final value.
        by_session = {}
        for row in sorted(daily[key], key=lambda item: (stamp(item["published_at"]), stamp(item["observed_at"]))):
            by_session[row["session"]] = row
        recent_daily = [by_session[day] for day in audited[-20:] if day in by_session]
        units = {unit for item in recent_daily for value, unit in [_volume(item)] if value is not None}
        unit = next(iter(units)) if len(units) == 1 else None
        volume = [value for item in recent_daily for value, row_unit in [_volume(item)]
                  if unit is not None and row_unit == unit and value is not None]
        turnover = [value for item in recent_daily for value in [_turnover(item, key[3])] if value is not None]
        all_quotes = sorted(quotes[key], key=lambda item: stamp(item["observed_at"]))
        quote_rows = [item for item in all_quotes if stamp(item["observed_at"]) >= cut - timedelta(seconds=cfg["observation_window_seconds"])]
        usable = [item for item in quote_rows if item.get("usable") is True]
        latest_usable = [item for item in all_quotes if item.get("usable") is True]
        latest = latest_usable[-1] if latest_usable else None
        age = (cut - stamp(latest["observed_at"])).total_seconds() if latest else None
        spreads = [_value(item, "spread_bps") for item in usable]
        spreads = [value for value in spreads if value is not None]
        depth_unit_set = {str(item.get("depth_unit") or "").upper() for item in usable
                          if _value(item, "depth_units") is not None
                          and str(item.get("depth_unit") or "").upper() in {"SHARES", "UNITS"}}
        depth_unit = next(iter(depth_unit_set)) if len(depth_unit_set) == 1 else None
        depths = [_value(item, "depth_units") for item in usable
                  if depth_unit and str(item.get("depth_unit") or "").upper() == depth_unit]
        depths = [value / size for value in depths if value is not None]
        # Unit-qualified paper size is supplied in the instrument quantity unit.
        features = {
            "activity5": _activity(by_session, audited, 5),
            "activity20": _activity(by_session, audited, 20),
            "median_volume": _metric(volume, unit),
            "median_turnover": _metric(turnover, key[3]),
            "spread_bps": _metric(spreads, "BPS"),
            "depth_paper_multiple": _metric(depths, "PAPER_SIZE_MULTIPLE"),
            "interarrival_seconds": _metric([v for item in recent_daily for v in [_value(item, "interarrival_seconds")] if v is not None], "SECONDS"),
            "range_bps": _metric([v for item in recent_daily for v in [_value(item, "range_bps")] if v is not None], "BPS"),
            "slippage_bps": _metric([v for item in usable for v in [_value(item, "slippage_bps")] if v is not None], "BPS"),
            "quote_age_seconds": {"value": age, "status": "VERIFIED_INPUT" if age is not None else "NO_VERIFICADO"},
            "usable_observation_fraction": {"value": len(usable) / len(quote_rows) if quote_rows else None,
                                            "samples": len(quote_rows),
                                            "status": "VERIFIED_INPUT" if quote_rows else "NO_VERIFICADO"},
        }
        reasons = ["FAMILY_IDENTITY_MISMATCH"] if family_mismatch else []
        if family not in EQUITY_FAMILIES:
            reasons.extend(["SPECIALIZED_LIFECYCLE", "STRATEGY_NOT_VALIDATED"])
        for name, threshold in (("activity5", cfg["min_activity5"]), ("activity20", cfg["min_activity20"])):
            value = features[name]["value"]
            if value is None:
                reasons.append("INSUFFICIENT_ACTIVITY_EVIDENCE")
            elif value == 0:
                reasons.append("NO_RECENT_TRADES")
            elif value < threshold:
                reasons.append("LOW_ACTIVITY")
        if not volume and not turnover:
            reasons.append("LIQUIDITY_UNITS_NO_VERIFICADO")
        elif (not volume or median(volume) == 0) and (not turnover or median(turnover) == 0):
            reasons.append("LOW_LIQUIDITY")
        if features["spread_bps"]["p95"] is None:
            reasons.append("SPREAD_NO_VERIFICADO")
        elif features["spread_bps"]["p95"] > cfg["max_spread_bps"]:
            reasons.append("SPREAD_TOO_WIDE")
        if not depths:
            reasons.append("DEPTH_NO_VERIFICADO")
        elif median(depths) < cfg["min_depth_paper_multiple"]:
            reasons.append("INSUFFICIENT_DEPTH")
        if age is None:
            reasons.append("SOURCE_UNAVAILABLE")
        elif age > cfg["max_quote_age_seconds"]:
            reasons.append("STALE_QUOTES")
        fraction = features["usable_observation_fraction"]["value"]
        if len(usable) < cfg["min_observations"] or fraction is None or fraction < cfg["min_useful_fraction"]:
            reasons.append("INSUFFICIENT_USEFUL_OBSERVATIONS")
        sources = sorted({str(item["source"]) for item in [*recent_daily, *quote_rows] if item.get("source")})
        rows.append({**dict(zip(IDENTITY_KEYS, key)), "identity": list(key), "family": family,
                     "group": list(group), "rank": None, "tradeability_score": None,
                     "score_is_probability": False, "directional_authority": False,
                     "components": features, "paper_size": size, "paper_quantity_unit": depth_unit,
                     "tradeable": False, "reason_codes": sorted(set(reasons)),
                     "provenance": {"sources": sources, "history_rows": len(recent_daily),
                                    "observation_rows": len(quote_rows), "cutoff": cut.isoformat(),
                                    "latest_observation_at": latest.get("observed_at") if latest else None,
                                    "evidence": [{"session": item["session"], "observed_at": item["observed_at"],
                                                  "published_at": item["published_at"], "source": item.get("source"),
                                                  "input_hash": digest(item)} for item in [*recent_daily, *all_quotes]]},
                     "cedear_features": _cedear_features(record, quote_rows, cut) if family == "CEDEARS" else {}})
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row["group"])].append(row)
    curves = []
    for group, members in sorted(groups.items()):
        for metric in ("median_volume", "median_turnover"):
            comparable = defaultdict(list)
            for row in members:
                component = row["components"][metric]
                if component["value"] is not None:
                    comparable[component["unit"]].append(row)
            for unit, comparable_rows in sorted(comparable.items()):
                total = sum(row["components"][metric]["value"] for row in comparable_rows)
                cumulative = 0.0
                points = []
                ordered = sorted(comparable_rows, key=lambda row: (-row["components"][metric]["value"], row["identity"]))
                for index, row in enumerate(ordered, 1):
                    value = row["components"][metric]["value"]
                    cumulative += value
                    points.append({"rank": index, "identity": row["identity"], "value": value,
                                   "cumulative_share": cumulative / total if total > 0 else None})
                    # Empirical percentile inside family/market/currency/unit.
                    peers = [other["components"][metric]["value"] for other in comparable_rows]
                    rank_value = sum(other < value for other in peers) + sum(other == value for other in peers) / 2
                    row["components"][metric]["group_percentile"] = rank_value / len(peers)
                curves.append({"group": list(group), "metric": metric, "unit": unit,
                               "members_with_evidence": len(ordered), "total": total, "points": points})
        for row in members:
            parts = row["components"]
            components = [parts[name].get("group_percentile") for name in ("median_volume", "median_turnover")]
            liquidity = [value for value in components if value is not None]
            if liquidity and max(liquidity) < cfg["min_liquidity_percentile"]:
                row["reason_codes"].append("LOW_LIQUIDITY")
            # Attention score only; unavailable metrics are exposed, never zeros.
            spread = parts["spread_bps"]["p95"]
            depth = parts["depth_paper_multiple"]["value"]
            age = parts["quote_age_seconds"]["value"]
            row["score_components"] = {
                "liquidity_group_percentile": sum(liquidity) / len(liquidity) if liquidity else None,
                "activity5": parts["activity5"]["value"], "activity20": parts["activity20"]["value"],
                "usable_fraction": parts["usable_observation_fraction"]["value"],
                "spread_quality": max(0, 1 - spread / max(cfg["max_spread_bps"], 1e-9)) if spread is not None else None,
                "depth_quality": min(1, depth / max(cfg["min_depth_paper_multiple"], 1e-9)) if depth is not None else None,
                "freshness_quality": max(0, 1 - age / max(cfg["max_quote_age_seconds"], 1e-9)) if age is not None else None,
            }
            values = [value for value in row["score_components"].values() if value is not None]
            row["tradeability_score"] = sum(values) / len(values) if values else None
            row["tradeable"] = not row["reason_codes"]
        for index, row in enumerate(sorted(members, key=lambda item: (-(item["tradeability_score"] or 0), item["identity"])), 1):
            row["rank"] = index
    rejected = {"history": rejected_history, "observations": rejected_quotes}
    return {"version": VERSION, "mode": "SHADOW", "evaluation": "OOS_PENDING",
            "cutoff": cut.isoformat(), "sessions": audited, "config": cfg,
            "config_fingerprint": digest(cfg), "rows": rows, "concentration_curves": curves,
            "inputs_hash": digest({"catalog": catalog, "history": history, "observations": observations, "sessions": audited}),
            "provenance": {"cutoff": cut.isoformat(), "audited_sessions": audited,
                           "input_policy": "OBSERVED_AND_PUBLISHED_BEFORE_CUT"},
            "rejected_inputs": rejected,
            "rejected_future_count": sum(counts.get("FUTURE_INFORMATION_REJECTED", 0) for counts in rejected.values()),
            "real_orders_sent": 0}


@dataclass(frozen=True)
class FrozenPreopen:
    """An immutable canonical snapshot; payload access produces a fresh copy."""
    payload_json: str
    digest: str

    @property
    def payload(self):
        return json.loads(self.payload_json)

    def to_dict(self):
        return {"payload": self.payload, "digest": self.digest}


def freeze_preopen(report, *, frozen_at, session_open, capacity_fingerprint):
    cut, frozen, opening = stamp(report["cutoff"]), stamp(frozen_at), stamp(session_open)
    if not cut < frozen < opening:
        raise ValueError("PREOPEN_CUT_FREEZE_OPEN_ORDER_REQUIRED")
    if (cut.date() >= opening.date() or not report.get("sessions")
            or max(report["sessions"]) >= opening.date().isoformat()):
        raise ValueError("PREVIOUS_SESSION_ONLY")
    if not capacity_fingerprint:
        raise ValueError("CAPACITY_FINGERPRINT_REQUIRED")
    if report.get("mode") != "SHADOW" or report.get("real_orders_sent") != 0:
        raise ValueError("SHADOW_PAPER_REQUIRED")
    if (report.get("version") != VERSION or report.get("config_fingerprint") != digest(report["config"])
            or stamp(report["provenance"]["cutoff"]) != cut):
        raise ValueError("REPORT_PROVENANCE_OR_CONFIG_MISMATCH")
    # The row clocks must attest the same causal cut, not just the wrapper.
    if any(stamp(row["provenance"]["cutoff"]) != cut for row in report["rows"]):
        raise ValueError("ROW_CUTOFF_MISMATCH")
    if any(not _available(item, cut)[0] or _session(item["session"]) > cut.date().isoformat()
           for row in report["rows"] for item in row["provenance"]["evidence"]):
        raise ValueError("PREOPEN_FUTURE_EVIDENCE_REJECTED")
    payload = {**report, "version": VERSION, "frozen_at": frozen.isoformat(),
               "session_open": opening.isoformat(), "capacity_fingerprint": capacity_fingerprint,
               "config_fingerprint": report["config_fingerprint"], "inputs_hash": report["inputs_hash"]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return FrozenPreopen(encoded, digest(payload))


def _minute(row):
    value = _value(row, "minute_of_session")
    return int(value) if value is not None and value == int(value) else None


def _profile_at(records, minute, field, unit=None):
    candidates = [row for row in records if _minute(row) == minute
                  and (unit is None or str(row.get("volume_unit") or "").upper() == unit)]
    latest = max(candidates, key=lambda item: stamp(item["published_at"])) if candidates else None
    return _value(latest, field) if latest else None


def anomaly_events(history, observations, *, as_of, config=None):
    """Contemporaneous discovery triggers with same-minute historical profiles.

    All records carry explicit ``session``, ``minute_of_session`` and source
    clocks. RVOL uses cumulative_volume, acceleration uses differences across
    the same horizon, and price shocks use historical returns of that horizon.
    No return after as_of is inspected. Events only request prospectively
    deeper observation; warmup/signal/economics/risk belong to later stages.
    """
    cut, cfg = stamp(as_of), _configuration(config)
    history, observations = list(history), list(observations)
    historical, h_rejected = _partition(history, cut)
    current, o_rejected = _partition(observations, cut)
    events = []
    for key, records in sorted(current.items()):
        records = sorted(records, key=lambda item: (stamp(item["observed_at"]), stamp(item["published_at"])))
        latest = records[-1]
        session = latest["session"]
        # Historical profiles must be prior sessions, not today's eventual bar.
        profiles = defaultdict(list)
        for row in historical[key]:
            if row["session"] < session:
                profiles[row["session"]].append(row)
        minute = _minute(latest)
        current_rows = [row for row in records if row["session"] == session]
        window = cfg["anomaly_window_minutes"]
        unit = str(latest.get("volume_unit") or "").upper()
        unit = unit if unit in {"SHARES", "UNITS"} else None
        features = {"rvol": None, "volume_acceleration": None, "normalized_price_shock": None,
                    "inactive_to_active": None, "new_trades": None, "spread_compression": None,
                    "depth_improvement": None, "quote_age_seconds": (cut - stamp(latest["observed_at"])).total_seconds(),
                    "usable_observation_fraction": sum(row.get("usable") is True for row in current_rows) / len(current_rows),
                    "minute_of_session": minute, "profile_sessions": len(profiles), "volume_unit": unit}
        reasons = []
        if minute is not None:
            past_minute = minute - window
            before_candidates = [row for row in current_rows if _minute(row) == past_minute]
            before = before_candidates[-1] if before_candidates else None
            if unit:
                historical_cumulative = [_profile_at(rows, minute, "cumulative_volume", unit) for rows in profiles.values()]
                historical_cumulative = [value for value in historical_cumulative if value is not None]
                now_volume = _value(latest, "cumulative_volume")
                if len(historical_cumulative) >= cfg["min_profile_sessions"] and median(historical_cumulative) > 0 and now_volume is not None:
                    features["rvol"] = now_volume / median(historical_cumulative)
                    if features["rvol"] >= cfg["rvol_threshold"]:
                        reasons.append("RVOL_ANOMALY")
                deltas = []
                for profile in profiles.values():
                    start = _profile_at(profile, past_minute, "cumulative_volume", unit)
                    end = _profile_at(profile, minute, "cumulative_volume", unit)
                    if start is not None and end is not None and end >= start:
                        deltas.append(end - start)
                previous_volume = _value(before, "cumulative_volume") if before else None
                if (before and str(before.get("volume_unit") or "").upper() == unit and now_volume is not None
                        and previous_volume is not None and now_volume >= previous_volume
                        and len(deltas) >= cfg["min_profile_sessions"] and median(deltas) > 0):
                    features["volume_acceleration"] = (now_volume - previous_volume) / median(deltas)
                    if features["volume_acceleration"] >= cfg["acceleration_threshold"]:
                        reasons.append("VOLUME_ACCELERATION")
            returns = []
            for profile in profiles.values():
                start = _profile_at(profile, past_minute, "price")
                end = _profile_at(profile, minute, "price")
                if start is not None and end is not None and start > 0:
                    returns.append(end / start - 1)
            price, previous_price = _value(latest, "price"), _value(before, "price") if before else None
            if len(returns) >= max(2, cfg["min_profile_sessions"]) and price is not None and previous_price and stdev(returns) > 0:
                features["normalized_price_shock"] = abs(price / previous_price - 1 - sum(returns) / len(returns)) / stdev(returns)
                if features["normalized_price_shock"] >= cfg["price_shock_sigma"]:
                    reasons.append("PRICE_SHOCK")
            if before:
                trades, prior_trades = _value(latest, "trades"), _value(before, "trades")
                if trades is not None and prior_trades is not None and trades >= prior_trades:
                    features["new_trades"] = trades - prior_trades
                    features["inactive_to_active"] = prior_trades == 0 and trades > 0
                    if features["inactive_to_active"]:
                        reasons.append("INACTIVE_TO_ACTIVE")
                    elif features["new_trades"] > 0:
                        reasons.append("NEW_TRADES")
                for field, feature, threshold, reason in (
                    ("spread_bps", "spread_compression", cfg["spread_compression_fraction"], "SPREAD_COMPRESSION"),
                    ("depth_units", "depth_improvement", cfg["depth_improvement_fraction"], "DEPTH_IMPROVEMENT"),
                ):
                    start, end = _value(before, field), _value(latest, field)
                    depth_units_match = field != "depth_units" or (
                        str(before.get("depth_unit") or "").upper() == str(latest.get("depth_unit") or "").upper()
                        and str(latest.get("depth_unit") or "").upper() in {"SHARES", "UNITS"})
                    if start is not None and start > 0 and end is not None and depth_units_match:
                        change = (start - end) / start if field == "spread_bps" else (end - start) / start
                        features[feature] = change
                        if change >= threshold:
                            reasons.append(reason)
        fresh = features["quote_age_seconds"] <= cfg["max_quote_age_seconds"]
        useful = latest.get("usable") is True and features["usable_observation_fraction"] >= cfg["min_useful_fraction"]
        rejected = []
        if session != cut.date().isoformat():
            rejected.append("SOURCE_SESSION_NOT_CURRENT")
        if not fresh:
            rejected.append("STALE_QUOTES")
        if not useful:
            rejected.append("INSUFFICIENT_USEFUL_OBSERVATIONS")
        family, _ = _family(latest, key)
        if family not in EQUITY_FAMILIES:
            rejected.extend(["SPECIALIZED_LIFECYCLE", "STRATEGY_NOT_VALIDATED"])
        events.append({**dict(zip(IDENTITY_KEYS, key)), "identity": list(key), "family": family,
                       "as_of": cut.isoformat(), "at": latest["observed_at"], "observed_at": latest["observed_at"],
                       "reason_codes": sorted(set(reasons)), "rejection_reason_codes": rejected,
                       "features": features, "promotion_candidate": bool(reasons) and not rejected,
                       "entry_authority": False, "action": "PROMOTION_ONLY",
                       "warmup_required": True,
                       "provenance": {"source": latest.get("source"), "published_at": latest["published_at"],
                                      "profile_sessions": sorted(profiles), "cutoff": cut.isoformat()},
                       "cedear_features": _cedear_features({}, current_rows, cut) if family == "CEDEARS" else {}})
    return {"version": VERSION, "mode": "SHADOW", "as_of": cut.isoformat(), "config": cfg,
            "config_fingerprint": digest(cfg), "events": events,
            "inputs_hash": digest({"history": history, "observations": observations}),
            "rejected_inputs": {"history": h_rejected, "observations": o_rejected},
            "rejected_future_count": h_rejected.get("FUTURE_INFORMATION_REJECTED", 0) + o_rejected.get("FUTURE_INFORMATION_REJECTED", 0),
            "real_orders_sent": 0}
