"""Risk facts, exits and currencies without re-running financial guards."""
from .components import fields, notice
from .datasets import shadow_rows
from .view_common import (render_table, BALANCES, BALANCE_DETAIL, DAILY_RISK, RISK_DETAIL,
                          POSITIONS, POSITION_DETAIL, PERFORMANCE, PERFORMANCE_DETAIL)


def render(p, destination, tab):
    if tab == "resumen":
        return render_table(p, destination, tab, p.balances(), "Patrimonio & exposición por moneda", BALANCES, BALANCE_DETAIL,
                            note="ARS, USD, USD_MEP y USD_CCL se conservan separados. Valuación stale no certifica riesgo actual.")
    if tab == "limites":
        return render_table(p, destination, tab, p.risk(), "Límites: configuración vs uso actual", DAILY_RISK, RISK_DETAIL,
                            note="DailyRisk y los guards canónicos conservan la autoridad. Esta vista no recalcula límites ni autoriza operaciones.")
    if tab in {"posiciones", "liquidez", "exposicion"}:
        extra = fields("notional|Notional verificado|money;normalized_exposure|Exposición normalizada compatible|money;concentration|Concentración;exit_reader_reserve|Reserva exit reader;urgency|Urgencia EOD / expiry")
        return render_table(p, destination, tab, p.positions(), dict(destination.tabs)[tab], POSITIONS, (*POSITION_DETAIL, *extra), filters=True,
                            note="No sumar unidades incompatibles. Sin book fresco ejecutable, la liquidez de salida permanece NO_VERIFICADO.")
    if tab == "pnl":
        return render_table(p, destination, tab, p.performance(), "PnL / Drawdown por moneda · cierres", PERFORMANCE, PERFORMANCE_DETAIL,
                            note="Realizado de cierres, costos y neto se atribuyen por estrategia/familia/moneda. Drawdown exige una trayectoria válida.")
    return render_table(p, destination, tab, shadow_rows(p, "event-risk"), "Event Risk · autoridad explícita",
                        fields("symbol|Identidad;mode|SHADOW / BINDING|status;state|Evento|status;as_of|Reloj|time;reason|Motivo"),
                        fields("decision_effect|Efecto autorizado;source|Fuente;entry_authority|Autoridad de entrada"),
                        note="Una noticia en SHADOW no concede permiso ni veto BINDING. Autoridad desconocida = NO_VERIFICADO.")
