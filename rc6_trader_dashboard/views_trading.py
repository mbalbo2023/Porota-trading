"""Family-specific fields never fall back to an equity signal."""
from .components import fields, notice
from .datasets import shadow_rows, family_summary
from .view_common import render_table, CATALOG, CATALOG_DETAIL, OPPORTUNITIES, OPPORTUNITY_DETAIL

FAMILY_GROUPS = {
    "equity-spot": ("ACCIONES", "CEDEARS", "CEDEAR", "ETF", "ETFS"),
    "scalping": ("ACCIONES", "CEDEARS", "CEDEAR", "ETF", "ETFS"),
    "renta-fija": ("BONOS", "LETRAS", "ON", "OBLIGACIONES_NEGOCIABLES"),
    "derivados": ("OPCIONES", "FUTUROS"),
    "cauciones": ("CAUCIONES", "CAUCION"),
    "tesoreria": ("FCI",),
}
FAMILY_DETAILS = {
    "equity-spot": fields("metadata.spread|Spread;metadata.depth|Profundidad;metadata.samples|Muestras|number;metadata.momentum|Momentum;metadata.rvol|RVOL;metadata.microstructure|Microestructura;metadata.signal|Señal|status;metadata.economics|Economics|status;metadata.risk|Risk|status;metadata.exits|Salidas;metadata.cedear_ratio|Ratio CEDEAR;metadata.underlying|Underlying CEDEAR;metadata.underlying_clock|Reloj underlying|time"),
    "scalping": fields("metadata.intraday_contract|Contrato intradía|status;metadata.warmup_progress|Warmup;metadata.achieved_cadence|Cadencia lograda;metadata.spread|Spread;metadata.depth|Profundidad;metadata.economics|Economics|status;metadata.maxhold_at|MaxHold|time;metadata.eod_at|EOD|time;metadata.paper_positions|Posiciones PAPER"),
    "renta-fija": fields("metadata.nominal_unit|Convención nominal;metadata.price_convention|Convención de precio;metadata.maturity|Vencimiento|time;metadata.coupon|Cupón;metadata.cashflows|Cashflows verificados;metadata.yield|TIR validada;metadata.duration|Duration validada;metadata.parity|Paridad;metadata.accrued|Devengado|money;metadata.liquidity|Liquidez;metadata.spread|Spread;metadata.depth|Profundidad;metadata.carry|Carry;metadata.strategy_status|Estado de estrategia|status"),
    "derivados": fields("metadata.contract|Contrato exacto;metadata.underlying|Underlying;metadata.right|Call / put;metadata.strike|Strike|money;metadata.expiry|Expiry|time;metadata.multiplier|Multiplicador;metadata.tick|Tick;metadata.iv|IV fresca/validada;metadata.greeks|Greeks frescos/validados;metadata.open_interest|OI fresco/validado;metadata.margin|Semántica de margen/reserva;metadata.variation|Variación de futuros;metadata.lifecycle|Expiry/EOD lifecycle;metadata.entry_authority|Autoridad especializada"),
    "cauciones": fields("metadata.settled_free_cash|Caja libre liquidada|money;metadata.rate|Tasa;metadata.tenor|Plazo;metadata.minimum|Mínimo verificado;metadata.step|Incremento verificado;metadata.costs|Costos|money;metadata.maturity|Vencimiento|time;metadata.opportunity_cost|Costo de oportunidad|money;metadata.sweep_state|Estado allocation/sweep|status;metadata.strategy_status|Estado de estrategia|status"),
    "tesoreria": fields("metadata.nav_clock|NAV clock|time;metadata.subscription_state|Suscripción|status;metadata.redemption_state|Rescate|status;metadata.liquidity_horizon|Horizonte de liquidez;metadata.strategy_status|Estado de estrategia|status"),
}
STRATEGIES = fields("family|Familia;strategy|Estrategia;status|Estado de estrategia|status;entry_authority|Autoridad de entrada;ready|RUNTIME_READY|number")
STRATEGY_DETAIL = fields("lifecycle_owner|Lifecycle owner;eligible|Eligible|number;tradeable|Tradeable|number;hot|HOT|number;cadence|Cadencia;as_of|Última evaluación|time;open_positions|Posiciones abiertas|number;trades_today|Operaciones hoy|number;net_pnl|PnL neto|money;blocker|Principal bloqueo;source_authority|Autoridad de fuente")


def render(p, destination, tab):
    if tab == "resumen":
        page = shadow_rows(p, "strategies")
        if not page.rows:
            page = family_summary(p)
        return render_table(p, destination, tab, page, "Estrategias & lifecycle por familia", STRATEGIES, STRATEGY_DETAIL,
                            note="ACTIVE_PAPER, SHADOW, OBSERVE_ONLY y NO_VERIFICADO son estados distintos. Readiness no concede entry_authority.")
    if tab == "cauciones":
        decisions = p.filters.get("caucion_view") == "decisiones"
        selected = "decisiones" if decisions else "colocaciones"
        links = "".join(
            f"<a class='button' href='/trading/cauciones?caucion_view={key}' "
            f"{'aria-current=page' if selected == key else ''}>{label}</a>"
            for key, label in (("colocaciones", "Colocaciones PAPER"), ("decisiones", "Decisiones / HOLD")))
        lead = (notice("Cauciones simuladas: caja liquidada, tasa/plazo, reserva y riesgo se evalúan antes de colocar. "
                       "Un registro PAPER no acredita una orden ni una tasa ejecutable actual.")
                + f"<nav class='pager' aria-label='Vistas de cauciones'>{links}</nav>"
                + "<p class='source-line'>Catálogo y contrato: <a href='/instrumentos/buscador?family=CAUCIONES'>CAUCIONES</a> "
                  "· <a href='/instrumentos/buscador?family=CAUCION'>CAUCION</a>. PPI es identidad primaria; "
                  "IOL es complementario. Sin cotización fresca verificada: NO_VERIFICADO.</p>")
        if decisions:
            return lead + render_table(
                p, destination, tab, p.cauciones(decisions=True), "Decisiones de asignación · incluso HOLD",
                fields("evaluated_at|Evaluado|time;status|Resultado PAPER|status;reason|Motivo;principal|Principal seleccionado|money;paper_id|Colocación PAPER"),
                fields("request_id|Solicitud;selected|Oferta seleccionada;candidates|Ofertas evaluadas;entry_authority|Autoridad"),
                note="HOLD es una decisión durable; no equivale a una colocación ni a una falla de proveedor.")
        return lead + render_table(
            p, destination, tab, p.cauciones(), "Colocaciones caución · ledger PAPER",
            fields("opened_at|Apertura|time;status|Estado|status;currency|Moneda;principal|Principal|money;tna_percent|TNA (%)|percent;maturity_at|Vencimiento|time"),
            fields("paper_id|ID PAPER;request_id|Solicitud;instrument_id|Instrumento;source|Fuente de oferta;interest_days|Días de interés|number;day_count_basis|Base días|number;gross_interest|Interés bruto|money;total_fees|Costos|money;net_interest|Interés neto estimado|money;fee_payment|Pago de gastos;settled_at|Liquidación|time;terms|Términos conservados;freshness|Frescura de tasa actual|status"),
            note="TNA registrada en la colocación (fracción × 100); no representa cotización actual. Interés neto = bruto menos costos registrados.")
    note = {"equity-spot": "ACCIONES / CEDEARS / ETF: signal, economics y risk conservan autoridades independientes.",
            "scalping": "Scalping: HOT/WARM/DISCOVERY observan; warmup no reemplaza economics/risk ni MaxHold/EOD.",
            "renta-fija": "Renta fija usa convención nominal/precio y settlement propios. No hay fallback a momentum equity.",
            "derivados": "Opciones / futuros requieren contrato exacto y lifecycle especializado; IV/Greeks/OI no verificados no habilitan entradas.",
            "tesoreria": "FCI usa NAV y plazos de suscripción/rescate; no se enruta por momentum scanner. Cauciones tiene su vista propia."}[tab]
    page = p.catalog(families=FAMILY_GROUPS[tab])
    return render_table(p, destination, tab, page, dict(destination.tabs)[tab], CATALOG,
                        (*FAMILY_DETAILS[tab], *CATALOG_DETAIL), filters=True, catalog=True, note=note)
