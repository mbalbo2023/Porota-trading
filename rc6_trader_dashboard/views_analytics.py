"""Economics, samples, factual exits and explicitly limited experiments."""
from cg_paper_workspace import artifact_root
from .components import fields, funnel, metric, notice, e, clock
from .datasets import shadow_rows, artifact
from .view_common import render_table, PERFORMANCE, PERFORMANCE_DETAIL, CLOSES, CLOSE_DETAIL, committed_funnel


def _action4_card(p):
    """Show the already published PAPER audit; page views never run the job."""
    payload, state = p.bounded_file("reports/rc6_action4_auditoria_latest.json",
                                    root=artifact_root(p.store.path))
    safety = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
    local = (payload.get("status") == "PAPER_READ_ONLY" and
             safety.get("real_orders_sent") == 0 and safety.get("broker_routes_called") is False)
    weekly = (payload.get("scope") == "RC6" and payload.get("status") == "SUCCESS" and
              payload.get("source") == "/api/observer/state" and
              payload.get("database_access_from_action") == "NO" and
              payload.get("mode") == "PRODUCTION_PAPER" and
              type(payload.get("real_orders_sent")) is int and payload["real_orders_sent"] == 0)
    if state != "AVAILABLE" or not (local or weekly):
        return notice("Action 4 · NO_VERIFICADO: auditoría PAPER publicada ausente, inválida o fuera del límite de lectura.")
    window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
    origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
    separation = payload.get("separation") if isinstance(payload.get("separation"), dict) else {}
    rows = "".join(f"<dt>{e(label)}</dt><dd>{e(value if value is not None else 'NO_VERIFICADO')}</dd>"
                   for label, value in (("Desde", window.get("from_local")),
                                        ("Hasta", window.get("to_local")),
                                        ("Generado UTC", payload.get("generated_at_utc")),
                                        ("Run", origin.get("run_id")),
                                        ("Commit", origin.get("commit")),
                                        ("Cierres PAPER", separation.get("executed_closed")),
                                        ("Aperturas simuladas", separation.get("opened_simulated")),
                                        ("HOLD", separation.get("hold"))))
    return ("<section class='panel'><h2>Action 4 · auditoría publicada</h2>"
            "<p class='muted'>Recibo histórico PAPER; su fecha no certifica la rueda actual.</p>"
            f"<dl class='facts'>{rows}</dl>"
            "<a class='text-link' href='/api/reports/action4'>Descargar auditoría JSON autenticada</a></section>")


def render(p, destination, tab):
    if tab=='anual':
        from .annual import render
        return render(p,destination,tab)
    if tab=='contrafactuales':
        from . import counterfactuals as cf
        selected=p.filters.get('channel','profiles')
        labels={'profiles':'Decisión factual vs perfiles','gates':'Gates SHADOW → BINDING','exits':'Salidas same-entry / replay'}
        links="<nav class='section-nav' aria-label='Clases contrafácticas'>"+''.join(
            "<a href='/analitica/contrafactuales?channel="+key+"'>"+e(label)+"</a>" for key,label in labels.items())+"</nav>"
        note=notice('Evidencia histórica observable, independiente del estado LIVE. Comparaciones SHADOW, sin fills reales, sin cambiar decisiones ni promover parámetros. Fechas y alcance de muestra explícitos.')
        if selected=='gates':
            page=cf.gates(p)
            return links+note+render_table(p,destination,tab,page,labels['gates'],
                fields('policy|Gate;currency|Moneda;family|Familia;evaluated|Evaluaciones|number;would_block|Would block|number;outcome_closed|Cierres comparables|number'),
                fields('market|Mercado;settlement|Liquidación;would_allow|Would allow|number;outcome_pending|Outcome pendiente|number;losses_avoided|Pérdidas evitables observadas|number;gains_removed|Ganancias eliminadas observadas|number;actual_paper_pnl|Subtotal PAPER de cierres|money;counterfactual_if_bound|Modelo si gate BINDING|money;automatic_promotion|Promoción automática;as_of|Evaluación más reciente|time'),
                note='Muestra bounded, deduplicada por gate/posición. Subtotales por moneda; no modelan realimentación de capital, fills alternativos ni una cartera ejecutable.')
        if selected=='exits':
            page=cf.exits(p)
            return links+note+render_table(p,destination,tab,page,labels['exits'],
                fields('symbol|Ticker;currency|Moneda;policy|Política SHADOW;state|Comparabilidad|status;modeled_net|Neto modelado comparable|money'),
                fields('family|Familia;market|Mercado;settlement|Liquidación;paper_id|Entrada factual;opened_at|Entrada|time;registered_at|Registro|time;as_of|Cut histórico|time;coverage|Cobertura de trayectoria;path_observations|Muestras|number;entry_evidence_sha256|Hash entrada;path_sha256|Hash trayectoria;comparison_basis|Base de comparación;economic_edge_validated|Edge validado'),
                note='Replay de la misma entrada factual; no se ejecuta ningún replay en la petición web. Caminos censurados/no comparables no publican neto como resultado completo.')
        page,counts=cf.profiles(p)
        cards="<div class='metric-grid'>"+''.join(metric(key,counts.get(key),'Muestra publicada, no ejecución ni rentabilidad',kind='number')
            for key in ('WOULD_CONFIRM','WOULD_WARN','WOULD_REJECT','INSUFFICIENT_EVIDENCE'))+"</div>"
        return links+note+cards+render_table(p,destination,tab,page,'Aprendizaje contrafáctico histórico',
            fields('symbol|Ticker;profile|Perfil SHADOW;original_decision|Decisión factual;outcome|Comparación|status;currency|Moneda'),
            fields('family|Familia;candidate_id|Evidence ID;evidence_at|Evidencia|time;compared_at|Comparación|time;reason|Límite / fundamento;entry_authority|Autoridad de entrada'),
            note='WOULD_CONFIRM/WARN/REJECT exige publicación comparable y clocks válidos. Un reporte antiguo sigue visible como histórico; no acredita salud LIVE.')
    if tab == "performance":
        return render_table(p, destination, tab, p.performance(), "Performance por moneda / estrategia / familia", PERFORMANCE, PERFORMANCE_DETAIL,
                            note="Win rate se acompaña de muestra, costos, neto, expectancy y profit factor cuando está definido. Edge y calibración OOS permanecen NO_VERIFICADO sin evidencia.")
    if tab == "senales":
        return render_table(p, destination, tab, shadow_rows(p, "signals"), "Señales & cohorts · score_is_probability=false",
                            fields("family|Familia;strategy|Estrategia;state|OOS|status;sample_size|Muestra|number;score|Score · NO PROBABILIDAD|score"),
                            fields("score_bucket|Bucket de score;hit_rate|Hit rate|percent;forward_returns|Forward returns;mfe|MFE validada;mae|MAE validada;auc|AUC dataset validado;calibration|Calibración OOS;symbol|Ticker;hour|Hora ART;regime|Régimen;registry_version|Registry / versión"),
                            note="Score NO PROBABILIDAD. AUC/calibración requieren dataset correcto; no se promueven evaluadores automáticamente.")
    if tab == "salidas":
        return render_table(p, destination, tab, p.positions(closed=True), "Salidas factuales PAPER", CLOSES,
                            (*CLOSE_DETAIL, *fields("shadow_alternative|Alternativa SHADOW;counterfactual|Same-entry counterfactual;trailing|Experimento trailing;break_even|Experimento break-even")),
                            note="TP / SL / EOD / MaxHold factuales se conservan. Alternativas SHADOW no modifican la política de salida.")
    if tab == "historico":
        local=p.local_history_summary()
        local_panel=notice("Histórico local: " + local["state"] + " · " + local.get("reason", local.get("scope", "")) + "; no habilita READY ni se presenta como OHLCV completo.")
        for row in local.get("rows", []):
            from .projection import json_object
            identity=json_object(row["identity_json"]); body=json_object(row["body_json"])
            local_panel += "<p class='muted'>" + e(str(identity.get("symbol"))+" / "+str(identity.get("currency"))+" / "+str(identity.get("settlement"))+" · "+str(row["session"])+" · "+str(body.get("quality"))+" · minutos "+str(body.get("captured_minutes"))+"/"+str(body.get("expected_minutes"))+" · faltantes "+str(body.get("missing_minutes"))+" · publicación "+clock(row["known_at"])) + "</p>"
        return local_panel + render_table(p, destination, tab, p.history(), "Calidad & coverage histórica",
                            fields("symbol|Ticker;family|Familia;row_count|Filas históricas|number;series_last_session|Última sesión;freshness|Sesiones históricas|status"),
                            fields("date_from|Desde;date_to|Hasta;expected_last_session|Última sesión esperada;last_ingest_at|Última ingesta|time;ingest_freshness|Frescura de ingesta|status;coverage|Coverage;gaps|Gaps;depth|Profundidad;candles|Velas;readiness_authority|Autoridad sobre readiness;source|Fuente"),
                            note="Histórico es calidad de datos; nunca gobierna RUNTIME_READY ni autoriza estrategias.")
    if tab == "experimentos":
        return render_table(p, destination, tab, shadow_rows(p, "experiments"), "Registro de experimentos SHADOW / validación",
                            fields("registry_version|Registry / versión;strategy|Evaluator;state|OOS|status;sample_size|Observaciones|number;entry_authority|Autoridad de entrada"),
                            fields("preregistered|Preregistro;economic_exit_lab|Economic/exit lab;observations|Observaciones;reason|Limitación;promotion|Promoción automática"),
                            note="Experimentos preregistrados. NO automatic promotion. OBSERVE_ONLY/SHADOW conservan entry_authority=false.")
    if tab == "cierre-diario":
        counts = p.counts()
        return committed_funnel(p) + render_table(p, destination, tab, p.performance(today=True), "Journal de la rueda · ART", PERFORMANCE,
                                           (*PERFORMANCE_DETAIL, *fields("exit_reasons|Motivos de salida;why_no_trade|Why no trade;late_missed|Late/missed discovery causal;no_trade_baseline|No-trade baseline;cash_opportunity_cost|Costo de oportunidad caja|money;caucion_opportunity_cost|Costo de oportunidad caución|money;alerts|Alertas / incidentes")),
                                           note="No-trade baseline y late/missed requieren evidencia causal. Se conservan moneda y atribución; no se reconstruyen oportunidades perdidas por intuición.")
    page = p.store.page("report_registry", order="id DESC" if "id" in p.store.columns("report_registry") else "", offset=p.offset)
    for row in page.rows:
        row["download"] = f"/api/reports/{row['id']}/pdf" if row.get("pdf_path") and row.get("id") else None
        row["report_type"] = row.get("report_type") or row.get("period_type")
    result = render_table(p, destination, tab, page, "Reportes versionados",
                          fields("id|Reporte;report_type|Tipo;created_at|Publicado|time;state|Estado|status"),
                          fields("period_start|Desde;period_end|Hasta;download|Descarga autenticada"))
    return _action4_card(p) + result + "<a class='text-link' href='/api/reports/weekly/download'>Descargar reporte semanal disponible</a>"
