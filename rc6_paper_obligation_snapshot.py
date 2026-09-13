"""Fail-closed obligation snapshot for the POROTA PAPER ledger only.

This is deliberately *not* a brokerage-account liability collector. It proves
that the existing PAPER cash engine has reconciled all cash-affecting objects it
models before the caucion sweep sizes free cash. Real-account deployment would
require a separate explicitly validated liability source and is out of scope.
"""
from __future__ import annotations

from typing import Iterable

from bs_instrument_contracts import aware_datetime, cash_currency
from di_caucion_cash_sweep_runtime_hf6 import CashObligation, ObligationSnapshot

SOURCE = "PAPER_LEDGER_RECONCILED_V1"


def _incomplete(at, reason: str) -> ObligationSnapshot:
    return ObligationSnapshot(
        observed_at=aware_datetime(at).isoformat(),
        source=f"{SOURCE}|{reason}",
        complete=False,
        obligations=(),
    )


def build_paper_only_obligation_snapshot(
    broker,
    *,
    as_of,
    currency: str,
    liquidity_deadline,
    additional_obligations: Iterable[CashObligation] = (),
) -> ObligationSnapshot:
    """Reconcile the PAPER ledger and return a complete snapshot or fail closed.

    The broker cash engine already deducts committed spot positions, caucion
    principal/upfront fees and excludes unsettled sale proceeds. This producer
    therefore emits only explicitly supplied extra PAPER obligations; it never
    invents a percentage reserve.
    """
    at = aware_datetime(as_of, "obligation snapshot time")
    deadline = aware_datetime(liquidity_deadline, "obligation liquidity deadline")
    if deadline < at:
        return _incomplete(at, "LIQUIDITY_DEADLINE_BEFORE_SNAPSHOT")
    try:
        ccy = cash_currency(currency)
        extras = tuple(additional_obligations or ())
        if any(not isinstance(item, CashObligation) for item in extras):
            return _incomplete(at, "ADDITIONAL_OBLIGATION_INVALID")
        with broker.store.connect() as c:
            c.execute("BEGIN")
            state = c.execute(
                "SELECT mode,real_orders_sent FROM observer_state WHERE id=1"
            ).fetchone()
            if not state:
                return _incomplete(at, "OBSERVER_STATE_MISSING")
            if str(state["mode"]) != "PRODUCTION_PAPER":
                return _incomplete(at, "MODE_NOT_PRODUCTION_PAPER")
            if int(state["real_orders_sent"]) != 0:
                return _incomplete(at, "REAL_ORDERS_SENT_NONZERO")

            # One call validates/reconciles spot positions, settlement credits
            # and caucion cash effects under the same read transaction. We do
            # not use the resulting amount here; the sweep re-reads execution
            # cash under its own transaction immediately before allocation.
            broker._cash(at, ccy, connection=c, for_execution=True)
            broker.cauciones.positions(connection=c)

        obligations = tuple(
            item for item in extras
            if item.currency == ccy and aware_datetime(item.due_at) <= deadline
        )
        return ObligationSnapshot(
            observed_at=at.isoformat(),
            source=SOURCE,
            complete=True,
            obligations=obligations,
        )
    except Exception as exc:
        return _incomplete(at, f"PAPER_LEDGER_RECONCILIATION_FAILED:{type(exc).__name__}")
