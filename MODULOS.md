# MODULOS — Porota Trading RC4

Inventario generado desde el ZIP operativo exacto de candidate1. El prefijo del nombre ya no se considera una garantía de capa; la columna `Estado RC4` es la fuente de navegación.

| Módulo | Estado RC4 | Importadores directos | Propósito (docstring) |
|---|---|---:|---|
| `_version` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 8 | Identidad única del candidato PAPER auditado. |
| `aa_env_guard` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 6 | aa_env_guard.py — NUEVO EN v14.0 (Instrucción 1 del pedido de v14). |
| `ab_log_watch` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | ab_log_watch.py — NUEVO EN v14.0 (Instrucción 9 del pedido de v14). |
| `ac_db` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 29 | ac_db.py — Capa única de conexión a SQLite (NUEVO EN v15.0) |
| `ad_macro_history` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 9 | ad_macro_history.py — Series macroeconómicas históricas argentinas (NUEVO EN v15.0) |
| `ae_ppi_api_watch` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 4 | ae_ppi_api_watch.py — Vigilancia de cambios en la API de PPI (NUEVO EN v15.0) |
| `af_model_registry` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 4 | af_model_registry.py — Resiliencia ante cambios de modelo de IA (NUEVO EN v15.0) |
| `ag_kill_switch_supervisor` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | ag_kill_switch_supervisor.py — Kill switch con criterio propio (NUEVO EN v15.0) |
| `ah_market_tools` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | ah_market_tools.py — Herramientas de mercado para Function Calling (NUEVO EN v15.0) |
| `ai_derivatives_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 4 | ai_derivatives_engine.py — Motor de riesgo para instrumentos derivados |
| `aj_trade_gate` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | aj_trade_gate.py — Catálogo único de los casos en que el sistema NO opera |
| `ak_byma_calendar` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 12 | Calendario operativo conservador de BYMA. |
| `ak_iol_client` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | ak_iol_client.py — Conector con la API de Invertir Online (IOL) |
| `al_historical_ingest` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 11 | al_historical_ingest.py — Ingesta y archivo histórico de precios |
| `al_market_startup` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | Política de arranque e hibernación guiada por el calendario de BYMA. |
| `am_api_health` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | am_api_health.py — Chequeo de salud con semáforos de APIs y módulos |
| `an_sre_deploy` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | an_sre_deploy.py — Propuestas de mejora del motor SRE: evaluación, aplicación |
| `ao_startup_gate` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 10 | ao_startup_gate.py — El bot no arranca solo: pide autorización primero |
| `ap_api_verifier` | `MANUAL_ADMIN_TOOL` | 1 | ap_api_verifier.py — Verificación end-to-end de todas las APIs externas |
| `aq_macro_backtest` | `OFFLINE_TOOL` | 0 | aq_macro_backtest.py — Backtest del filtro macroeconómico |
| `ar_telegram_commands` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | ar_telegram_commands.py — Control remoto por Telegram |
| `as_greeks_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | as_greeks_engine.py — Volatilidad implícita y griegas para opciones de BYMA |
| `at_model_discovery` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | at_model_discovery.py — Descubrimiento dinámico de modelos de IA (v16.2) |
| `au_fee_schedule` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 11 | au_fee_schedule.py — Tarifario por clase de activo (v16.2) |
| `av_rofex_client` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | av_rofex_client.py — Datos de contrato de futuros (v16.2) |
| `ax_equity` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | ax_equity.py — Patrimonio de la cuenta (v16.2) |
| `ay_dashboard_auth` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 5 | ay_dashboard_auth.py — Autenticación del panel (v16.2) |
| `az_maintenance_job` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Ejecutor de una sola tarea de mantenimiento. |
| `az_maintenance_scheduler` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 4 | Scheduler liviano de mantenimiento para el supervisor siempre activo. |
| `b_notifiers` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 6 | b_notifiers.py — Notificador (v7.0) |
| `ba_data912_history` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 7 | Archivo histórico de respaldo basado en la API pública Data912. |
| `bb_runtime_status` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 12 | Estado operativo persistido compartido por el bot y el dashboard. |
| `bc_dashboard_v163` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Extensiones seguras del dashboard v16.3. |
| `bd_ppi_readonly_guard` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 13 | Barrera HTTP fail-closed para observacion de mercado PPI Produccion. |
| `be_paper_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 30 | Motor paper independiente para observar PPI Produccion sin operar cuentas. |
| `bf_production_paper_observer` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 13 | Observador productivo con paper trading completo y cero capacidad operativa. |
| `bg_paper_dashboard` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 21 | Dashboard 24x7 v17 RC3, independiente y sin credenciales PPI. |
| `bh_paper_gemini` | `COMPATIBILITY_INACTIVE` | 1 | Porton Gemini aislado para la simulacion productiva. |
| `bh_universe_dashboard_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Vista unificada del universo operativo HF6. |
| `bi_operational_services` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 7 | Servicios 24x7 del observador: SRE, backups, macro, noticias y reportes. |
| `bj_sandbox_health_probe` | `MANUAL_ADMIN_TOOL` | 0 | Una única autenticación Sandbox, sin cuenta, históricos ni órdenes. |
| `bl_candle_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 19 | Archivo versionado y barras de muestras PAPER. No es un feed de negocios. |
| `bm_exit_supervisor` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 11 | Reloj de salidas PAPER sin red ni IA; intención persistente separada del fill. |
| `bn_telegram_bus` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 5 | Outbox PAPER transaccional. No envía órdenes ni bloquea el reloj financiero. |
| `bo_signal_core` | `OFFLINE_REPLAY_LIBRARY` | 3 | Candidato de señal de contado para evaluación OFFLINE, sin promoción. |
| `bq_exit_policy` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 4 | Ventana conservadora del simulador de contado, no calendario universal. |
| `br_backtest_gate` | `OFFLINE_REPLAY_LIBRARY` | 2 | Controles previos de datos y partición temporal; NO aprueba estrategias. |
| `bs_instrument_contracts` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 39 | Contratos financieros v17: unidades explícitas, sin fallback a acciones. |
| `bt_caucion_paper` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 19 | Cauciones colocadoras y disponibilidad de caja del simulador v17. |
| `bu_instrument_catalog` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 5 | Catálogo financiero derivado de payloads PPI, con procedencia explícita. |
| `bv_paper_runtime` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 9 | Runtime PAPER: reloj, escáner, lector, avisos y archivo en procesos distintos. |
| `bw_daily_risk` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 9 | Corte diario PAPER por moneda, persistente y sin acceso a red. |
| `bx_execution_replay` | `OFFLINE_REPLAY_TOOL` | 5 | Replay offline de órdenes IOC sobre libros fechados, no backtester de señales. |
| `by_strategy_backtest` | `OFFLINE_REPLAY_LIBRARY` | 2 | Evaluación offline del candidato de velas, conectada al ledger de ejecución. |
| `bz_replay_risk` | `OFFLINE_REPLAY_LIBRARY` | 5 | Riesgo diario offline: una moneda, capital inicial, sin flujos externos. |
| `c_ppi_client` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 14 | c_ppi_client.py — Cliente resiliente de Portfolio Personal Inversiones (PPI) |
| `ca_caucion_allocator` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 5 | Selección determinista y colocación PAPER, sin red ni programación automática. |
| `cb_caucion_audit` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 4 | Lectura consistente del historial de asignación PAPER; sin ejecutar ni migrar. |
| `cc_spot_liquidity` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Profundidad consumida por fills PAPER de contado, por fotografía y lado. |
| `cd_spot_ledger` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 10 | Ventas parciales PAPER: lotes económicos sin alterar la entrada original. |
| `ce_caucion_treasury` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Política conservadora delegada por el operador: sólo simulación ARS. |
| `cf_intraday_scalping` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Colector intradiario y scanner de scalping PAPER v17 HF3. |
| `cf_sale_settlement` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | Disponibilidad de ventas PAPER: mismo contrato para cierres y parciales. |
| `cg_paper_workspace` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 11 | Continuidad PAPER v17 separada del libro anterior; nunca importa históricos. |
| `ch_contract_evidence_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | HF6 contract evidence registry. |
| `ch_empirical_learning` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | Métricas empíricas sobre fills PAPER cerrados, sin autoajuste ni pronóstico. |
| `ci_operational_context` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | Contexto observacional de mercado y sectores; nunca decide una operación. |
| `ci_ppi_bond_estimate_patch_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Read-only extension for the documented PPI Bonds/Estimate endpoint. |
| `ck_contract_evidence_runner_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 0 | Scheduled HF6 contract evidence collection. |
| `cl_contract_evidence_dashboard_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 0 | Adds contract-source visibility to the existing HF6 Universo operativo page. |
| `cm_special_family_discovery_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Read-only discovery probes for PPI-declared families missing from HF6 catalog. |
| `cn_ppi_authenticated_family_scraper_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 0 | Authenticated PPI web/API evidence collector for HF6 PRODUCTION_PAPER. |
| `co_contract_ingestion_policy_hf6` | `PENDING_WIRING` | 0 | HF6 Contract Evidence v2 ingestion policy. |
| `co_market_sessions_hf6` | `PENDING_WIRING` | 0 | Sesiones de mercado versionadas por familia/mercado para Contract Evidence v2. |
| `cp_contract_evidence_v2_hf6` | `PENDING_WIRING` | 2 | Versioned, append-only Contract Evidence v2 storage. |
| `cp_history_ingest_policy_hf6` | `PENDING_WIRING` | 4 | HF6 Contract/Data v2: policy for progressive historical ingestion. |
| `cq_contract_readiness_hf6` | `PENDING_WIRING` | 0 | Fail-closed Contract Evidence v2 readiness rules for PRODUCTION_PAPER. |
| `cq_family_contract_rules_hf6` | `PENDING_WIRING` | 1 | HF6-v2: requisitos fail-closed por familia, separando contrato y dinámica. |
| `cq_history_attempt_ledger_hf6` | `PENDING_WIRING` | 1 | HF6 v2: append-only evidence ledger for historical ingestion attempts. |
| `cr_data912_reconcile_hf6` | `PENDING_WIRING` | 2 | HF6-v2: reconciliación batch post-cierre de históricos con Data912. |
| `cr_pending_settlement_diagnostics_hf6` | `PENDING_WIRING` | 0 | Read-only diagnostics for PAPER sale receivables shown by the dashboard. |
| `cs_postclose_history_scheduler_hf6` | `PENDING_WIRING` | 0 | HF6-v2: scheduler lógico del batch histórico post-cierre. |
| `ct_ppi_history_salvage_hf6` | `PENDING_WIRING` | 0 | HF6-v2: salva filas PPI históricas válidas sin aceptar filas defectuosas. |
| `cu_history_store_v2_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 4 | HF6-v2: almacenamiento histórico canónico con identidad financiera completa. |
| `cv_history_store_adapter_hf6` | `PENDING_WIRING` | 2 | Adapter HF6-v2 para mantener el histórico fuera de observer_v17.db. |
| `cw_data912_history_v2_sink` | `PENDING_WIRING` | 1 | Data912 batch sink para History Store v2. |
| `cx_a3_primary_readonly_hf6` | `PENDING_WIRING` | 2 | A3/Primary REST read-only client for HF6 v2. |
| `cy_a3_contract_bridge_hf6` | `PENDING_WIRING` | 0 | Normalize A3/Primary instrument metadata into Contract Evidence v2. |
| `cy_market_source_arbitration_hf6` | `PENDING_WIRING` | 0 | HF6 v2 market-source policy. |
| `cz_a3_cem_public_history_hf6` | `PENDING_WIRING` | 1 | A3 CEM public read-only client for HF6 v2. |
| `d_economics` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 8 | economics.py — Fricción, costos reales y hurdle rate dinámico (v2.2) |
| `da_dashboard_ux_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Canonical UX/navigation model for HF6 v2. |
| `db_a3_cem_normalizer_hf6` | `PENDING_WIRING` | 1 | Normalize official A3 CEM reference/history data for HF6 v2. |
| `db_dashboard_logs_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Read-only log discovery/tailing for the HF6 v2 dashboard. |
| `dd_history_metrics_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | Read-only HF6 v2 history metrics for dashboard/introspection. |
| `de_concurrent_risk_capacity_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | Dynamic concurrent-risk capacity for HF6 PAPER. |
| `de_scheduler_catalog_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Scheduler catalog/model for HF6 v2 dashboard. |
| `df_caucion_end_of_day_sweep_hf6` | `PENDING_WIRING` | 2 | End-of-day caucion cash-sweep planner for HF6 PAPER. |
| `df_daily_operation_summary_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | Read-only daily operation summaries for the HF6-v2 dashboard. |
| `dg_dashboard_daily_result_ux_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Responsive HF6-v2 dashboard helpers for daily results and report registry. |
| `dh_dashboard_compact_lists_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | Compact/paginated dashboard list helpers for HF6-v2. |
| `dh_paper_dynamic_risk_gate_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | HF6-v2 bridge between the PAPER ledger and dynamic concurrent-risk policy. |
| `di_caucion_cash_sweep_runtime_hf6` | `PENDING_WIRING` | 1 | HF6-v2 end-of-day caucion cash-sweep orchestration. |
| `e_technical_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | technical_engine.py — Motor cuantitativo técnico (v2.2) |
| `entrypoint` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 0 | Supervisor del dashboard y del motor de trading dentro del contenedor. |
| `f_gemini_decision_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | f_gemini_decision_engine.py — Motor de co-decisión macro/geopolítico |
| `g_news_feed` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | g_news_feed.py — Extractor de noticias RSS (v8.0) |
| `h_daily_report` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | daily_report.py — Apertura de rueda, resumen de cierre y P&L diario (v4.0) |
| `i_auto_tuner` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | i_auto_tuner.py — Reportes y propuestas mensuales (v17) |
| `j_main` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | j_main.py — Bucle principal y Scheduler (v8.0) |
| `k_position_manager` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 7 | k_position_manager.py — Stop-loss, take-profit y P&L real por operación (v6.0) |
| `l_order_confirmation` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 5 | l_order_confirmation.py — Ejecución de órdenes: automática (SANDBOX) o con |
| `m_instrument_universe` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | m_instrument_universe.py — Universo de instrumentos, descubrimiento |
| `m_introspection_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | m_introspection_engine.py — Motor de Introspección SRE, RAG y |
| `o_dashboard` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | o_dashboard.py — Dashboard web de solo lectura (v8.0) |
| `ops_disk_inventory_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 0 | Inventario de disco HF6: clasifica antes de eliminar y no modifica nada. |
| `ops_introspection_hf4` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | Introspección funcional horaria y bajo demanda de Porota HF5. |
| `ops_publish_introspection_hf6` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | Publica una vista sanitaria mínima de la introspección Porota. |
| `p_risk_guardian` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 9 | p_risk_guardian.py — Kill switch automático (v8.0) |
| `porota_mode_manager` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 8 | Selector exclusivo de modo operativo para Porota Trading v17 candidata. |
| `q_backtest` | `DEPRECATED_RETAINED` | 1 | q_backtest.py — Experimento legado NO VALIDADO (v17) |
| `r_clear_kill_switch` | `MANUAL_ADMIN_TOOL` | 0 | r_clear_kill_switch.py — Liberar el kill switch a mano (v10.5) |
| `r_news_engine_247` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | r_news_engine_247.py — Noticias fuera de horario de mercado (v10.0) |
| `s_learning_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 2 | s_learning_engine.py — Motor de diagnóstico y aprendizaje (v10.0) |
| `t_model_guardian` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | t_model_guardian.py — Guardián de modelos de Gemini (v10.0) |
| `u_aiops_watcher` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | u_aiops_watcher.py — Detección Predictiva de Anomalías con Isolation Forest |
| `v_config_metadata` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | v_config_metadata.py — Descripciones de cada variable del .env (v10.4) |
| `w_agent_graph` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 1 | w_agent_graph.py — Arquitectura Multi-Agente con LangGraph (NUEVO EN v12.0, |
| `x_ppi_websocket` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 5 | x_ppi_websocket.py — Streams de tiempo real de PPI (Market Data + Account Data). |
| `y_infra_monitor` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 4 | y_infra_monitor.py — Monitoreo de infraestructura (NUEVO EN v13.0) |
| `z_reports_engine` | `ACTIVE_OR_SCHEDULED_REACHABLE` | 3 | z_reports_engine.py — Informe semanal de evolución para presentación |

## Colisiones de prefijo de dos letras

- `ak_`: `ak_byma_calendar.py`, `ak_iol_client.py`
- `al_`: `al_historical_ingest.py`, `al_market_startup.py`
- `az_`: `az_maintenance_job.py`, `az_maintenance_scheduler.py`
- `bh_`: `bh_paper_gemini.py`, `bh_universe_dashboard_hf6.py`
- `cf_`: `cf_intraday_scalping.py`, `cf_sale_settlement.py`
- `ch_`: `ch_contract_evidence_hf6.py`, `ch_empirical_learning.py`
- `ci_`: `ci_operational_context.py`, `ci_ppi_bond_estimate_patch_hf6.py`
- `co_`: `co_contract_ingestion_policy_hf6.py`, `co_market_sessions_hf6.py`
- `cp_`: `cp_contract_evidence_v2_hf6.py`, `cp_history_ingest_policy_hf6.py`
- `cq_`: `cq_contract_readiness_hf6.py`, `cq_family_contract_rules_hf6.py`, `cq_history_attempt_ledger_hf6.py`
- `cr_`: `cr_data912_reconcile_hf6.py`, `cr_pending_settlement_diagnostics_hf6.py`
- `cy_`: `cy_a3_contract_bridge_hf6.py`, `cy_market_source_arbitration_hf6.py`
- `db_`: `db_a3_cem_normalizer_hf6.py`, `db_dashboard_logs_hf6.py`
- `de_`: `de_concurrent_risk_capacity_hf6.py`, `de_scheduler_catalog_hf6.py`
- `df_`: `df_caucion_end_of_day_sweep_hf6.py`, `df_daily_operation_summary_hf6.py`
- `dh_`: `dh_dashboard_compact_lists_hf6.py`, `dh_paper_dynamic_risk_gate_hf6.py`

## Regla RC4

- No renombrar masivamente módulos sólo para reparar prefijos.
- Todo módulo nuevo debe tener docstring con responsabilidad, input/output y autoridad.
- Todo módulo no alcanzable por runtime debe quedar clasificado como tooling, test, deprecated, hold o pending wiring.
- La auditoría de alcanzabilidad debe ejecutarse como gate antes del build final.
