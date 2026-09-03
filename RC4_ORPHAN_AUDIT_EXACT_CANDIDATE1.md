# RC4 — Auditoría exacta de alcanzabilidad de módulos (candidate1)

Fuente auditada: `Porota-Trading-17.0.0-rc3-hf6-v2-candidate1-OPERATIVE-AUDIT.zip`  
SHA-256: `8e8c7a2596e6575c5e680d6e5c01a79ebb3ac1e89a26f11f7ba5bb4e1cd38499`  
Análisis: AST imports + import dinámico literal + subprocess `.py` literal + referencias de scripts/deploy/systemd + raíces runtime split canónicas.

## Resultado

- Archivos totales del ZIP: **289**.
- Módulos Python: **212**.
- Módulos Python top-level no test: **131**.
- Tests Python: **70** (incluye tests top-level heredados).
- Top-level alcanzables desde runtime/scheduler/tooling versionado: **100**.
- Pendientes de wiring explícito: **20**.
- Huérfanos inesperados sin clasificación: **0** después de clasificar por intención verificada.

> `PENDING_WIRING` NO significa que el módulo funcione hoy. Significa que existe código sin camino operativo final y RC4 debe conectarlo o deferirlo explícitamente antes de release.

## Módulos no alcanzables por runtime/scheduler que requieren decisión

| Módulo | Clasificación RC4 | Evidencia/decisión |
|---|---|---|
| `ap_api_verifier` | `MANUAL_ADMIN_TOOL` | Verificador manual de APIs, alcanzado por tests pero no por runtime. |
| `aq_macro_backtest` | `OFFLINE_TOOL` | Herramienta offline con `__main__`; no pertenece al admission path. |
| `bh_paper_gemini` | `COMPATIBILITY_INACTIVE` | Compatibilidad heredada; IA intradiaria OFF. No reactivar en RC4. |
| `bj_sandbox_health_probe` | `MANUAL_ADMIN_TOOL` | Probe manual Sandbox; no debe entrar al runtime PPI producción. |
| `bo_signal_core` | `OFFLINE_REPLAY_LIBRARY` | Biblioteca candidata para replay offline; no promovida al motor. |
| `br_backtest_gate` | `OFFLINE_REPLAY_LIBRARY` | Validación temporal/replay offline. |
| `bx_execution_replay` | `OFFLINE_REPLAY_TOOL` | Motor de replay offline. |
| `by_strategy_backtest` | `OFFLINE_REPLAY_LIBRARY` | Backtest de estrategia candidato offline. |
| `bz_replay_risk` | `OFFLINE_REPLAY_LIBRARY` | Modelo de riesgo para replay offline. |
| `co_contract_ingestion_policy_hf6` | `PENDING_WIRING` | Política de cadencia Contract Evidence: conectar al Scheduler RC4. |
| `co_market_sessions_hf6` | `PENDING_WIRING` | Modelo de sesiones por familia: conectar o deferir explícitamente. |
| `cp_contract_evidence_v2_hf6` | `PENDING_WIRING` | Store Contract Evidence v2; materialización operativa pendiente. |
| `cp_history_ingest_policy_hf6` | `PENDING_WIRING` | Política de ingesta v2; pendiente del pipeline History. |
| `cq_contract_readiness_hf6` | `PENDING_WIRING` | Readiness V2: debe consumir evidencia normalizada y fresca. |
| `cq_family_contract_rules_hf6` | `PENDING_WIRING` | Reglas contractuales por familia; sólo útiles tras normalización. |
| `cq_history_attempt_ledger_hf6` | `PENDING_WIRING` | Ledger de intentos de History Store: conectar al job post-close. |
| `cr_data912_reconcile_hf6` | `PENDING_WIRING` | Fallback Data912 post-close; nunca autoridad live. |
| `cr_pending_settlement_diagnostics_hf6` | `PENDING_WIRING` | Diagnóstico settlement/caja inmovilizada: integrar al dashboard. |
| `cs_postclose_history_scheduler_hf6` | `PENDING_WIRING` | Raíz huérfana confirmada por auditoría: crear entrypoint/timer con locking. |
| `ct_ppi_history_salvage_hf6` | `PENDING_WIRING` | Salvage PPI History: conectar detrás del job post-close. |
| `cv_history_store_adapter_hf6` | `PENDING_WIRING` | Adapter al History Store separado. |
| `cw_data912_history_v2_sink` | `PENDING_WIRING` | Sink Data912 a History Store v2. |
| `cx_a3_primary_readonly_hf6` | `PENDING_WIRING` | Cliente A3 Primary read-only; no autoridad live. |
| `cy_a3_contract_bridge_hf6` | `PENDING_WIRING` | Bridge A3→Contract Evidence sólo enrichment/background. |
| `cy_market_source_arbitration_hf6` | `PENDING_WIRING` | Precedencia de fuentes PPI/A3: conectar a History/Contract reconciliation. |
| `cz_a3_cem_public_history_hf6` | `PENDING_WIRING` | A3 CEM public history; timer post-close pendiente. |
| `db_a3_cem_normalizer_hf6` | `PENDING_WIRING` | Normalizador A3 CEM detrás del job A3. |
| `df_caucion_end_of_day_sweep_hf6` | `PENDING_WIRING` | Planner de cash sweep; HOLD contractual. |
| `di_caucion_cash_sweep_runtime_hf6` | `PENDING_WIRING` | Orquestación caución; routing debe seguir prohibido. |
| `q_backtest` | `DEPRECATED_RETAINED` | Backtest legado/autodeclarado no validado; mantener sólo hasta retiro documentado. |
| `r_clear_kill_switch` | `MANUAL_ADMIN_TOOL` | Herramienta manual de seguridad; por diseño no debe ser llamada automáticamente. |

## Artefacto `:memory:.ses`

- El ZIP contiene `:memory:.ses` y los manifiestos HF2/HF5 ya lo arrastraban.
- Una búsqueda exhaustiva en los 212 módulos y scripts de candidate1 no encontró un productor actual que construya archivos `*.ses`.
- `ay_dashboard_auth.py` persiste sesiones en `data/dashboard_sessions.json`, no en `*.ses`.
- Por lo tanto RC4 lo clasifica como **LEGACY_RELEASE_ARTIFACT / productor actual no localizado**.
- Acción: excluir `*.ses`/`:memory:*`, retirar el artefacto del source de release y mantener un guard que falle si reaparece. No inventar un productor inexistente.

## Colisiones de prefijos

La auditoría reportó 15 colisiones de prefijo de dos letras. Sobre el ZIP exacto se observan **16**: se suma `bh_` (`bh_paper_gemini.py` y `bh_universe_dashboard_hf6.py`).

Esto no rompe imports, pero confirma la recomendación de mantener `MODULOS.md` y no hacer un renombrado masivo dentro de RC4.

## Release gate

RC4 no puede declarar el inventario de módulos `PASS` mientras exista cualquier `PENDING_WIRING` sin una decisión final `CONNECTED`, `DEFERRED_WITH_REASON`, `HOLD_WITH_REASON` o `DEPRECATED_WITH_REASON`.
