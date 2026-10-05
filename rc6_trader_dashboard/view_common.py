"""Trader-oriented table contracts shared by pure views."""
from .components import fields, notice, table, filters_form

OPPORTUNITIES = fields("rank|Prioridad|number;symbol|Ticker;state|Observación|status;strategy|Estrategia;tradeability|Tradeability · no direccional|score;reason|Motivo principal")
OPPORTUNITY_DETAIL = fields("family|Familia;warmup|Warmup;freshness|Freshness|status;signal|Señal|status;economics|Economics|status;risk|Risk|status;entry_authority|Autoridad de entrada;rank_components|Componentes del rank;selected_at|Seleccionado|time;promoted_at|Promoción|time;demoted_at|Democión|time;last_useful_observation_at|Última observación útil|time;usable_observation_fraction|Fracción útil;revisit_seconds|Revisit planificado (s)|number;achieved_revisit_seconds|Revisit logrado (s)|number;discovery_age|Discovery age (s)|number;book_at|Reloj del book|time;pipeline|Cadena signal/economics/risk;rejection_reason|Reason codes;preopen_digest|Preopen digest;configuration_fingerprint|Config fingerprint;generation_id|Generación comprometida;provenance|Provenance")
POSITIONS = fields("symbol|Posición;strategy|Estrategia;freshness|Mark freshness|status;quantity|Cantidad original|number;entry_price|Entrada|money;exit_state|Supervisor de salida|status")
POSITION_DETAIL = fields("ledger|Ledger;status|Estado familiar|status;cash_multiplier|Multiplicador cash|number;margin_reserved|Reserva PAPER|money;reserve_policy|Policy reserva vs margen broker;variation_realized|Variation realizada|money;settlement_base_price|Base settlement|money;cash_effect|Efecto cash al corte|money;as_of|Corte exacto|time;side|Lado;unit|Unidad;remaining_quantity|Cantidad remanente verificada|number;mark|Mark · no ejecutable sin book verificado|money;executable_mark|Ejecutabilidad|status;unrealized_pnl|PnL no realizado medido|money;partial_sales|Realizado parcial (ledger);entry_cost|Costos de entrada|money;exit_cost|Costos de salida|money;time_in_position_seconds|Tiempo en posición (s)|number;stop_price|Stop|money;target_price|Target|money;stop_distance|Distancia al stop;target_distance|Distancia al target;maxhold_at|MaxHold hasta|time;maxhold_remaining|MaxHold restante (s)|number;eod_at|EOD hasta|time;eod_remaining|EOD restante (s)|number;mark_as_of|Reloj del mark|time;bid_depth|Liquidez / bid depth;supervised_at|Última supervisión|time;exit_due_at|Salida pendiente hasta|time;exit_reason|Bloqueo de salida;exit_cause|Causa durable de salida;exit_freshness|Frescura intent|status;exit_attempts|Intentos de salida|number;recorded_exit_state|Estado intent previo|status;workers|Estado de lectores/supervisión;exit_reader_state|Lector de salidas|status;risk_contribution|Contribución al riesgo;lifecycle|Lifecycle especializado;mfe|MFE · trayectoria validada;mae|MAE · trayectoria validada")
CLOSES = fields("symbol|Cierre;closed_at|Salida|time;close_reason|Motivo de salida;net_pnl|PnL neto|money;strategy|Estrategia;currency|Moneda")
CLOSE_DETAIL = fields("opened_at|Entrada|time;time_in_position_seconds|Duración (s)|number;gross_pnl|PnL bruto|money;entry_cost|Costo entrada|money;exit_cost|Costo salida|money;stop_price|Stop|money;target_price|Target|money;maxhold_at|MaxHold|time;eod_at|EOD|time;expected_economics|Resultado vs economics esperado;mfe|MFE · trayectoria validada;mae|MAE · trayectoria validada")
DECISIONS = fields("as_of|Hora|time;symbol|Identidad;final|Resultado factual|status;strategy|Estrategia;economics|Economics|status;reason|Motivo principal")
DECISION_DETAIL = fields("previous_state|Estado previo|status;signal|Señal|status;risk|Risk|status;score|Score · NO PROBABILIDAD|score;features|Variables causales;expected_cost|Costo esperado|money;provider_clock|Reloj provider|time;receipt_clock|Reloj recibido|time;liquidity|Liquidez / profundidad;risk_usage|Uso del riesgo;decision_key|Evidence ID;evidence_phase|Fase de captura;evidence_state|Integridad snapshot|status;admission_state|Receipt financiero|status;admission_reason|Estado de lectura receipt;admission_snapshot_key|Receipt key;admission_snapshot_sha256|Receipt SHA256;admission_at|Admisión al corte|time;admission_captured_at|Captura durable|time;entry_fill_recorded_at|Fill registrado dentro de TX|time;entry_fill_committed_at|Fill comprometido posterior a COMMIT|time;evidence|Evidencia inmutable;gates|Gate factual")
CATALOG = fields("symbol|Ticker;family|Familia;readiness|RUNTIME_READY|status;market|Mercado;currency|Moneda;settlement|Settlement")
CATALOG_DETAIL = fields("description|Descripción;status|Estado del catálogo|status;readiness_as_of|Readiness as_of|time;last_seen_at|Catálogo as_of|time;strategy_route|Ruta de estrategia;universe_state|Estado del universo|status;tradeability|Tradeability · NO PROBABILIDAD|score;metadata|Contrato / evidencia de catálogo;active_position|Posición activa;candidate|Candidato observado")
CAPACITY = fields("engine|Engine;policy_state|Selector / política|status;open_evidence|Evidencia OPEN|status;safe_capacity|Capacidad OPEN demostrada|number;recommended_capacity|Recomendación SHADOW|number;baseline|Baseline|number")
CAPACITY_DETAIL = fields("discovery|DISCOVERY|number;p50_age_seconds|Discovery age p50 (s)|number;p95_age_seconds|Discovery age p95 (s)|number;max_discovery_age|Discovery age max (s)|number;touched_fraction_in_window|Touched fraction;fresh_useful_fraction|Useful observation fraction;distinct_observation_fraction|Distinct observation fraction;planned_revisit|Revisit planificado por identidad (s);achieved_revisit|Revisit logrado por identidad (s);scanner_capacity_rejects|Scanner capacity rejects|number;endpoint_budgets|Endpoint budgets;opened_priority|Prioridad OPENED / EXIT;latency_p50|Latencia p50 (ms)|number;latency_p95|Latencia p95 (ms)|number;capacity|Evidencia de capacidad;exit_capacity|Demanda / capacidad EXIT canónica;by_family_source|Coverage por familia/fuente;missed_late_discovery|Late/missed discovery causal")
WORKERS = fields("worker|Worker;state|Estado|status;as_of|Heartbeat|time;age_seconds|Age (s)|number;freshness|Freshness|status")
WORKER_DETAIL = fields("pid|PID child|number;restarts|Reinicios|number;spawn_failures|Fallos de spawn|number;generation_state|Generación comprometida|status;verification_level|Alcance de verificación del corte;generation_freshness|Frescura generación|status;generation_as_of|Generación as_of|time;operational_readiness|Readiness operacional|status;source_sha|Fuente SHA;candidate_tree_sha|Candidate tree;global_counters|Contadores globales PPI;exit_service|Demanda / presión de salidas;telemetry_retention|Ventana / pérdidas de telemetría;open_capacity|Capacidad OPEN|status;last_success_at|Último éxito|time;last_error|Último error;supervised_positions|Posiciones supervisadas;coverage_exception|Excepción de coverage;detail|Detalle técnico")
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
    form = filters_form(path, p.filters, catalog=catalog, positions="paper_positions" in page.source) if filters else ""
    return ((notice(note) if note else "") + form +
            table(page, title, columns, detail, path=path, filters=p.filters))


def committed_funnel(p):
    from .components import funnel, e, clock
    from .projection import funnel_cohort_id
    from urllib.parse import urlencode
    scoped = p.funnel_scope
    if not scoped.get("selected"):
        return funnel({}, scoped["reason"] or scoped["state"])
    scope = f"{scoped['label']} · {clock(scoped.get('as_of'))} · CURRENT.json"
    links = []
    for row in scoped["groups"][:10]:
        filters = {k: v for k, v in p.filters.items() if k not in {"offset", "cohort", "funnel_offset"}}
        filters.update(currency=row.get("currency"), channel=row.get("channel"))
        label = f"{row.get('currency')} / {row.get('channel')}"
        if "identity" in row:
            filters["cohort"] = funnel_cohort_id(row)
            label += f" · {row.get('symbol')} · {row.get('strategy_id')} · {row.get('hour_art')} ART · {filters['cohort'][:8]}"
        links.append(f"<a class='button' href='?{e(urlencode(filters))}'>{e(label)}</a>")
    offset, total = scoped.get("groups_offset", 0), scoped.get("total_groups", len(scoped["groups"]))
    params = {k: v for k, v in p.filters.items() if k not in {"offset", "funnel_offset"}}
    if offset:
        links.append(f"<a class='button' href='?{e(urlencode({**params, 'funnel_offset': max(0, offset - 10)}))}'>Grupos anteriores</a>")
    if offset + len(scoped["groups"]) < total:
        links.append(f"<a class='button' href='?{e(urlencode({**params, 'funnel_offset': offset + 10}))}'>Mostrar 10 grupos más</a>")
    scope += f" · grupos {offset + 1 if scoped['groups'] else 0}–{offset + len(scoped['groups'])} de {total}"
    return f"<nav class='pager' aria-label='Grupos del embudo'>{''.join(links)}</nav>" + funnel(scoped["counts"], scope)
