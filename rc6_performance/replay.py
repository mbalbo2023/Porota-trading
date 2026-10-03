"""Incremental, causal exit-policy comparison over one factual PAPER entry."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from .common import identity, number, stamp
from .costs import expected_round_trip_cost
from .shadow import usable_book


@dataclass(frozen=True)
class ExitPolicy:
    name: str
    stop_fraction: Decimal
    target_fraction: Decimal
    max_hold_seconds: int
    trailing_fraction: Decimal | None = None
    minimum_net_lock: Decimal | None = None

    def __post_init__(self):
        for key in ("stop_fraction", "target_fraction"):
            object.__setattr__(self, key, number(getattr(self, key), positive=True))
        if self.stop_fraction >= 1 or self.max_hold_seconds <= 0:
            raise ValueError("INVALID_EXIT_POLICY")
        if self.trailing_fraction is not None:
            object.__setattr__(self, "trailing_fraction", number(self.trailing_fraction, positive=True))
            if self.trailing_fraction >= 1:
                raise ValueError("INVALID_TRAILING_POLICY")
        if self.minimum_net_lock is not None:
            object.__setattr__(self, "minimum_net_lock", number(self.minimum_net_lock, nonnegative=True))


class ExitReplay:
    def __init__(self, entry, policy, fees, *, eod_at, session_close_at=None,
                 participation="0.1", slippage="0.0002", max_age_seconds=120):
        self.entry, self.policy, self.fees = dict(entry), policy, fees
        self.ident, self.opened = identity(entry), stamp(entry["opened_at"])
        if self.ident[1] not in {"ACCIONES", "CEDEARS", "ETFS"} and not entry.get("contract_cash_multiplier"):
            raise ValueError("CONTRACT_MULTIPLIER_REQUIRED")
        self.price, self.total = number(entry["entry_price"], positive=True), number(entry["quantity"], positive=True)
        self.factor = number(entry.get("contract_cash_multiplier", 1), positive=True)
        self.deadline = stamp(eod_at)
        if self.deadline <= self.opened:
            raise ValueError("FACTUAL_EOD_DEADLINE_REQUIRED")
        self.session_close = stamp(session_close_at) if session_close_at else self.deadline
        if self.session_close < self.deadline:
            raise ValueError("INVALID_SESSION_CLOSE")
        self.part, self.slip = number(participation, positive=True), number(slippage, nonnegative=True)
        if self.part > 1 or self.slip >= 1:
            raise ValueError("INVALID_EXECUTION_MODEL")
        self.max_age = max_age_seconds
        self.step = number(entry.get("quantity_step", 1), positive=True)
        if self.total % self.step:
            raise ValueError("INVALID_ENTRY_LOT")
        self.remaining, self.high, self.last_at = self.total, self.price, self.opened
        self.reason, self.detected_at, self.fills = None, None, []
        self.used_depth = {}
        self.last_rejection = None
        self.break_even_armed = False

    def advance(self, book, *, as_of):
        now = stamp(as_of)
        if now < self.last_at:
            raise ValueError("REPLAY_TIME_REVERSED")
        self.last_at = now
        if not self.remaining:
            return self.result()
        # A time condition is observed even when no executable book is available.
        if not self.reason and now >= self.deadline:
            self.reason = "EOD_PAPER"
        if not self.reason and now >= self.opened + timedelta(seconds=self.policy.max_hold_seconds):
            self.reason = "MAX_HOLD_PAPER"
        if self.reason and self.detected_at is None:
            self.detected_at = now.isoformat()
        if book is None:
            self.last_rejection = "source_unavailable"
            return self.result()
        if identity(book) != self.ident:
            raise ValueError("REPLAY_IDENTITY_MISMATCH")
        error = usable_book(book, now, max_age_seconds=self.max_age)
        if error:
            self.last_rejection = error
            return self.result()
        if stamp(book["book_at"]) < self.opened:
            self.last_rejection = "book_before_entry"
            return self.result()
        # Never turn an EOD event into an overnight executable fill.
        if now > self.session_close or now.date() != self.opened.date():
            self.last_rejection = "session_closed"
            return self.result()
        bid = number(book["bid"])
        self.high = max(self.high, bid)
        stop = self.price * (1 - self.policy.stop_fraction)
        if self.policy.trailing_fraction is not None:
            stop = max(stop, self.high * (1 - self.policy.trailing_fraction))
        if self.policy.minimum_net_lock is not None:
            trial = expected_round_trip_cost(self.price, bid * (1-self.slip), self.total,
                                            self.factor, self.fees, intraday_eligible=self.ident[4] == "BYMA")
            net = (bid * (1-self.slip)-self.price)*self.total*self.factor-trial["explicit_fees"]
            if net / (self.price*self.total*self.factor) >= self.policy.minimum_net_lock:
                self.break_even_armed = True
            if self.break_even_armed:
                # Once armed, a subsequent pullback cannot remove protection.
                # Above entry, the smaller-leg BYMA rebate leaves entry rights;
                # a market without that rebate pays the full entry rate.
                entry_rate = self.fees.low_rate if self.ident[4] == "BYMA" else self.fees.full_rate
                denominator = number(1-self.fees.full_rate, positive=True)
                break_even_bid = self.price*(1+entry_rate)/denominator/(1-self.slip)
                stop = max(stop, break_even_bid)
        if not self.reason:
            if bid <= stop:
                self.reason = "STOP_PAPER"
            elif bid >= self.price * (1 + self.policy.target_fraction):
                self.reason = "TAKE_PROFIT_PAPER"
            if self.reason:
                self.detected_at = now.isoformat()
        if not self.reason:
            return self.result()
        key = str(book["book_at"])
        capacity = max(Decimal(0), number(book["bid_size"], nonnegative=True)*self.part-self.used_depth.get(key, Decimal(0)))
        qty = (min(capacity, self.remaining) // self.step) * self.step
        if qty == 0:
            self.last_rejection = "insufficient_depth"
            return self.result()
        self.used_depth[key] = self.used_depth.get(key, Decimal(0)) + qty
        fill_price = (bid * (1-self.slip)).quantize(Decimal("0.0001"))
        costs = expected_round_trip_cost(self.price, fill_price, qty, self.factor, self.fees,
                                         intraday_eligible=self.ident[4] == "BYMA")
        gross = (fill_price-self.price)*qty*self.factor
        self.fills.append({"at": now.isoformat(), "quantity": qty, "price": fill_price,
                           "gross": gross, "costs": costs["explicit_fees"], "net": gross-costs["explicit_fees"]})
        self.remaining -= qty
        self.last_rejection = None
        return self.result()

    def result(self):
        return {"mode": "SHADOW", "policy": self.policy.name, "real_order_routes": [],
                "state": "CLOSED" if not self.remaining else "EXIT_PENDING" if self.reason else "OPEN",
                "reason": self.reason, "condition_detected_at": self.detected_at,
                "remaining": self.remaining, "fills": list(self.fills),
                "net": sum((f["net"] for f in self.fills), Decimal(0)),
                "last_rejection": self.last_rejection, "factual_entry": dict(self.entry),
                "eod_at": self.deadline.isoformat(), "execution_model": "PAPER_SENSITIVITY",
                "account_terms": "NO_VERIFICADO", "economic_edge_validated": False}
