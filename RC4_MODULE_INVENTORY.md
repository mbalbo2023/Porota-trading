# RC4 — Inventario de módulos y scripts

**Método:** AST imports + referencias literales en `systemd/`, `scripts/` y `tests/`. No elimina archivos.

## Resumen

- **DEFERRED_INTEGRATION:** 1
- **LIBRARY_REACHABLE:** 7
- **OPS_CLI_DIAGNOSTIC:** 1
- **OPS_CLI_MANUAL:** 1
- **OPS_CLI_MANUAL_LEGACY:** 1
- **OPS_CLI_OFFLINE:** 1
- **OPS_CLI_OR_BUILD:** 11
- **RC4_TOOL_OR_TEST:** 3
- **RUNTIME_ACTIVE:** 101
- **SCHEDULER_ACTIVE:** 5
- **TEST_ONLY:** 23

- **Colisiones de prefijo detectadas:** 20

## Decisión sobre aparentes huérfanos

La ausencia de import directo no implica que un módulo sea basura: puede ser CLI, scheduler, build tool, fixture o módulo cargado dinámicamente. RC4 clasifica explícitamente los CLI humanos/offline y el único bridge diferido; `REVIEW_REQUIRED` debe llegar a cero antes de TEST-READY.

## Tabla completa

| Módulo | Clasificación | Importado por | Referencias externas | Decisión / razón |
|---|---|---|---|---|
| `_version.py` | RUNTIME_ACTIVE | bc_dashboard_v163, bf_production_paper_observer, bg_paper_dashboard, cg_paper_workspace, o_dashboard, ops_introspection_hf4, rc4_release_preflight | scripts/build_v17_ppi_probe.py, scripts/porota_apply_daily_results_responsive_hf6.sh, scripts/porota_apply_dashboard_ux_logs_hf6.sh, scripts/porota_apply_history_dashboard_metrics_hf6.sh, scripts/v17_host_general_backup.py, scripts/v17_ppi_public_probe.py, scripts/v17_rc3_hf2_deploy.py, scripts/v17_rc3_hf6_deploy.py, tests/test_dashboard_v17_rc3.py, tests/test_hotfix_rc3_hf5.py, tests/test_integracion_v162.py, tests/test_ppi_public_probe_v17.py | — |
| `aa_env_guard.py` | RUNTIME_ACTIVE | bc_dashboard_v163, entrypoint, j_main, k_position_manager, l_order_confirmation, o_dashboard | — | — |
| `ab_log_watch.py` | RUNTIME_ACTIVE | j_main | — | — |
| `ac_db.py` | RUNTIME_ACTIVE | aa_env_guard, ab_log_watch, ad_macro_history, ae_ppi_api_watch, af_model_registry, ag_kill_switch_supervisor, am_api_health, aq_macro_backtest, ar_telegram_commands, ax_equity, b_notifiers, bb_runtime_status, c_ppi_client, h_daily_report, i_auto_tuner, j_main, k_position_manager, l_order_confirmation, m_instrument_universe, o_dashboard, p_risk_guardian, r_news_engine_247, s_learning_engine, t_model_guardian, y_infra_monitor, z_reports_engine | tests/test_api_verifier_publicas.py, tests/test_derivatives_and_gate.py, tests/test_operational_safety_v163.py, tests/test_ppi_documented_contract_v17.py, tests/test_ppi_retry_policy_v1633.py | — |
| `ad_macro_history.py` | RUNTIME_ACTIVE | ah_market_tools, am_api_health, ap_api_verifier, as_greeks_engine, az_maintenance_job, f_gemini_decision_engine, j_main | tests/test_api_verifier_publicas.py, tests/test_macro_bcra_v4.py | — |
| `ae_ppi_api_watch.py` | RUNTIME_ACTIVE | am_api_health, c_ppi_client, j_main | — | — |
| `af_model_registry.py` | RUNTIME_ACTIVE | ag_kill_switch_supervisor, f_gemini_decision_engine, j_main | — | — |
| `ag_kill_switch_supervisor.py` | RUNTIME_ACTIVE | j_main, l_order_confirmation | — | — |
| `ah_market_tools.py` | RUNTIME_ACTIVE | f_gemini_decision_engine, j_main | tests/test_operational_safety_v163.py | — |
| `ai_derivatives_engine.py` | RUNTIME_ACTIVE | j_main, m_instrument_universe | tests/test_derivatives_and_gate.py, tests/test_integracion_v162.py, tests/test_v161.py | — |
| `aj_trade_gate.py` | RUNTIME_ACTIVE | j_main | tests/test_derivatives_and_gate.py, tests/test_integracion_v162.py | — |
| `ak_byma_calendar.py` | RUNTIME_ACTIVE | al_historical_ingest, al_market_startup, bf_production_paper_observer, bq_exit_policy, ca_caucion_allocator, ce_caucion_treasury, cf_intraday_scalping, cf_sale_settlement, cs_postclose_history_scheduler_hf6, j_main | scripts/build_v17_ledger_preflight.py, tests/test_byma_calendar.py, tests/test_partial_receipts_v17.py | — |
| `ak_iol_client.py` | RUNTIME_ACTIVE | ah_market_tools, ap_api_verifier | — | — |
| `al_historical_ingest.py` | RUNTIME_ACTIVE | ah_market_tools, ai_derivatives_engine, am_api_health, az_maintenance_job, ba_data912_history, bc_dashboard_v163, cv_history_store_adapter_hf6, cw_data912_history_v2_sink, o_dashboard | tests/test_data912_history_fallback.py, tests/test_historical_freshness_v1633.py | — |
| `al_market_startup.py` | RUNTIME_ACTIVE | entrypoint, j_main | tests/test_market_auto_start.py | — |
| `am_api_health.py` | RUNTIME_ACTIVE | o_dashboard | tests/test_dashboard_truth_v1633.py, tests/test_operational_safety_v163.py | — |
| `an_sre_deploy.py` | RUNTIME_ACTIVE | o_dashboard | — | — |
| `ao_startup_gate.py` | RUNTIME_ACTIVE | ar_telegram_commands, c_ppi_client, entrypoint, j_main, l_order_confirmation, o_dashboard | tests/test_derivatives_and_gate.py, tests/test_integracion_v162.py, tests/test_order_trace_units_v17.py, tests/test_ppi_documented_contract_v17.py, tests/test_ppi_observed_configuration_v17.py, tests/test_telegram_startup_routing.py | — |
| `ap_api_verifier.py` | TEST_ONLY | — | tests/test_api_verifier_publicas.py | — |
| `aq_macro_backtest.py` | OPS_CLI_OFFLINE | — | — | Backtest macro offline; se invoca a demanda. |
| `ar_telegram_commands.py` | RUNTIME_ACTIVE | l_order_confirmation | tests/test_integracion_v162.py, tests/test_v161.py | — |
| `as_greeks_engine.py` | RUNTIME_ACTIVE | ai_derivatives_engine | tests/test_macro_bcra_v4.py, tests/test_v161.py | — |
| `at_model_discovery.py` | RUNTIME_ACTIVE | af_model_registry | — | — |
| `au_fee_schedule.py` | RUNTIME_ACTIVE | be_paper_engine, bt_caucion_paper, cf_intraday_scalping, d_economics, j_main | tests/test_caucion_ledger_v17.py, tests/test_economics_costs.py, tests/test_hotfix_rc3_hf1.py, tests/test_integracion_v162.py, tests/test_production_paper_v1634.py, tests/test_propiedades_costos.py | — |
| `av_rofex_client.py` | RUNTIME_ACTIVE | m_instrument_universe | tests/test_integracion_v162.py, tests/test_rofex_margin_v1633.py | — |
| `ax_equity.py` | RUNTIME_ACTIVE | j_main, p_risk_guardian | tests/test_integracion_v162.py | — |
| `ay_dashboard_auth.py` | RUNTIME_ACTIVE | entrypoint, o_dashboard | tests/test_dashboard_paper_v1634.py, tests/test_dashboard_session.py, tests/test_integracion_v162.py | — |
| `az_maintenance_job.py` | TEST_ONLY | — | tests/test_deploy_nonroot.py, tests/test_historical_freshness_v1633.py, tests/test_maintenance_scheduler.py | — |
| `az_maintenance_scheduler.py` | RUNTIME_ACTIVE | entrypoint | tests/test_data912_history_fallback.py, tests/test_historical_freshness_v1633.py, tests/test_maintenance_scheduler.py | — |
| `b_notifiers.py` | RUNTIME_ACTIVE | ap_api_verifier, az_maintenance_job, entrypoint, j_main, o_dashboard | tests/test_integracion_v162.py, tests/test_notifier_contract.py | — |
| `ba_data912_history.py` | RUNTIME_ACTIVE | az_maintenance_job, cr_data912_reconcile_hf6, cw_data912_history_v2_sink, m_instrument_universe, test_hf6_history_data912_v2 | tests/test_data912_history_fallback.py, tests/test_historical_freshness_v1633.py | — |
| `bb_runtime_status.py` | RUNTIME_ACTIVE | am_api_health, an_sre_deploy, b_notifiers, bc_dashboard_v163, c_ppi_client, entrypoint, j_main, l_order_confirmation, m_introspection_engine, o_dashboard, y_infra_monitor | tests/test_dashboard_truth_v1633.py | — |
| `bc_dashboard_v163.py` | RUNTIME_ACTIVE | o_dashboard | tests/test_dashboard_truth_v1633.py | — |
| `bd_ppi_readonly_guard.py` | RUNTIME_ACTIVE | bf_production_paper_observer, bv_paper_runtime, cf_intraday_scalping, ci_ppi_bond_estimate_patch_hf6, ck_contract_evidence_runner_hf6 | scripts/build_v17_ppi_probe.py, scripts/v17_ppi_public_probe.py, tests/test_contract_evidence_hf6.py, tests/test_exit_supervision_v17.py, tests/test_hotfix_rc3_hf4.py, tests/test_hotfix_rc3_hf5.py, tests/test_intraday_scalping_hf3.py, tests/test_ppi_documented_contract_v17.py, tests/test_ppi_public_probe_v17.py, tests/test_production_paper_v1634.py | — |
| `be_paper_engine.py` | RUNTIME_ACTIVE | bf_production_paper_observer, bj_sandbox_health_probe, bv_paper_runtime, cf_intraday_scalping, cg_paper_workspace | scripts/porota_apply_dynamic_risk_gate_hf6.sh, tests/test_backtest_data_v17.py, tests/test_candle_archive_v17.py, tests/test_caucion_ledger_v17.py, tests/test_caucion_treasury_v17.py, tests/test_daily_risk_outbox_v17.py, tests/test_dashboard_paper_v1634.py, tests/test_execution_replay_v17.py, tests/test_exit_ledger_isolation_v17.py, tests/test_exit_supervision_v17.py, tests/test_hotfix_rc3_hf1.py, tests/test_hotfix_rc3_hf2.py, tests/test_hotfix_rc3_hf4.py, tests/test_hotfix_rc3_hf5.py, tests/test_hotfix_rc3_hf6.py, tests/test_intraday_scalping_hf3.py, tests/test_ledger_preflight_v17.py, tests/test_legacy_observed_shape_v17.py, tests/test_open_entry_terms_v17.py, tests/test_operational_services_v1635.py, tests/test_paper_workspace_v17.py, tests/test_partial_allocation_integrity_v17.py, tests/test_partial_receipts_v17.py, tests/test_ppi_observed_configuration_v17.py, tests/test_production_paper_v1634.py, tests/test_rc4_acceptance.py, tests/test_single_close_ledger_v17.py, tests/test_strategy_backtest_v17.py | — |
| `bf_production_paper_observer.py` | RUNTIME_ACTIVE | bj_sandbox_health_probe, bv_paper_runtime, cf_intraday_scalping | tests/test_candle_archive_v17.py, tests/test_dashboard_paper_v1634.py, tests/test_exit_ledger_isolation_v17.py, tests/test_exit_supervision_v17.py, tests/test_hotfix_rc3_hf1.py, tests/test_hotfix_rc3_hf2.py, tests/test_hotfix_rc3_hf4.py, tests/test_hotfix_rc3_hf6.py, tests/test_intraday_scalping_hf3.py, tests/test_paper_workspace_v17.py, tests/test_ppi_observed_configuration_v17.py, tests/test_production_paper_v1634.py | — |
| `bg_paper_dashboard.py` | RUNTIME_ACTIVE | bh_universe_dashboard_hf6, cl_contract_evidence_dashboard_hf6, o_dashboard | scripts/porota_apply_daily_results_responsive_hf6.sh, scripts/porota_apply_dashboard_ux_logs_hf6.sh, scripts/porota_apply_history_dashboard_metrics_hf6.sh, scripts/porota_apply_scheduler_dashboard_hf6.sh, tests/test_candle_archive_v17.py, tests/test_caucion_ledger_v17.py, tests/test_daily_risk_outbox_v17.py, tests/test_dashboard_paper_v1634.py, tests/test_dashboard_v17_rc3.py, tests/test_exit_supervision_v17.py, tests/test_hotfix_rc3_hf1.py, tests/test_hotfix_rc3_hf2.py, tests/test_hotfix_rc3_hf5.py, tests/test_hotfix_rc3_hf6.py, tests/test_intraday_scalping_hf3.py, tests/test_open_entry_terms_v17.py, tests/test_paper_workspace_v17.py, tests/test_partial_allocation_integrity_v17.py, tests/test_partial_receipts_v17.py, tests/test_ppi_observed_configuration_v17.py, tests/test_production_paper_v1634.py, tests/test_rc4_acceptance.py, tests/test_single_close_ledger_v17.py | — |
| `bh_paper_gemini.py` | TEST_ONLY | — | tests/test_hotfix_rc3_hf1.py, tests/test_production_paper_v1634.py | — |
| `bh_universe_dashboard_hf6.py` | RUNTIME_ACTIVE | cl_contract_evidence_dashboard_hf6, o_dashboard | scripts/porota_contract_evidence_dashboard_overlay_hf6.sh | — |
| `bi_operational_services.py` | RUNTIME_ACTIVE | bf_production_paper_observer | tests/test_caucion_ledger_v17.py, tests/test_daily_risk_outbox_v17.py, tests/test_dashboard_paper_v1634.py, tests/test_operational_services_v1635.py, tests/test_paper_workspace_v17.py, tests/test_production_paper_v1634.py, tests/test_single_close_ledger_v17.py | — |
| `bj_sandbox_health_probe.py` | OPS_CLI_DIAGNOSTIC | — | — | Probe Sandbox manual; no debe correr automáticamente en producción. |
| `bl_candle_engine.py` | RUNTIME_ACTIVE | be_paper_engine, bf_production_paper_observer, bg_paper_dashboard, bo_signal_core, br_backtest_gate, bt_caucion_paper, bv_paper_runtime, bx_execution_replay, by_strategy_backtest, bz_replay_risk, ca_caucion_allocator, cb_caucion_audit, cc_spot_liquidity, ce_caucion_treasury | tests/test_backtest_data_v17.py, tests/test_candle_archive_v17.py, tests/test_dashboard_paper_v1634.py, tests/test_execution_replay_v17.py, tests/test_strategy_backtest_v17.py | — |
| `bm_exit_supervisor.py` | RUNTIME_ACTIVE | be_paper_engine, bv_paper_runtime | tests/test_caucion_ledger_v17.py, tests/test_daily_risk_outbox_v17.py, tests/test_exit_ledger_isolation_v17.py, tests/test_exit_supervision_v17.py, tests/test_open_entry_terms_v17.py, tests/test_partial_allocation_integrity_v17.py, tests/test_partial_receipts_v17.py, tests/test_production_paper_v1634.py, tests/test_single_close_ledger_v17.py | — |
| `bn_telegram_bus.py` | RUNTIME_ACTIVE | be_paper_engine, bf_production_paper_observer, bi_operational_services, bv_paper_runtime | tests/test_daily_risk_outbox_v17.py | — |
| `bo_signal_core.py` | TEST_ONLY | bx_execution_replay, by_strategy_backtest | tests/test_strategy_backtest_v17.py | — |
| `bq_exit_policy.py` | RUNTIME_ACTIVE | bv_paper_runtime, rc4_validation | tests/test_exit_ledger_isolation_v17.py, tests/test_exit_supervision_v17.py, tests/test_hotfix_rc3_hf4.py | — |
| `br_backtest_gate.py` | TEST_ONLY | bx_execution_replay | tests/test_backtest_data_v17.py | — |
| `bs_instrument_contracts.py` | RUNTIME_ACTIVE | ah_market_tools, be_paper_engine, bf_production_paper_observer, bg_paper_dashboard, bi_operational_services, bl_candle_engine, bm_exit_supervisor, bn_telegram_bus, bo_signal_core, bq_exit_policy, br_backtest_gate, bt_caucion_paper, bu_instrument_catalog, bw_daily_risk, bx_execution_replay, by_strategy_backtest, bz_replay_risk, c_ppi_client, ca_caucion_allocator, cb_caucion_audit, cc_spot_liquidity, cd_spot_ledger, ce_caucion_treasury, cf_intraday_scalping, cf_sale_settlement, cr_pending_settlement_diagnostics_hf6, df_caucion_end_of_day_sweep_hf6, df_daily_operation_summary_hf6, dh_paper_dynamic_risk_gate_hf6, di_caucion_cash_sweep_runtime_hf6, x_ppi_websocket | scripts/build_v17_ledger_preflight.py, scripts/v17_ledger_preflight.py, tests/test_caucion_treasury_v17.py, tests/test_derivatives_and_gate.py, tests/test_execution_replay_v17.py, tests/test_partial_receipts_v17.py, tests/test_production_paper_v1634.py, tests/test_single_close_ledger_v17.py, tests/test_strategy_backtest_v17.py | — |
| `bt_caucion_paper.py` | RUNTIME_ACTIVE | be_paper_engine, bg_paper_dashboard, bi_operational_services, bw_daily_risk, ca_caucion_allocator, cb_caucion_audit, ce_caucion_treasury, df_caucion_end_of_day_sweep_hf6, di_caucion_cash_sweep_runtime_hf6 | tests/test_cash_sweep_runtime_hf6.py, tests/test_caucion_ledger_v17.py, tests/test_daily_risk_outbox_v17.py, tests/test_dashboard_paper_v1634.py, tests/test_dynamic_risk_and_caucion_sweep_hf6.py, tests/test_operational_services_v1635.py, tests/test_partial_allocation_integrity_v17.py, tests/test_partial_receipts_v17.py, tests/test_production_paper_v1634.py, tests/test_single_close_ledger_v17.py | — |
| `bu_instrument_catalog.py` | RUNTIME_ACTIVE | bf_production_paper_observer, bv_paper_runtime | tests/test_hotfix_rc3_hf4.py, tests/test_intraday_scalping_hf3.py, tests/test_ppi_observed_configuration_v17.py, tests/test_production_paper_v1634.py | — |
| `bv_paper_runtime.py` | RUNTIME_ACTIVE | bf_production_paper_observer, cf_intraday_scalping | tests/test_daily_risk_outbox_v17.py, tests/test_exit_ledger_isolation_v17.py, tests/test_exit_supervision_v17.py, tests/test_hotfix_rc3_hf1.py, tests/test_hotfix_rc3_hf4.py, tests/test_hotfix_rc3_hf6.py, tests/test_intraday_scalping_hf3.py, tests/test_paper_workspace_v17.py | — |
| `bw_daily_risk.py` | RUNTIME_ACTIVE | be_paper_engine, bz_replay_risk, ca_caucion_allocator | tests/test_daily_risk_outbox_v17.py, tests/test_exit_ledger_isolation_v17.py, tests/test_hotfix_rc3_hf6.py, tests/test_open_entry_terms_v17.py, tests/test_partial_allocation_integrity_v17.py, tests/test_single_close_ledger_v17.py | — |
| `bx_execution_replay.py` | TEST_ONLY | bo_signal_core, bx_execution_replay, by_strategy_backtest | tests/test_execution_replay_v17.py, tests/test_strategy_backtest_v17.py | — |
| `by_strategy_backtest.py` | TEST_ONLY | bx_execution_replay | tests/test_strategy_backtest_v17.py | — |
| `bz_replay_risk.py` | TEST_ONLY | bx_execution_replay, by_strategy_backtest | tests/test_daily_risk_outbox_v17.py, tests/test_execution_replay_v17.py, tests/test_strategy_backtest_v17.py | — |
| `c_ppi_client.py` | RUNTIME_ACTIVE | ap_api_verifier, b_notifiers, bb_runtime_status, bc_dashboard_v163, bj_sandbox_health_probe, j_main, m_introspection_engine, x_ppi_websocket | tests/test_operational_safety_v163.py, tests/test_order_trace_units_v17.py, tests/test_ppi_documented_contract_v17.py, tests/test_ppi_observed_configuration_v17.py, tests/test_ppi_retry_policy_v1633.py, tests/test_ppi_sandbox_adapter_v1632.py | — |
| `ca_caucion_allocator.py` | RUNTIME_ACTIVE | be_paper_engine, cb_caucion_audit, ce_caucion_treasury, di_caucion_cash_sweep_runtime_hf6 | tests/test_production_paper_v1634.py | — |
| `cb_caucion_audit.py` | RUNTIME_ACTIVE | bg_paper_dashboard | tests/test_caucion_treasury_v17.py, tests/test_dashboard_paper_v1634.py, tests/test_paper_workspace_v17.py | — |
| `cc_spot_liquidity.py` | RUNTIME_ACTIVE | be_paper_engine | tests/test_production_paper_v1634.py | — |
| `cd_spot_ledger.py` | RUNTIME_ACTIVE | be_paper_engine, bg_paper_dashboard, bi_operational_services, bm_exit_supervisor, bt_caucion_paper, dh_paper_dynamic_risk_gate_hf6 | scripts/build_v17_ledger_preflight.py, scripts/v17_ledger_preflight.py, tests/test_exit_ledger_isolation_v17.py, tests/test_partial_allocation_integrity_v17.py, tests/test_production_paper_v1634.py | — |
| `ce_caucion_treasury.py` | RUNTIME_ACTIVE | be_paper_engine | tests/test_caucion_treasury_v17.py | — |
| `cf_intraday_scalping.py` | RUNTIME_ACTIVE | bv_paper_runtime | tests/test_intraday_scalping_hf3.py | — |
| `cf_sale_settlement.py` | RUNTIME_ACTIVE | bt_caucion_paper, cd_spot_ledger, cr_pending_settlement_diagnostics_hf6 | scripts/build_v17_ledger_preflight.py, scripts/v17_ledger_preflight.py, tests/test_partial_receipts_v17.py, tests/test_rc4_acceptance.py | — |
| `cg_paper_workspace.py` | RUNTIME_ACTIVE | be_paper_engine, bf_production_paper_observer, bg_paper_dashboard, bi_operational_services, bj_sandbox_health_probe, bv_paper_runtime, cb_caucion_audit, porota_mode_manager | tests/test_caucion_ledger_v17.py, tests/test_dashboard_v17_rc3.py, tests/test_paper_workspace_v17.py | — |
| `ch_contract_evidence_hf6.py` | OPS_CLI_OR_BUILD | ck_contract_evidence_runner_hf6, cm_special_family_discovery_hf6 | scripts/porota_contract_evidence_hf6_runtime.sh, tests/test_contract_evidence_hf6.py | — |
| `ch_empirical_learning.py` | RUNTIME_ACTIVE | bg_paper_dashboard, ops_introspection_hf4, rc4_policy_context | tests/test_hotfix_rc3_hf6.py | — |
| `ci_operational_context.py` | RUNTIME_ACTIVE | bg_paper_dashboard, ops_introspection_hf4, rc4_policy_context | tests/test_hotfix_rc3_hf6.py | — |
| `ci_ppi_bond_estimate_patch_hf6.py` | OPS_CLI_OR_BUILD | ck_contract_evidence_runner_hf6 | scripts/porota_contract_evidence_hf6_runtime.sh, tests/test_contract_evidence_hf6.py | — |
| `ck_contract_evidence_runner_hf6.py` | OPS_CLI_OR_BUILD | — | scripts/porota_contract_evidence_hf6_runtime.sh | — |
| `ck_policy_gate_hf6.py` | RUNTIME_ACTIVE | be_paper_engine, bg_paper_dashboard, rc4_validation | tests/test_rc4_acceptance.py | — |
| `cl_contract_evidence_dashboard_hf6.py` | OPS_CLI_OR_BUILD | — | scripts/porota_contract_evidence_dashboard_overlay_hf6.sh, scripts/porota_contract_patch_finalize_hf6.sh | — |
| `cm_special_family_discovery_hf6.py` | OPS_CLI_OR_BUILD | ck_contract_evidence_runner_hf6 | scripts/porota_contract_evidence_hf6_runtime.sh, tests/test_contract_evidence_hf6.py | — |
| `cn_ppi_authenticated_family_scraper_hf6.py` | OPS_CLI_MANUAL_LEGACY | — | scripts/porota_contract_patch_finalize_hf6.sh, scripts/porota_ppi_authenticated_all_families_hf6.sh | Scraper HF6 legacy con login explícito. Se conserva sólo para trazabilidad/manual; los timers RC4 usan exclusivamente trusted-device GET-only. |
| `co_contract_ingestion_policy_hf6.py` | RUNTIME_ACTIVE | de_scheduler_catalog_hf6, rc4_contract_schedule | tests/test_rc4_acceptance.py | — |
| `co_market_sessions_hf6.py` | LIBRARY_REACHABLE | rc4_validation | — | — |
| `cp_contract_evidence_v2_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard, cq_family_contract_rules_hf6, cy_a3_contract_bridge_hf6, rc4_contract_import_job, rc4_contract_legacy_bridge | tests/test_rc4_acceptance.py | — |
| `cp_history_ingest_policy_hf6.py` | RUNTIME_ACTIVE | cr_data912_reconcile_hf6, ct_ppi_history_salvage_hf6, test_cp_history_ingest_policy_hf6, test_hf6_history_data912_v2 | — | — |
| `cq_contract_readiness_hf6.py` | RUNTIME_ACTIVE | cp_contract_evidence_v2_hf6 | — | — |
| `cq_family_contract_rules_hf6.py` | LIBRARY_REACHABLE | test_hf6_history_data912_v2 | — | — |
| `cq_history_attempt_ledger_hf6.py` | RUNTIME_ACTIVE | ct_ppi_history_salvage_hf6 | — | — |
| `cr_data912_reconcile_hf6.py` | LIBRARY_REACHABLE | cs_postclose_history_scheduler_hf6, test_hf6_history_data912_v2 | — | — |
| `cr_pending_settlement_diagnostics_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard | tests/test_rc4_acceptance.py | — |
| `cs_postclose_history_scheduler_hf6.py` | LIBRARY_REACHABLE | rc4_postclose_history_job | — | — |
| `ct_ppi_history_salvage_hf6.py` | RUNTIME_ACTIVE | bf_production_paper_observer | — | — |
| `cu_history_store_v2_hf6.py` | RUNTIME_ACTIVE | ct_ppi_history_salvage_hf6, cw_data912_history_v2_sink, db_a3_cem_normalizer_hf6, rc4_a3_cem_history_runner, test_hf6_history_data912_v2 | scripts/porota_apply_history_source_rank_hf6.sh, tests/test_rc4_acceptance.py | — |
| `cv_history_store_adapter_hf6.py` | RUNTIME_ACTIVE | ct_ppi_history_salvage_hf6, cw_data912_history_v2_sink, rc4_a3_cem_history_runner | — | — |
| `cw_data912_history_v2_sink.py` | LIBRARY_REACHABLE | cr_data912_reconcile_hf6 | — | — |
| `cx_a3_primary_readonly_hf6.py` | TEST_ONLY | test_a3_primary_readonly_hf6 | tests/test_a3_primary_readonly_hf6.py | — |
| `cy_a3_contract_bridge_hf6.py` | DEFERRED_INTEGRATION | — | — | A3 Primary privado no autenticado; bridge conservado sin inventar wiring. |
| `cy_market_source_arbitration_hf6.py` | LIBRARY_REACHABLE | rc4_a3_cem_history_runner | — | — |
| `cz_a3_cem_public_history_hf6.py` | TEST_ONLY | rc4_a3_cem_history_runner | tests/test_a3_cem_public_history_hf6.py | — |
| `d_economics.py` | RUNTIME_ACTIVE | aq_macro_backtest, ar_telegram_commands, e_technical_engine, h_daily_report, j_main, q_backtest | tests/test_economics_costs.py, tests/test_integracion_v162.py, tests/test_propiedades_costos.py | — |
| `da_dashboard_ux_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard | scripts/porota_apply_dashboard_ux_logs_hf6.sh, tests/test_dashboard_ux_logs_cem_hf6.py, tests/test_rc4_acceptance.py | — |
| `db_a3_cem_normalizer_hf6.py` | TEST_ONLY | rc4_a3_cem_history_runner | tests/test_dashboard_ux_logs_cem_hf6.py | — |
| `db_dashboard_logs_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard | scripts/porota_apply_dashboard_ux_logs_hf6.sh, scripts/porota_apply_history_dashboard_metrics_hf6.sh, scripts/porota_apply_scheduler_dashboard_hf6.sh, tests/test_dashboard_ux_logs_cem_hf6.py | — |
| `dd_history_metrics_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard | scripts/porota_apply_history_dashboard_metrics_hf6.sh, tests/test_rc4_acceptance.py | — |
| `de_concurrent_risk_capacity_hf6.py` | RUNTIME_ACTIVE | be_paper_engine, dh_paper_dynamic_risk_gate_hf6 | scripts/porota_apply_dynamic_risk_gate_hf6.sh, tests/test_dynamic_risk_and_caucion_sweep_hf6.py, tests/test_rc4_acceptance.py | — |
| `de_scheduler_catalog_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard | scripts/porota_apply_scheduler_dashboard_hf6.sh, tests/test_rc4_acceptance.py, tests/test_scheduler_dashboard_hf6.py | — |
| `df_caucion_end_of_day_sweep_hf6.py` | TEST_ONLY | di_caucion_cash_sweep_runtime_hf6 | tests/test_dynamic_risk_and_caucion_sweep_hf6.py | — |
| `df_daily_operation_summary_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard | scripts/porota_apply_daily_results_responsive_hf6.sh | — |
| `dg_dashboard_daily_result_ux_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard | scripts/porota_apply_daily_results_responsive_hf6.sh, tests/test_dashboard_daily_responsive_hf6.py | — |
| `dh_dashboard_compact_lists_hf6.py` | RUNTIME_ACTIVE | bg_paper_dashboard | scripts/porota_apply_daily_results_responsive_hf6.sh, tests/test_dashboard_daily_responsive_hf6.py | — |
| `dh_paper_dynamic_risk_gate_hf6.py` | RUNTIME_ACTIVE | be_paper_engine | scripts/porota_apply_dynamic_risk_gate_hf6.sh | — |
| `di_caucion_cash_sweep_runtime_hf6.py` | TEST_ONLY | — | tests/test_cash_sweep_runtime_hf6.py | — |
| `e_technical_engine.py` | RUNTIME_ACTIVE | j_main, q_backtest | tests/test_operational_safety_v163.py | — |
| `entrypoint.py` | RUNTIME_ACTIVE | — | scripts/v17_ledger_preflight.py, scripts/v17_ppi_public_probe.py, scripts/v17_rc3_hf2_deploy.py, scripts/v17_rc3_hf6_deploy.py, tests/test_container_rootfs.py, tests/test_deploy_nonroot.py, tests/test_hotfix_rc3_hf6.py, tests/test_integracion_v162.py, tests/test_ledger_preflight_v17.py, tests/test_maintenance_scheduler.py, tests/test_market_auto_start.py, tests/test_ppi_public_probe_v17.py | — |
| `f_gemini_decision_engine.py` | RUNTIME_ACTIVE | ap_api_verifier, j_main | — | — |
| `g_news_feed.py` | RUNTIME_ACTIVE | ap_api_verifier, j_main, r_news_engine_247 | tests/test_api_verifier_publicas.py | — |
| `h_daily_report.py` | RUNTIME_ACTIVE | j_main | tests/test_rc4_acceptance.py | — |
| `i_auto_tuner.py` | RUNTIME_ACTIVE | az_maintenance_job, j_main | tests/test_backtest_data_v17.py | — |
| `j_main.py` | RUNTIME_ACTIVE | ar_telegram_commands, az_maintenance_job | tests/test_economics_costs.py, tests/test_integracion_v162.py, tests/test_maintenance_scheduler.py, tests/test_operational_safety_v163.py | — |
| `k_position_manager.py` | RUNTIME_ACTIVE | ah_market_tools, an_sre_deploy, ar_telegram_commands, i_auto_tuner, j_main, x_ppi_websocket, z_reports_engine | — | — |
| `l_order_confirmation.py` | RUNTIME_ACTIVE | an_sre_deploy, ar_telegram_commands, j_main | tests/test_derivatives_and_gate.py, tests/test_integracion_v162.py, tests/test_telegram_startup_routing.py | — |
| `m_instrument_universe.py` | RUNTIME_ACTIVE | j_main | tests/test_data912_history_fallback.py, tests/test_derivatives_and_gate.py, tests/test_integracion_v162.py, tests/test_ppi_documented_contract_v17.py | — |
| `m_introspection_engine.py` | RUNTIME_ACTIVE | j_main | — | — |
| `o_dashboard.py` | RUNTIME_ACTIVE | — | scripts/porota_contract_evidence_dashboard_overlay_hf6.sh, scripts/porota_contract_patch_finalize_hf6.sh, tests/test_dashboard_paper_v1634.py, tests/test_dashboard_session.py, tests/test_dashboard_v17_rc3.py, tests/test_integracion_v162.py | — |
| `ops_disk_inventory_hf6.py` | OPS_CLI_OR_BUILD | — | scripts/v17_rc3_hf6_deploy.py | — |
| `ops_introspection_hf4.py` | SCHEDULER_ACTIVE | — | scripts/porota-introspeccion-hf5-manual.sh, scripts/v17_rc3_hf6_deploy.py, systemd/porota-functional-deep-audit-rc4.service, tests/test_hotfix_rc3_hf6.py | — |
| `ops_publish_introspection_hf6.py` | OPS_CLI_OR_BUILD | — | scripts/porota_publish_introspection_github_hf6.sh, tests/test_hotfix_rc3_hf6.py | — |
| `p_risk_guardian.py` | RUNTIME_ACTIVE | ag_kill_switch_supervisor, an_sre_deploy, ar_telegram_commands, j_main, l_order_confirmation, m_introspection_engine, o_dashboard, r_clear_kill_switch, x_ppi_websocket | tests/test_integracion_v162.py | — |
| `porota_mode_manager.py` | OPS_CLI_OR_BUILD | — | scripts/v17_rc3_hf2_deploy.py, scripts/v17_rc3_hf6_deploy.py, tests/test_daily_risk_outbox_v17.py, tests/test_dashboard_v17_rc3.py, tests/test_exit_supervision_v17.py, tests/test_hotfix_rc3_hf1.py, tests/test_hotfix_rc3_hf4.py, tests/test_hotfix_rc3_hf5.py, tests/test_hotfix_rc3_hf6.py, tests/test_intraday_scalping_hf3.py, tests/test_paper_workspace_v17.py | — |
| `q_backtest.py` | TEST_ONLY | — | tests/test_backtest_data_v17.py | — |
| `r_clear_kill_switch.py` | OPS_CLI_MANUAL | — | — | Emergency CLI humano; no debe ser llamado automáticamente. |
| `r_news_engine_247.py` | RUNTIME_ACTIVE | az_maintenance_job, g_news_feed, j_main | — | — |
| `rc4_a3_cem_history_job.py` | SCHEDULER_ACTIVE | — | systemd/porota-a3-cem-history-rc4.service | — |
| `rc4_a3_cem_history_runner.py` | TEST_ONLY | rc4_a3_cem_history_job | tests/test_rc4_acceptance.py | — |
| `rc4_backup_coverage.py` | RUNTIME_ACTIVE | bg_paper_dashboard | tests/test_rc4_acceptance.py | — |
| `rc4_contract_due_job.py` | OPS_CLI_OR_BUILD | — | scripts/porota_contract_evidence_trusted_rc4.sh | — |
| `rc4_contract_import_job.py` | OPS_CLI_OR_BUILD | — | scripts/porota_contract_evidence_trusted_rc4.sh, tests/test_rc4_acceptance.py | — |
| `rc4_contract_legacy_bridge.py` | TEST_ONLY | — | tests/test_rc4_acceptance.py | — |
| `rc4_contract_schedule.py` | TEST_ONLY | rc4_contract_due_job | tests/test_rc4_acceptance.py | — |
| `rc4_functional_health.py` | SCHEDULER_ACTIVE | — | systemd/porota-functional-health-rc4.service, tests/test_rc4_acceptance.py | — |
| `rc4_health_explain.py` | RUNTIME_ACTIVE | bg_paper_dashboard | — | — |
| `rc4_host_general_backup_job.py` | SCHEDULER_ACTIVE | — | systemd/porota-host-general-backup-rc4.service, tests/test_rc4_acceptance.py | — |
| `rc4_module_inventory.py` | TEST_ONLY | — | tests/test_rc4_acceptance.py | — |
| `rc4_policy_context.py` | RUNTIME_ACTIVE | be_paper_engine | tests/test_rc4_acceptance.py | — |
| `rc4_postclose_history_job.py` | SCHEDULER_ACTIVE | — | systemd/porota-history-postclose-rc4.service | — |
| `rc4_ppi_contract_normalizer.py` | TEST_ONLY | rc4_trusted_browser_contract_collector | tests/test_rc4_acceptance.py | — |
| `rc4_release_preflight.py` | TEST_ONLY | — | tests/test_rc4_acceptance.py | — |
| `rc4_replay.py` | LIBRARY_REACHABLE | rc4_replay_analysis | — | — |
| `rc4_replay_analysis.py` | TEST_ONLY | — | tests/test_rc4_acceptance.py | — |
| `rc4_storage_lifecycle_audit.py` | TEST_ONLY | — | tests/test_rc4_acceptance.py | — |
| `rc4_trusted_browser_contract_collector.py` | OPS_CLI_OR_BUILD | — | scripts/porota_contract_evidence_trusted_rc4.sh, tests/test_rc4_acceptance.py | — |
| `rc4_validation.py` | TEST_ONLY | — | tests/test_rc4_acceptance.py | — |
| `s_learning_engine.py` | RUNTIME_ACTIVE | az_maintenance_job, j_main | — | — |
| `t_model_guardian.py` | RUNTIME_ACTIVE | az_maintenance_job, j_main | tests/test_v161.py | — |
| `test_a3_primary_readonly_hf6.py` | RC4_TOOL_OR_TEST | — | — | — |
| `test_cp_history_ingest_policy_hf6.py` | RC4_TOOL_OR_TEST | — | — | — |
| `test_hf6_history_data912_v2.py` | RC4_TOOL_OR_TEST | — | — | — |
| `u_aiops_watcher.py` | RUNTIME_ACTIVE | j_main | — | — |
| `v_config_metadata.py` | RUNTIME_ACTIVE | bb_runtime_status, bc_dashboard_v163, o_dashboard | — | — |
| `w_agent_graph.py` | RUNTIME_ACTIVE | j_main | — | — |
| `x_ppi_websocket.py` | RUNTIME_ACTIVE | ab_log_watch, ah_market_tools, c_ppi_client, j_main | tests/test_operational_safety_v163.py | — |
| `y_infra_monitor.py` | RUNTIME_ACTIVE | ab_log_watch, az_maintenance_job, j_main, o_dashboard | — | — |
| `z_reports_engine.py` | RUNTIME_ACTIVE | az_maintenance_job, j_main, o_dashboard | — | — |

## Colisiones de prefijo

- `ak`: `ak_byma_calendar.py`, `ak_iol_client.py`
- `al`: `al_historical_ingest.py`, `al_market_startup.py`
- `az`: `az_maintenance_job.py`, `az_maintenance_scheduler.py`
- `bh`: `bh_paper_gemini.py`, `bh_universe_dashboard_hf6.py`
- `cf`: `cf_intraday_scalping.py`, `cf_sale_settlement.py`
- `ch`: `ch_contract_evidence_hf6.py`, `ch_empirical_learning.py`
- `ci`: `ci_operational_context.py`, `ci_ppi_bond_estimate_patch_hf6.py`
- `ck`: `ck_contract_evidence_runner_hf6.py`, `ck_policy_gate_hf6.py`
- `co`: `co_contract_ingestion_policy_hf6.py`, `co_market_sessions_hf6.py`
- `cp`: `cp_contract_evidence_v2_hf6.py`, `cp_history_ingest_policy_hf6.py`
- `cq`: `cq_contract_readiness_hf6.py`, `cq_family_contract_rules_hf6.py`, `cq_history_attempt_ledger_hf6.py`
- `cr`: `cr_data912_reconcile_hf6.py`, `cr_pending_settlement_diagnostics_hf6.py`
- `cy`: `cy_a3_contract_bridge_hf6.py`, `cy_market_source_arbitration_hf6.py`
- `db`: `db_a3_cem_normalizer_hf6.py`, `db_dashboard_logs_hf6.py`
- `de`: `de_concurrent_risk_capacity_hf6.py`, `de_scheduler_catalog_hf6.py`
- `df`: `df_caucion_end_of_day_sweep_hf6.py`, `df_daily_operation_summary_hf6.py`
- `dh`: `dh_dashboard_compact_lists_hf6.py`, `dh_paper_dynamic_risk_gate_hf6.py`
- `m`: `m_instrument_universe.py`, `m_introspection_engine.py`
- `ops`: `ops_disk_inventory_hf6.py`, `ops_introspection_hf4.py`, `ops_publish_introspection_hf6.py`
- `r`: `r_clear_kill_switch.py`, `r_news_engine_247.py`

## Política RC4

- `REVIEW_REQUIRED` **no se elimina automáticamente**.
- Toda eliminación futura requiere evidencia de no uso + tests + justificación en la auditoría.
- Las colisiones de prefijo son deuda de nomenclatura, no motivo suficiente para renombrar en caliente y romper imports.
