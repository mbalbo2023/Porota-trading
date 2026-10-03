"""Point-in-time labels, empirical movement estimates and same-input SHADOW gates.

Labels are outcomes, never input features. A fitted model must be frozen before
the evaluated decision; the decision API has no access to future snapshots.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from .common import stamp, number, identity, digest
from .costs import expected_round_trip_cost


def usable_book(book, at, *, max_age_seconds=120, minimum_depth=0):
    try:
        identity(book)
        now, received, sourced = stamp(at), stamp(book["observed_at"]), stamp(book["book_at"])
        if not sourced <= received <= now:
            return "future_quote"
        if (now - received).total_seconds() > max_age_seconds or (now - sourced).total_seconds() > max_age_seconds:
            return "stale_book"
        bid, ask = number(book["bid"], positive=True), number(book["ask"], positive=True)
        if ask < bid:
            return "crossed_book"
        if number(book["bid_size"], nonnegative=True) < number(minimum_depth, nonnegative=True):
            return "insufficient_depth"
        if not book.get("source", book.get("metadata_source")):
            return "source_unavailable"
    except (ValueError, TypeError, KeyError):
        return "invalid_quote"
    return ""


def forward_labels(entry, books, horizons, *, as_of, tolerance_seconds=120):
    at, available = stamp(entry["decision_at"]), stamp(as_of)
    ident, price = identity(entry), number(entry["entry_price"], positive=True)
    if at > available:
        raise ValueError("DECISION_NOT_YET_AVAILABLE")
    eligible = []
    for book in books:
        received = stamp(book["observed_at"])
        if identity(book) == ident and at < stamp(book["book_at"]) <= received <= available:
            if not usable_book(book, received):
                eligible.append(book)
    eligible.sort(key=lambda b: stamp(b["observed_at"]))
    results = []
    for horizon in horizons:
        if isinstance(horizon, bool) or horizon <= 0:
            raise ValueError("POSITIVE_HORIZON_REQUIRED")
        target = at + timedelta(seconds=horizon)
        points = [b for b in eligible if at < stamp(b["observed_at"]) <= target]
        future = next((b for b in eligible if target <= stamp(b["observed_at"]) <= target + timedelta(seconds=tolerance_seconds)), None)
        if target > available or future is None:
            results.append({"horizon_seconds": horizon, "status": "NO_VERIFICADO", "reason": "horizon_or_book_unavailable"})
            continue
        sampled = points if future in points else points + [future]
        returns = [number(b["bid"]) / price - 1 for b in sampled]
        results.append({"horizon_seconds": horizon, "status": "MEDIDO", "decision_at": entry["decision_at"],
                        "label_available_at": future["observed_at"], "identity": ident,
                        "gross_forward_return": number(future["bid"]) / price - 1,
                        "mfe_observed": max(returns), "mae_observed": min(returns),
                        "observations": len(sampled), "score": entry.get("score"),
                        "source": future.get("source", future.get("metadata_source")),
                        "price_anchor": "EXECUTED_ENTRY_TO_FUTURE_BID",
                        "execution_guaranteed": False})
    return results


@dataclass(frozen=True)
class MovementModel:
    training_cutoff: str
    horizon_seconds: int
    family: str
    currency: str
    observations: int
    expected_gross_return: Decimal
    provenance: str
    entry_anchor: str = "EXECUTED_ENTRY_TO_FUTURE_BID"


def fit_movement(labels, *, training_cutoff, horizon_seconds, family, currency, minimum_observations):
    cutoff = stamp(training_cutoff)
    selected, seen = [], set()
    for label in labels:
        if label.get("status") != "MEDIDO" or label.get("horizon_seconds") != horizon_seconds:
            continue
        if stamp(label["label_available_at"]) > cutoff:
            raise ValueError("FUTURE_LABEL_IN_TRAINING")
        if stamp(label["decision_at"]) >= stamp(label["label_available_at"]):
            raise ValueError("INVALID_LABEL_ORDER")
        if tuple(label["identity"])[1] != family or tuple(label["identity"])[3] != currency:
            continue
        if label.get("price_anchor") != "EXECUTED_ENTRY_TO_FUTURE_BID" or not label.get("source"):
            raise ValueError("LABEL_PROVENANCE_REQUIRED")
        key = (tuple(label["identity"]), label["decision_at"], label["horizon_seconds"])
        if key in seen:
            raise ValueError("DUPLICATE_TRAINING_LABEL")
        seen.add(key)
        selected.append(number(label["gross_forward_return"]))
    if minimum_observations < 1 or len(selected) < minimum_observations:
        raise ValueError("INSUFFICIENT_OUT_OF_SAMPLE_DESIGN_DATA")
    return MovementModel(training_cutoff, horizon_seconds, family, currency, len(selected),
                         sum(selected, Decimal(0)) / len(selected), digest(labels))


def economics_gate(book, *, decision_at, eod_at, quantity, multiplier, model, fees,
                   minimum_edge=0, exit_slippage=Decimal("0.0002"),
                   entry_slippage=Decimal("0.0002"), max_age_seconds=120, participation=Decimal("0.1")):
    result = {"mode": "SHADOW", "decision_effect": "NONE", "real_order_routes": [],
              "status": "NO_VERIFICADO", "accepted": None}
    error = usable_book(book, decision_at, max_age_seconds=max_age_seconds)
    if error:
        return result | {"status": "REJECTED", "accepted": False, "reason": error}
    at, deadline = stamp(decision_at), stamp(eod_at)
    if model is None:
        return result | {"reason": "expected_move_unverified"}
    if stamp(model.training_cutoff) >= at:
        raise ValueError("MODEL_NOT_FROZEN_BEFORE_DECISION")
    if (model.family, model.currency) != (identity(book)[1], identity(book)[3]):
        return result | {"reason": "family_or_currency_model_unverified"}
    if model.entry_anchor != "EXECUTED_ENTRY_TO_FUTURE_BID":
        raise ValueError("MODEL_PRICE_ANCHOR_MISMATCH")
    if (deadline - at).total_seconds() < model.horizon_seconds:
        return result | {"status": "REJECTED", "accepted": False, "reason": "EOD_too_close"}
    qty, factor, part = number(quantity, positive=True), number(multiplier, positive=True), number(participation, positive=True)
    if part > 1:
        raise ValueError("INVALID_DEPTH_PARTICIPATION")
    if min(number(book["ask_size"], nonnegative=True), number(book["bid_size"], nonnegative=True)) * part < qty:
        return result | {"status": "REJECTED", "accepted": False, "reason": "insufficient_depth"}
    entry_slip, exit_slip = number(entry_slippage, nonnegative=True), number(exit_slippage, nonnegative=True)
    if exit_slip >= 1:
        raise ValueError("INVALID_EXIT_SLIPPAGE")
    entry = number(book["ask"]) * (1 + entry_slip)
    expected_bid = entry * (1 + number(model.expected_gross_return))
    exit_fill = expected_bid * (1 - exit_slip)
    # Intraday same-identity/same-currency/same-settlement BYMA is explicit.
    intraday = at.date() == deadline.date() and book["market"] == "BYMA"
    costs = expected_round_trip_cost(entry, exit_fill, qty, factor, fees, intraday_eligible=intraday)
    gross = (exit_fill - entry) * qty * factor
    net = gross - costs["total_expected_friction"]
    edge = net / (entry * qty * factor)
    accepted = edge > number(minimum_edge)
    return result | {"status": "EVALUATED", "accepted": accepted,
                     "reason": "economic_edge_sufficient" if accepted else "economic_edge_insufficient",
                     "expected_net_edge": edge, "expected_gross_move": model.expected_gross_return,
                     "training_cutoff": model.training_cutoff, "model_provenance": model.provenance,
                     "model_observations": model.observations, "horizon_seconds": model.horizon_seconds,
                     "time_to_eod_seconds": (deadline - at).total_seconds(), "costs": costs,
                     "economic_edge_validated": False}


def evaluate_same_snapshot(snapshot, evaluators, *, decision_at):
    """No evaluator can mutate another variant's input; provenance is shared."""
    import json
    from .common import canonical
    payload = canonical(snapshot)
    result = {}
    for name, evaluator in evaluators.items():
        decision = evaluator(json.loads(payload), decision_at=decision_at)
        result[name] = {"mode": "SHADOW", "input_sha256": digest(snapshot),
                        "as_of": decision_at, "decision": decision, "real_order_routes": []}
    return result
