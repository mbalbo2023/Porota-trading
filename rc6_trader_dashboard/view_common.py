"""Trader-oriented table contracts shared by pure views."""
from .components import fields, notice, table, filters_form

OPPORTUNITIES = fields("rank|Prioridad|number;symbol|Ticker;state|Observación|status;strategy|Estrategia;tradeability|Tradeability · no direccional|score;reason|Motivo principal")
OPPORTUNITY_DETAIL = fields("family|Familia;warmup|Warmup;freshness|Freshness|status;signal|Señal|status;economics|Economics|status;risk|Risk|status;entry_authority|Autoridad de entrada;rank_components|Componentes del rank;selected_at|Seleccionado|time;promoted_at|Promoción|time;demoted_at|Democión|time;last_useful_observation_at|Última observación útil|time;usable_observation_fraction|Fracción útil;revisit_seconds|Revisit planificado (s)|number;achieved_revisit_seconds|Revisit logrado (s)|number;discovery_age|Discovery age (s)|number;book_at|Reloj del book|time;pipeline|Cadena signal/economics/risk;rejection_reason|Reason codes;preopen_digest|Preopen digest;configuration_fingerprint|Config fingerprint;generation_id|Generación comprometida;provenance|Provenance")
POSITIONS = fields("symbol|Posición;strategy|Estrategia;freshness|Mark freshness|status;quantity|Cantidad original|number;entry_price|Entrada|money;exit_state|Supervisor de salida|status")
POSITION_DETAIL = fields("side|Lado;unit|Unidad;remaining_quantity|Cantidad remanente verificada|number;mark|Mark · no ejecutable sin book verificado|money;executable_mark|Ejecutabilidad|status;unrealized_pnl|PnL no realizado medido|money;partial_sales|Realizado parcial (ledger);entry_cost|Costos de entrada|money;exit_cost|Costos de salida|money;time_in_position_seconds|Tiempo en posición (s)|number;stop_price|Stop|money;target_price|Target|money;stop_distance|Distancia al stop;target_distance|Distancia al target;maxhold_at|MaxHold hasta|time;maxhold_remaining|MaxHold restante (s)|number;eod_at|EOD hasta|time;eod_remaining|EOD restante (s)|number;mark_as_of|Reloj del mark|time;bid_depth|Liquidez / bid depth;supervised_at|Última supervisión|time;exit_due_at|Salida pendiente hasta|time;exit_reason|Bloqueo de salida;workers|Estado de lectores/supervisión;exit_reader_state|Lector de salidas|status;risk_contribution|Contribución al riesgo;lifecycle|Lifecycle especializado;mfe|MFE · trayectoria validada;mae|MAE · trayectoria validada")
CLOSES = fields("symbol|Cierre;closed_at|Salida|time;close_reason|Motivo de salida;net_pnl|PnL neto|money;strategy|Estrategia;currency|Moneda")
CLOSE_DETAIL = fields("opened_at|Entrada|time;time_in_position_seconds|Duración (s)|number;gross_pnl|PnL bruto|money;entry_cost|Costo entrada|money;exit_cost|Costo salida|money;stop_price|Stop|money;target_price|Target|money;maxhold_at|MaxHold|time;eod_at|EOD|time;expected_economics|Resultado vs economics esperado;mfe|MFE · trayectoria validada;mae|MAE · trayectoria validada")
DECISIONS = fields("as_of|Hora|time;symbol|Identidad;final|Resultado factual|status;strategy|Estrategia;economics|Economics|status;reason|Motivo principal")
DECISION_DETAIL = fields("previous_state|Estado previo|status;signal|Señal|status;risk|Risk|status;score|Score · NO PROBABILIDAD|score;features|Variables causales;expected_cost|Costo esperado|money;provider_clock|Reloj provider|time;receipt_clock|Reloj recibido|time;liquidity|Liquidez / profundidad;risk_usage|Uso del riesgo;decision_key|Evidence ID;evidence|Evidencia inmutable;gates|Gate factual")
CATALOG = fields("symbol|Ticker;family|Familia;readiness|RUNTIME_READY|status;market|Mercado;currency|Moneda;settlement|Settlement")
CATALOG_DETAIL = fields("description|Descripción;status|Estado del catálogo|status;readiness_as_of|Readiness as_of|time;last_seen_at|Catálogo as_of|time;strategy_route|Ruta de estrategia;universe_state|Estado del universo|status;tradeability|Tradeability · NO PROBABILIDAD|score;metadata|Contrato / evidencia de catálogo;active_position|Posición activa;candidate|Candidato observado")
CAPACITY = fields("engine|Engine;state|Evidencia OPEN|status;safe_capacity|Capacidad demostrada|number;baseline|Baseline;hot|HOT|number;warm|WARM|number")
CAPACITY_DETAIL = fields("discovery|DISCOVERY|number;p50_age_seconds|Discovery age p50 (s)|number;p95_age_seconds|Discovery age p95 (s)|number;max_discovery_age|Discovery age max (s)|number;touched_fraction_in_window|Touched fraction;fresh_useful_fraction|Useful observation fraction;distinct_observation_fraction|Distinct observation fraction;planned_revisit|Revisit planificado (s)|number;achieved_revisit|Revisit logrado (s)|number;scanner_capacity_rejects|Scanner capacity rejects|number;endpoint_budgets|Endpoint budgets;opened_priority|Prioridad OPENED / EXIT;latency_p50|Latencia p50 (ms)|number;latency_p95|Latencia p95 (ms)|number;capacity|Evidencia de capacidad;by_family_source|Coverage por familia/fuente;missed_late_discovery|Late/missed discovery causal")
WORKERS = fields("worker|Worker;state|Estado|status;as_of|Heartbeat|time;age_seconds|Age (s)|number;freshness|Freshness|status")
WORKER_DETAIL = fields("last_success_at|Último éxito|time;last_error|Último error;supervised_positions|Posiciones supervisadas;coverage_exception|Excepción de coverage;detail|Detalle técnico")
SOURCES = fields("component|Fuente / scope;state|Estado|status;role|Autoridad;freshness|Freshness|status;as_of|Chequeo|time")
SOURCE_DETAIL = fields("provider_clock|Provider / event clock|time;receipt_clock|Receipt / capture clock|time;lkg|Last Known Good|time;scope|Campos/familias afectados;conflicts|Conflictos;detail|Diagnóstico scoped")
BALANCES = fields("currency|Moneda;equity|Patrimonio PAPER|money;valuation_state|Calidad de valuación|status;cash|Caja disponible|money;exposure|Exposición|money;unrealized_pnl|PnL no realizado|money")
BALANCE_DETAIL = fields("reserved|Caja reservada|money;pending_proceeds|Cobros pendientes|money;caucion_principal|Principal caución|money;caucion_accrued|Caución devengada|money;realized_pnl|Realizado acumulado|money;measured_at|Valuación as_of|time;freshness|Freshness|status;stale_positions|Marks stale|number;exposure_limit|Límite de exposición|money;concentration|Concentración;drawdown|Drawdown|money")
DAILY_RISK = fields("currency|Moneda;state|Riesgo diario|status;daily_pnl|PnL diario de DailyRisk|money;loss_budget|Presupuesto de pérdida|money;limit_pct|Límite hard (%)|percent")
RISK_DETAIL = fields("baseline_equity|Patrimonio base diario|money;last_equity|Patrimonio actual|money;latched_at|Latched desde|time;evaluated_at|Evaluado|time;detail|Guard / motivo;daily_consumed|Riesgo usado|money;soft_stop|Soft stop;position_cap|Position cap;emergency_cap|Emergency cap;family_guards|Guards por familia;risk_per_trade|Riesgo por operación|money")
PERFORMANCE = fields("currency|Moneda;strategy|Estrategia;net_pnl|PnL neto|money;sample_size|Muestra (trades)|number;costs|Costos|money;profit_factor|Profit factor|number")
PERFORMANCE_DETAIL = fields("family|Familia;gross_pnl|PnL bruto|money;win_rate|Win rate · con muestra|percent;expectancy|Expectancy|money;avg_win|Ganancia media|money;avg_loss|Pérdida media|money;drawdown|Drawdown|money;holding_time|Duración media|number;edge|Edge / OOS;as_of|Último cierre|time")


def render_table(p, destination, tab, page, title, columns, detail=(), *, filters=False, catalog=False, note=""):
    from .navigation import view_path
    path = view_path(destination, tab)
    form = filters_form(path, p.filters, catalog=catalog, positions=page.source == "paper_positions") if filters else ""
    return ((notice(note) if note else "") + form +
            table(page, title, columns, detail, path=path, filters=p.filters))


def committed_funnel(p):
    from .components import funnel, e, clock
    from urllib.parse import urlencode
    report = p.shadow["report"].get("operational_funnel", {})
    groups = report.get("by_currency_channel", []) if isinstance(report, dict) else []
    groups = [r for r in groups if isinstance(r, dict) and isinstance(r.get("stages"), dict)]
    if not groups:
        return funnel({}, p.shadow["state"])
    selected = next((r for r in groups if r.get("channel") == p.filters.get("channel") and r.get("currency") == p.filters.get("currency")), None)
    selected = selected or next((r for r in groups if r.get("channel") == "NATIVE_FACTUAL"), groups[0])
    names = {"READY": "CATALOG_READY", "ELIGIBLE": "STRATEGY_ELIGIBLE", "TRADEABLE": "TRADEABLE",
             "DISCOVERY": "DISCOVERY_TOUCHED", "WARM": "WARM", "HOT": "HOT", "SIGNAL": "SIGNAL_CANDIDATE",
             "ECONOMICS": "ECONOMICS_PASS", "RISK": "RISK_PASS", "PAPER": "PAPER_OPENED"}
    counts = {stage: selected["stages"].get(native) for stage, native in names.items()}
    scope = f"{selected.get('currency')} / {selected.get('channel')} · {clock(report.get('as_of'))} · CURRENT.json"
    links = "".join(f"<a class='button' href='?{e(urlencode({'currency': r.get('currency'), 'channel': r.get('channel')}))}'>{e(r.get('currency'))} / {e(r.get('channel'))}</a>" for r in groups[:10])
    return f"<nav class='pager' aria-label='Scope del embudo'>{links}</nav>" + funnel(counts, scope)
