"""RC4-HF2 candidate: métricas económicas dimensionalmente coherentes para Forward Lab.

Módulo puro. No accede a broker, no abre órdenes y no modifica runtime.
Las métricas se expresan en retornos o PnL; nunca compara unidades de precio
contra bps/tasas sin normalizar.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


def _d(value, name: str) -> Decimal:
    try:
        result=Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(name+"_INVALID") from exc
    if not result.is_finite():
        raise ValueError(name+"_NONFINITE")
    return result


def expectancy(*, p_win, avg_win, avg_loss) -> Decimal:
    """Esperanza por operación en las mismas unidades de avg_win/avg_loss."""
    p=_d(p_win,"P_WIN")
    win=_d(avg_win,"AVG_WIN")
    loss=_d(avg_loss,"AVG_LOSS")
    if p < 0 or p > 1:
        raise ValueError("P_WIN_OUT_OF_RANGE")
    if win < 0 or loss < 0:
        raise ValueError("PAYOFF_MUST_BE_NONNEGATIVE_MAGNITUDE")
    return p*win-(Decimal("1")-p)*loss


def net_return(*, net_pnl, entry_notional) -> Decimal:
    pnl=_d(net_pnl,"NET_PNL")
    notional=_d(entry_notional,"ENTRY_NOTIONAL")
    if notional <= 0:
        raise ValueError("ENTRY_NOTIONAL_NONPOSITIVE")
    return pnl/notional


def executable_excursions_long(*, entry_ask, executable_exit_prices: Iterable) -> dict:
    """MFE/MAE long normalizados usando precios de salida ejecutables simulados.

    Para una posición larga la entrada conservadora es ask y cada salida
    observable debe ser un precio ejecutable (por ejemplo bid), no un last no
    ejecutable. El resultado queda en tasa, no en unidades de precio.
    """
    entry=_d(entry_ask,"ENTRY_ASK")
    if entry <= 0:
        raise ValueError("ENTRY_ASK_NONPOSITIVE")
    exits=[_d(x,"EXECUTABLE_EXIT") for x in executable_exit_prices]
    if not exits or any(x <= 0 for x in exits):
        raise ValueError("EXECUTABLE_EXITS_REQUIRED_POSITIVE")
    best=max(exits)
    worst=min(exits)
    return {
        "mfe_exec_return":(best-entry)/entry,
        "mae_exec_return":(worst-entry)/entry,
        "best_executable_exit":best,
        "worst_executable_exit":worst,
        "entry_ask":entry,
    }


def realized_expectancy(net_returns: Iterable) -> dict:
    values=[_d(x,"NET_RETURN") for x in net_returns]
    if not values:
        return {"n":0,"p_win":None,"avg_win":None,"avg_loss":None,"expectancy":None}
    wins=[x for x in values if x>0]
    losses=[-x for x in values if x<0]
    p=Decimal(len(wins))/Decimal(len(values))
    avg_win=sum(wins,Decimal("0"))/Decimal(len(wins)) if wins else Decimal("0")
    avg_loss=sum(losses,Decimal("0"))/Decimal(len(losses)) if losses else Decimal("0")
    return {
        "n":len(values),
        "p_win":p,
        "avg_win":avg_win,
        "avg_loss":avg_loss,
        "expectancy":expectancy(p_win=p,avg_win=avg_win,avg_loss=avg_loss),
    }


def pnl_concentration(rows: Iterable[tuple[str, object]]) -> dict:
    """Concentración del PnL positivo por símbolo, sin heurística top2/media."""
    by_symbol=defaultdict(lambda:Decimal("0"))
    for symbol,pnl in rows:
        by_symbol[str(symbol or "UNKNOWN")]+=_d(pnl,"PNL")
    positives=sorted((value,symbol) for symbol,value in by_symbol.items() if value>0)
    positives.reverse()
    total=sum((value for value,_ in positives),Decimal("0"))
    share1=(positives[0][0]/total if positives and total>0 else Decimal("0"))
    share2=(sum((x[0] for x in positives[:2]),Decimal("0"))/total if total>0 else Decimal("0"))
    return {
        "symbols":len(by_symbol),
        "positive_pnl_total":total,
        "share_top_1_positive_pnl":share1,
        "share_top_2_positive_pnl":share2,
        "top_positive_symbols":[symbol for _,symbol in positives[:2]],
    }
