# Índice histórico de Porota RC6

Estos enlaces fijan versiones de Git anteriores a la consolidación documental.
Conservan código citado, decisiones, RCA y evidencia de su fecha. No son instrucciones
vigentes, autorización de operación ni una lista de tareas que deba repetirse.
Para trabajar, usar [AGENTS.md](../../AGENTS.md) y [RUNBOOK.md](../RUNBOOK.md).

## Fuentes preservadas

- Candidato anterior: [0e7577fc6112406794ffbc392c902904481f8ca0](https://github.com/mbalbo2023/Porota-trading/tree/0e7577fc6112406794ffbc392c902904481f8ca0).
- Plano de control anterior: [9d3f89d2826b46d842a80b740bc24e272aa81e8d](https://github.com/mbalbo2023/Porota-trading/tree/9d3f89d2826b46d842a80b740bc24e272aa81e8d).
- Los PR cerrados y sus discusiones conservan su historia en GitHub; este índice
  no los reabre, fusiona ni elimina branches.
- Consultar sólo el antecedente relevante. No es obligatorio recorrer este índice
  para empezar una tarea o para aprobar un release.

## Criterio de consolidación

Se retiran 170 documentos del árbol del candidato: checkpoints, status
markers, planes y procedimientos anteriores, registros de ownership y RCA fechados.
Las reglas vigentes se concentran en AGENTS, la política estructurada y un runbook.
Cada archivo retirado tiene debajo un enlace inmutable a su contenido original.

La revisión de consumidores buscó rutas completas y nombres con `rg` en código,
workflows, tests, Docker y manifiestos. Los manifiestos RC4/HF2/HF5/HF6 conservados
describen paquetes históricos: no son inventarios del árbol actual. El instalador
legacy HF6 valida su baseline antiguo y no es parte de Predeploy/Promote RC6.
Un filtro documental de un workflow antiguo y un generador de inventario RC4
no constituyen dependencias del runtime. Las URLs de procedencia fijadas a otro
SHA siguen resolviendo sus originales. No se modifican esos manifiestos ni datos.

El checkpoint `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-05.md` permanece
en su ruta por compatibilidad con cuatro scripts históricos que lo leen o editan;
su banner lo identifica como histórico. No debe volver a usarse como bootstrap.
SECURITY y las referencias técnicas de evidencia contractual/runtime permanecen.
La política YAML deja de contener un baseline de despliegue fechado; su versión
anterior está [preservada en Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/ops/policy/porota-policy.yaml).

## Prueba histórica de A3 read-only

El [módulo que estaba en la raíz](retired-tests/test_a3_primary_readonly_hf6.py.txt)
ya se excluía de pytest por colisión de nombre. Se conserva íntegro como texto.
El [contrato vigente](../../tests/test_a3_primary_readonly_hf6.py) sigue en la
colección automática. La [exclusión anterior](https://github.com/mbalbo2023/Porota-trading/blob/373c1e985590cd5bd0b4fbc80f389eaa2e7d5a5e/ops/policy/test-policy.yaml#L40)
documenta el motivo y el sucesor.

## Registros y planes de raíz (103)

| Archivo original | Versión preservada |
|---|---|
| `A3_PRIMARY_READONLY_HF6_V2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/A3_PRIMARY_READONLY_HF6_V2.md) |
| `AUDITORIA_FULL_DASHBOARD_RC6_2026-09-29.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/AUDITORIA_FULL_DASHBOARD_RC6_2026-09-29.md) |
| `AUDIT_RESPONSE_RC4.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/AUDIT_RESPONSE_RC4.md) |
| `CHECKPOINT_00_WS_CAUCION_11_RECONCILED_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_CAUCION_11_RECONCILED_2026-10-01.md) |
| `CHECKPOINT_00_WS_CONSOLIDATED_ALL_PENDING_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_CONSOLIDATED_ALL_PENDING_2026-10-01.md) |
| `CHECKPOINT_00_WS_CONTROLPLANE_16_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_CONTROLPLANE_16_2026-10-01.md) |
| `CHECKPOINT_00_WS_DASH_06_OCT01_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_DASH_06_OCT01_2026-09-30.md) |
| `CHECKPOINT_00_WS_DASH_07_HISTORY_TRUTH_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_DASH_07_HISTORY_TRUTH_2026-10-01.md) |
| `CHECKPOINT_00_WS_HISTORY_08_OCT01_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_HISTORY_08_OCT01_2026-09-30.md) |
| `CHECKPOINT_00_WS_HISTORY_09_PROGRESS_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_HISTORY_09_PROGRESS_2026-10-01.md) |
| `CHECKPOINT_00_WS_INSTRUMENTATION_09_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_INSTRUMENTATION_09_2026-09-30.md) |
| `CHECKPOINT_00_WS_INTEG_13_RECONCILED_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_INTEG_13_RECONCILED_2026-10-01.md) |
| `CHECKPOINT_00_WS_RUNTIME_07_OCT01_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_RUNTIME_07_OCT01_2026-09-30.md) |
| `CHECKPOINT_00_WS_TESTS_12_RECONCILE_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_TESTS_12_RECONCILE_2026-10-01.md) |
| `CHECKPOINT_00_WS_TESTS_15_GOVERNED_ROOT_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_00_WS_TESTS_15_GOVERNED_ROOT_2026-10-01.md) |
| `CHECKPOINT_01_WS_CAUCION_11_GREEN_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_01_WS_CAUCION_11_GREEN_2026-10-01.md) |
| `CHECKPOINT_01_WS_HISTORY_08_OCT01_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_01_WS_HISTORY_08_OCT01_2026-10-01.md) |
| `CHECKPOINT_01_WS_HISTORY_09_RUNTIME_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_01_WS_HISTORY_09_RUNTIME_2026-10-01.md) |
| `CHECKPOINT_01_WS_RUNTIME_07_OCT01_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_01_WS_RUNTIME_07_OCT01_2026-10-01.md) |
| `CHECKPOINT_02_WS_OPS_RUNTIME_FINDINGS_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_02_WS_OPS_RUNTIME_FINDINGS_2026-09-30.md) |
| `CHECKPOINT_RC6_DEPLOY_IMAGE_RETENTION_GUARD_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_RC6_DEPLOY_IMAGE_RETENTION_GUARD_2026-10-01.md) |
| `CHECKPOINT_RC6_POSTAUDIT_FINAL_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_RC6_POSTAUDIT_FINAL_2026-10-01.md) |
| `CHECKPOINT_RELEASE_RC6_ZERO_KNOWN_ERROR_V3_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_RELEASE_RC6_ZERO_KNOWN_ERROR_V3_2026-10-01.md) |
| `CHECKPOINT_WS_DASH_05_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_WS_DASH_05_2026-09-30.md) |
| `CHECKPOINT_WS_DEPLOY_RCA_05_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_WS_DEPLOY_RCA_05_2026-09-30.md) |
| `CHECKPOINT_WS_DEPLOY_RCA_06_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_WS_DEPLOY_RCA_06_2026-09-30.md) |
| `CHECKPOINT_WS_DISK_07B_PRETRANSFER_GUARD_FIX_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_WS_DISK_07B_PRETRANSFER_GUARD_FIX_2026-10-01.md) |
| `CHECKPOINT_WS_DISK_07_HOUSEKEEPING_POLICY_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_WS_DISK_07_HOUSEKEEPING_POLICY_2026-10-01.md) |
| `CHECKPOINT_WS_INSTRUMENTATION_10_2026-10-01.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_WS_INSTRUMENTATION_10_2026-10-01.md) |
| `CHECKPOINT_WS_OPS_PREOPEN_CLOSURE_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/CHECKPOINT_WS_OPS_PREOPEN_CLOSURE_2026-09-30.md) |
| `HANDOFF_WS_DASH_04_FULL_DASHBOARD_TRUTH_2026-09-29.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/HANDOFF_WS_DASH_04_FULL_DASHBOARD_TRUTH_2026-09-29.md) |
| `HANDOFF_WS_MOTOR_11_BROKER_PARITY_FULL_INTEGRATION_2026-09-28.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/HANDOFF_WS_MOTOR_11_BROKER_PARITY_FULL_INTEGRATION_2026-09-28.md) |
| `HF6_ACCEPTANCE_20260901.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/HF6_ACCEPTANCE_20260901.md) |
| `HOTFIX_RC3_HF1.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/HOTFIX_RC3_HF1.md) |
| `HOTFIX_RC3_HF2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/HOTFIX_RC3_HF2.md) |
| `HOTFIX_RC3_HF4.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/HOTFIX_RC3_HF4.md) |
| `HOTFIX_RC3_HF6.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/HOTFIX_RC3_HF6.md) |
| `HOTFIX_RC4_HF2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/HOTFIX_RC4_HF2.md) |
| `INTRADAY_AI_DEPRECATED.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/INTRADAY_AI_DEPRECATED.md) |
| `MODOS_DE_OPERACION_V1634.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/MODOS_DE_OPERACION_V1634.md) |
| `PATCH_HF6_CONTRACT_EVIDENCE_V2_WIP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/PATCH_HF6_CONTRACT_EVIDENCE_V2_WIP.md) |
| `PATCH_HF6_V2_PLAN.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/PATCH_HF6_V2_PLAN.md) |
| `PENDIENTES_POST_V1633.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/PENDIENTES_POST_V1633.md) |
| `PLAN_DE_TESTING.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/PLAN_DE_TESTING.md) |
| `PLAN_WS_OPS_OCT01_2026-09-30.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/PLAN_WS_OPS_OCT01_2026-09-30.md) |
| `POROTA_RC6_ANNUAL_ANALYSIS_CHECKPOINT_2026-09-21.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_RC6_ANNUAL_ANALYSIS_CHECKPOINT_2026-09-21.md) |
| `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-10_2033_INTEGRATED_GREEN_FH_GREEN_PREDEPLOY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-10_2033_INTEGRATED_GREEN_FH_GREEN_PREDEPLOY.md) |
| `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-10_2050_POSTDEPLOY_FINAL.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-10_2050_POSTDEPLOY_FINAL.md) |
| `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-10_CONTINUIDAD.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-10_CONTINUIDAD.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-10_1235_CONTINUIDAD_PARALELA.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-10_1235_CONTINUIDAD_PARALELA.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-10_2236_ACTIONS_BILLING_UNBLOCKED.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-10_2236_ACTIONS_BILLING_UNBLOCKED.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP0_PLAN_Y_HALLAZGOS.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP0_PLAN_Y_HALLAZGOS.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP1B_RCA_EXACTO_PRE_FIX.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP1B_RCA_EXACTO_PRE_FIX.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP1_CP8_RECOVERY_CORRECTION.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP1_CP8_RECOVERY_CORRECTION.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP1_MATRIZ_FUNCIONAL.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP1_MATRIZ_FUNCIONAL.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP2_CP4_CP5_SOURCE_PREP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP2_CP4_CP5_SOURCE_PREP.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP2_SOURCE_WIRING.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP2_SOURCE_WIRING.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP3_CP4_RUNTIME_RCA.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP3_CP4_RUNTIME_RCA.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP4_DLR_FORWARD_FIX.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP4_DLR_FORWARD_FIX.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP5_GDELT_UX_SOURCE_WIRING.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP5_GDELT_UX_SOURCE_WIRING.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_PARALLEL_CP2_CP5_BARRIER.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_PARALLEL_CP2_CP5_BARRIER.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_RECOVERY_GO_LIVE_WATCH.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_RECOVERY_GO_LIVE_WATCH.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_RUNNER_PROBE_PARALLEL_CONTINUITY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_RUNNER_PROBE_PARALLEL_CONTINUITY.md) |
| `POROTA_TRADING_CHECKPOINT_RC6_2026-09-15_ISSUE67_ACTION4.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_RC6_2026-09-15_ISSUE67_ACTION4.md) |
| `POROTA_TRADING_RC6_CONTEXT_HANDOFF.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_RC6_CONTEXT_HANDOFF.md) |
| `POROTA_TRADING_RC6_DATA_LIFECYCLE_SCHEDULER_BACKFILL_BACKTEST_2026-09-09.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_RC6_DATA_LIFECYCLE_SCHEDULER_BACKFILL_BACKTEST_2026-09-09.md) |
| `PREOPEN_RETIRE_HF6_V2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/PREOPEN_RETIRE_HF6_V2.md) |
| `RC4_DAILY_READINESS_PLAN.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC4_DAILY_READINESS_PLAN.md) |
| `RC4_DEPLOY_CONTRACT.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC4_DEPLOY_CONTRACT.md) |
| `RC4_MODULE_INVENTORY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC4_MODULE_INVENTORY.md) |
| `RC4_SCRIPT_INVENTORY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC4_SCRIPT_INVENTORY.md) |
| `RC4_TEST_REPORT.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC4_TEST_REPORT.md) |
| `RC6_CI_REVALIDATION_2026-09-10.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC6_CI_REVALIDATION_2026-09-10.md) |
| `RC6_DEPLOY2_READY_2026-09-06.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC6_DEPLOY2_READY_2026-09-06.md) |
| `RC6_DEPLOY_PREFLIGHT_2026-09-16.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC6_DEPLOY_PREFLIGHT_2026-09-16.md) |
| `RC6_GDELT_SHADOW_DESIGN.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC6_GDELT_SHADOW_DESIGN.md) |
| `RC6_RIESGO_MACRO_BCRA_SHADOW.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC6_RIESGO_MACRO_BCRA_SHADOW.md) |
| `RC6_VALIDATION_COMPACT_OPERATION.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/RC6_VALIDATION_COMPACT_OPERATION.md) |
| `REVISION_SANDBOX_RC1.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/REVISION_SANDBOX_RC1.md) |
| `STORAGE_LIFECYCLE_HF6_V2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/STORAGE_LIFECYCLE_HF6_V2.md) |
| `VALIDATION_CHECKPOINT_HF6_V2_2026-09-02_2255.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/VALIDATION_CHECKPOINT_HF6_V2_2026-09-02_2255.md) |
| `W10_BINDING_POLICY_CHECKPOINT.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W10_BINDING_POLICY_CHECKPOINT.md) |
| `W10_CURRENT_STATUS.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W10_CURRENT_STATUS.md) |
| `W10_STATUS_MARKER_2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W10_STATUS_MARKER_2.md) |
| `W10_STATUS_MARKER_3.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W10_STATUS_MARKER_3.md) |
| `W10_STATUS_MARKER_4.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W10_STATUS_MARKER_4.md) |
| `W10_STATUS_MARKER_5.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W10_STATUS_MARKER_5.md) |
| `W16_FINAL_POLICY_CHECKPOINT.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W16_FINAL_POLICY_CHECKPOINT.md) |
| `W16_STATUS_MARKER.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W16_STATUS_MARKER.md) |
| `W16_STATUS_MARKER_2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W16_STATUS_MARKER_2.md) |
| `W16_STATUS_MARKER_3.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W16_STATUS_MARKER_3.md) |
| `W16_STATUS_MARKER_4.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W16_STATUS_MARKER_4.md) |
| `W17_FORMALIZATION_CHECKPOINT.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W17_FORMALIZATION_CHECKPOINT.md) |
| `W17_STATUS_MARKER.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W17_STATUS_MARKER.md) |
| `W17_STATUS_MARKER_2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W17_STATUS_MARKER_2.md) |
| `W17_STATUS_MARKER_3.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W17_STATUS_MARKER_3.md) |
| `W17_STATUS_MARKER_4.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W17_STATUS_MARKER_4.md) |
| `W18_NO_AUTO_ROLLBACK_POLICY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W18_NO_AUTO_ROLLBACK_POLICY.md) |
| `W18_PACKAGE_CHECKPOINT.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W18_PACKAGE_CHECKPOINT.md) |
| `W18_STATUS_MARKER.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W18_STATUS_MARKER.md) |
| `W18_STATUS_MARKER_2.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W18_STATUS_MARKER_2.md) |
| `W18_STATUS_MARKER_3.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W18_STATUS_MARKER_3.md) |
| `W18_STATUS_MARKER_4.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/W18_STATUS_MARKER_4.md) |

## Evidencia de releases (20)

| Archivo original | Versión preservada |
|---|---|
| `docs/releases/RC6_679_BACKUP_DASH_AUDIT_20261021.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_679_BACKUP_DASH_AUDIT_20261021.md) |
| `docs/releases/RC6_714_PENDING_PAPER_CAUCION_DEPLOY_20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_714_PENDING_PAPER_CAUCION_DEPLOY_20261022.md) |
| `docs/releases/RC6_716_PENDING_COLD_STOP_FIX_FORWARD_20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_716_PENDING_COLD_STOP_FIX_FORWARD_20261022.md) |
| `docs/releases/RC6_717_OFFLINE_IMAGE_DISK_GUARD_20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_717_OFFLINE_IMAGE_DISK_GUARD_20261022.md) |
| `docs/releases/RC6_A108_FEE_RCA_20261026.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A108_FEE_RCA_20261026.md) |
| `docs/releases/RC6_A166_CLASSIFIER_AND_IOL_RCA_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A166_CLASSIFIER_AND_IOL_RCA_20261028.md) |
| `docs/releases/RC6_A167_PREHOST_CLASSIFIER_RCA_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A167_PREHOST_CLASSIFIER_RCA_20261028.md) |
| `docs/releases/RC6_A169_STAGED_GZIP_HELD_STOP_RCA_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A169_STAGED_GZIP_HELD_STOP_RCA_20261028.md) |
| `docs/releases/RC6_A171_COLD2_HELD_RECEIPT_RCA_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A171_COLD2_HELD_RECEIPT_RCA_20261028.md) |
| `docs/releases/RC6_A172_RUNNING_A171_FIX_FORWARD_IVAC_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A172_RUNNING_A171_FIX_FORWARD_IVAC_20261028.md) |
| `docs/releases/RC6_A174_SCHEDULER_DISPATCH_RCA_IVAC_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A174_SCHEDULER_DISPATCH_RCA_IVAC_20261028.md) |
| `docs/releases/RC6_A175_POSTLOGIN_STARTUP_RCA_IVAC_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A175_POSTLOGIN_STARTUP_RCA_IVAC_20261028.md) |
| `docs/releases/RC6_A176_HANDOFF_20261029.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A176_HANDOFF_20261029.md) |
| `docs/releases/RC6_A77_SEMAFORO_PREDEPLOY_20261026.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A77_SEMAFORO_PREDEPLOY_20261026.md) |
| `docs/releases/RC6_A79_FAMILY_STRATEGY_READINESS_20261026.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_A79_FAMILY_STRATEGY_READINESS_20261026.md) |
| `docs/releases/RC6_FUNCTIONAL_RECONCILIATION_2026-10-14.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_FUNCTIONAL_RECONCILIATION_2026-10-14.md) |
| `docs/releases/RC6_ISSUE600_20261016.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_ISSUE600_20261016.md) |
| `docs/releases/RC6_ISSUE600_PR_PREPARATION.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_ISSUE600_PR_PREPARATION.md) |
| `docs/releases/RC6_ISSUE624_COLD_STORAGE_RECOVERY_20261019.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_ISSUE624_COLD_STORAGE_RECOVERY_20261019.md) |
| `docs/releases/RC6_UNIFIED_HANDOFF_2026-10-02.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/releases/RC6_UNIFIED_HANDOFF_2026-10-02.md) |

## Auditorías, diseño y handoffs (45)

| Archivo original | Versión preservada |
|---|---|
| `docs/DEPLOY_V2_ARCHITECTURE.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/DEPLOY_V2_ARCHITECTURE.md) |
| `docs/DEPLOY_V2_ARTIFACT_PROMOTION.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/DEPLOY_V2_ARTIFACT_PROMOTION.md) |
| `docs/RC6_A207_PAPER_ADMIN_ACCOUNTING_RCA_20261030.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/RC6_A207_PAPER_ADMIN_ACCOUNTING_RCA_20261030.md) |
| `docs/RC6_HISTORICAL_CANDLE_SHADOW_OPERATIONS.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/RC6_HISTORICAL_CANDLE_SHADOW_OPERATIONS.md) |
| `docs/RC6_IOL_DEPLOY_RCA_2026-09-24.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/RC6_IOL_DEPLOY_RCA_2026-09-24.md) |
| `docs/V17_AUDITORIA_Y_PLAN.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_AUDITORIA_Y_PLAN.md) |
| `docs/V17_LEDGER_PREFLIGHT.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_LEDGER_PREFLIGHT.md) |
| `docs/V17_LEDGER_RESULTADO_20260828.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_LEDGER_RESULTADO_20260828.md) |
| `docs/V17_PAPER_DATASET_RESULTADO_20260829.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_PAPER_DATASET_RESULTADO_20260829.md) |
| `docs/V17_PAPER_SEPARACION.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_PAPER_SEPARACION.md) |
| `docs/V17_PPI_CAUCIONES_CONSULTA_PENDIENTE.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_PPI_CAUCIONES_CONSULTA_PENDIENTE.md) |
| `docs/V17_PPI_DIAGNOSTICO.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_PPI_DIAGNOSTICO.md) |
| `docs/V17_PREINSTALACION.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_PREINSTALACION.md) |
| `docs/V17_PREPARACION_DIRECTORIO.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/V17_PREPARACION_DIRECTORIO.md) |
| `docs/WS_MOTOR_09_OWNERSHIP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_09_OWNERSHIP.md) |
| `docs/WS_MOTOR_09_REVIEW.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_09_REVIEW.md) |
| `docs/WS_MOTOR_11_DECISION_AUDIT.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_11_DECISION_AUDIT.md) |
| `docs/WS_MOTOR_11_FIELD_SOURCE_MATRIX.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_11_FIELD_SOURCE_MATRIX.md) |
| `docs/WS_MOTOR_11_OWNERSHIP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_11_OWNERSHIP.md) |
| `docs/WS_MOTOR_11_QUE_AGREGA_EL_MCP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_11_QUE_AGREGA_EL_MCP.md) |
| `docs/WS_MOTOR_11_QUE_AGREGA_EL_SCRAPING.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_11_QUE_AGREGA_EL_SCRAPING.md) |
| `docs/WS_MOTOR_11_RECONCILIATION_AND_RCA.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_11_RECONCILIATION_AND_RCA.md) |
| `docs/WS_MOTOR_12_FIELD_SOURCE_MATRIX.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_12_FIELD_SOURCE_MATRIX.md) |
| `docs/WS_MOTOR_12_OWNERSHIP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_12_OWNERSHIP.md) |
| `docs/WS_MOTOR_12_QUE_AGREGA_EL_MCP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_12_QUE_AGREGA_EL_MCP.md) |
| `docs/WS_MOTOR_12_QUE_AGREGA_EL_SCRAPING.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_12_QUE_AGREGA_EL_SCRAPING.md) |
| `docs/WS_MOTOR_12_RCA_FIX_GUARD_TEST.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_12_RCA_FIX_GUARD_TEST.md) |
| `docs/WS_MOTOR_13_OWNERSHIP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_13_OWNERSHIP.md) |
| `docs/WS_MOTOR_14_OWNERSHIP.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_14_OWNERSHIP.md) |
| `docs/WS_MOTOR_14_PAPER_POLICIES.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/WS_MOTOR_14_PAPER_POLICIES.md) |
| `docs/a198-family-source-engine-audit.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/a198-family-source-engine-audit.md) |
| `docs/a200-consolidated-six-fronts-rc6.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/a200-consolidated-six-fronts-rc6.md) |
| `docs/architecture/RC6_624_DURABLE_AUDIT_ADR.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/architecture/RC6_624_DURABLE_AUDIT_ADR.md) |
| `docs/audit/RC6_A172_IOL_GUARDIAN_RETRY_IVAC_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/audit/RC6_A172_IOL_GUARDIAN_RETRY_IVAC_20261028.md) |
| `docs/audit/RC6_A175_PREOPEN_IMAGE_IDENTITY_IVAC_20261029.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/audit/RC6_A175_PREOPEN_IMAGE_IDENTITY_IVAC_20261029.md) |
| `docs/audit/RC6_A178_DEPLOY_TRANSITION_RCA_20261029.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/audit/RC6_A178_DEPLOY_TRANSITION_RCA_20261029.md) |
| `docs/audit/RC6_A178_HISTORICAL_DEPLOY_DEGRADATION_20261029.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/audit/RC6_A178_HISTORICAL_DEPLOY_DEGRADATION_20261029.md) |
| `docs/audit/RC6_A178_PENDING_ORIGIN_WIRING_RCA_20261029.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/audit/RC6_A178_PENDING_ORIGIN_WIRING_RCA_20261029.md) |
| `docs/checkpoints/POROTA_RC6_CHECKPOINT_2026-09-24_IOL_DEPLOY_COMPLETENESS.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/checkpoints/POROTA_RC6_CHECKPOINT_2026-09-24_IOL_DEPLOY_COMPLETENESS.md) |
| `docs/checkpoints/RC6_SHADOW_SYSTEMIC_20261015.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/checkpoints/RC6_SHADOW_SYSTEMIC_20261015.md) |
| `docs/dashboard/WS_DASH_TRADER_TERMINAL_08_ARCHITECTURE_2026-10-04.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/dashboard/WS_DASH_TRADER_TERMINAL_08_ARCHITECTURE_2026-10-04.md) |
| `docs/dashboard/WS_DASH_TRADER_TERMINAL_08_HANDOFF.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/dashboard/WS_DASH_TRADER_TERMINAL_08_HANDOFF.md) |
| `docs/handoffs/RC6_DEPLOY_CONTINUITY_AND_OPEN_ITEMS_2026-09-17.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/handoffs/RC6_DEPLOY_CONTINUITY_AND_OPEN_ITEMS_2026-09-17.md) |
| `docs/rc6-685-cold-archive-closed-stop-20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/rc6-685-cold-archive-closed-stop-20261022.md) |
| `docs/rc6-685-postinstall-contract-v1-expiry-20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/rc6-685-postinstall-contract-v1-expiry-20261022.md) |

## Procedimientos de política anteriores (2)

| Archivo original | Versión preservada |
|---|---|
| `ops/policy/FAILURE_LEARNING.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/ops/policy/FAILURE_LEARNING.md) |
| `ops/policy/rc6-storage-history-reprojection.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/ops/policy/rc6-storage-history-reprojection.md) |

## Versiones anteriores de archivos conservados

| Archivo | Versión anterior |
|---|---|
| `AGENTS.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/AGENTS.md) |
| `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-05.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-05.md) |
| `README.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/README.md) |
| `SECURITY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/SECURITY.md) |
| `docs/HF6_CONTRACT_EVIDENCE_POLICY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/HF6_CONTRACT_EVIDENCE_POLICY.md) |
| `docs/RUNTIME_EVIDENCE_CHANNEL_RC6.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/0e7577fc6112406794ffbc392c902904481f8ca0/docs/RUNTIME_EVIDENCE_CHANNEL_RC6.md) |

## Documentación anterior del plano de control (37)

El siguiente inventario permite consolidar main sin perder sus documentos propios.
La presencia de un enlace aquí no prueba que una modificación se haya integrado en main.
Los arranques de septiembre, el agente de rescate de octubre, la skill Docker del
workflow retirado, los dos runbooks anteriores y la unanimidad ficticia se sustituyen
por las instrucciones compactas del candidato. Los registros fechados se consultan
a través de estos enlaces, sin copiar sus instrucciones al procedimiento vigente.

| Archivo original | Versión preservada |
|---|---|
| `.agents/skills/porota-docker-image-retention/SKILL.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/.agents/skills/porota-docker-image-retention/SKILL.md) |
| `.github/agents/rc6-market-rescue.agent.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/.github/agents/rc6-market-rescue.agent.md) |
| `.github/pull_request_template.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/.github/pull_request_template.md) |
| `AGENTS.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/AGENTS.md) |
| `POROTA_RC6_DOCKER_IMAGE_RETENTION_CHECKPOINT_2026-09-19.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/POROTA_RC6_DOCKER_IMAGE_RETENTION_CHECKPOINT_2026-09-19.md) |
| `POROTA_TRADING_CHAT_START_HERE.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/POROTA_TRADING_CHAT_START_HERE.md) |
| `POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md) |
| `POROTA_TRADING_CHECKPOINT_CONTRACT_GAP_MATRIX_2026-09-14.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/POROTA_TRADING_CHECKPOINT_CONTRACT_GAP_MATRIX_2026-09-14.md) |
| `POROTA_TRADING_CHECKPOINT_OPEN_SESSION_CAUCIONES_PROBE_2026-09-14.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/POROTA_TRADING_CHECKPOINT_OPEN_SESSION_CAUCIONES_PROBE_2026-09-14.md) |
| `POROTA_TRADING_CHECKPOINT_OPEN_SESSION_CONTRACT_PROBE_2026-09-14.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/POROTA_TRADING_CHECKPOINT_OPEN_SESSION_CONTRACT_PROBE_2026-09-14.md) |
| `POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md) |
| `README.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/README.md) |
| `docs/audits/RC6_20261022_MARKET_WATCH_P0_FIX_FORWARD.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/audits/RC6_20261022_MARKET_WATCH_P0_FIX_FORWARD.md) |
| `docs/governance/RC6_A620_PID1_POLICY_READONLY_20261103.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/governance/RC6_A620_PID1_POLICY_READONLY_20261103.md) |
| `docs/governance/RC6_DEPLOY_V2_CONTINUITY_20261028.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/governance/RC6_DEPLOY_V2_CONTINUITY_20261028.md) |
| `docs/governance/RC6_NIGHT_AUTONOMOUS_EXECUTION_20261113.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/governance/RC6_NIGHT_AUTONOMOUS_EXECUTION_20261113.md) |
| `docs/governance/RC6_TEAM_ROLES_AND_RACI_20261105.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/governance/RC6_TEAM_ROLES_AND_RACI_20261105.md) |
| `docs/handoffs/RC6_696_20261022_FINAL_OUTCOME.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_696_20261022_FINAL_OUTCOME.md) |
| `docs/handoffs/RC6_696_20261022_NO_GO_STORAGE_HISTORY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_696_20261022_NO_GO_STORAGE_HISTORY.md) |
| `docs/handoffs/RC6_696_DEPLOY_V2_PROCESS_LESSONS_20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_696_DEPLOY_V2_PROCESS_LESSONS_20261022.md) |
| `docs/handoffs/RC6_696_NEXT_AUDIT_IMPLEMENTATION_HANDOFF_20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_696_NEXT_AUDIT_IMPLEMENTATION_HANDOFF_20261022.md) |
| `docs/handoffs/RC6_719_BIG_HUB_RECONCILIATION_20261023.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_719_BIG_HUB_RECONCILIATION_20261023.md) |
| `docs/handoffs/RC6_719_GENERAL_AUDIT_NIGHT_HANDOFF_20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_719_GENERAL_AUDIT_NIGHT_HANDOFF_20261022.md) |
| `docs/handoffs/RC6_A617_PPI_ORIGIN_SELECTOR_20261103.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_A617_PPI_ORIGIN_SELECTOR_20261103.md) |
| `docs/handoffs/RC6_A630_PPI_CURRENT_BOOK_20261104.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_A630_PPI_CURRENT_BOOK_20261104.md) |
| `docs/handoffs/RC6_A652_ERROR_BUCKETS_READONLY_20261104.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_A652_ERROR_BUCKETS_READONLY_20261104.md) |
| `docs/handoffs/RC6_A652_WATCH_BUDGET_MICRO_RO_20261104.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/handoffs/RC6_A652_WATCH_BUDGET_MICRO_RO_20261104.md) |
| `docs/ops/RC6_A627_SCHEDULE_CAUSALITY_20261103.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/ops/RC6_A627_SCHEDULE_CAUSALITY_20261103.md) |
| `docs/ops/RC6_A627_SCHEDULE_P1_RECONCILIATION.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/ops/RC6_A627_SCHEDULE_P1_RECONCILIATION.md) |
| `docs/ops/RC6_ISSUE600_CONTROL_PLANE.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/ops/RC6_ISSUE600_CONTROL_PLANE.md) |
| `docs/ops/RC6_MARKET_WATCH_READONLY.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/ops/RC6_MARKET_WATCH_READONLY.md) |
| `docs/ops/RC6_SCHEDULE_WATCHDOG.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/ops/RC6_SCHEDULE_WATCHDOG.md) |
| `docs/rc6_a626_main_host_contracts_checkpoint.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/rc6_a626_main_host_contracts_checkpoint.md) |
| `docs/releases/RC6_712_INTEGRAL_AUDIT_20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/releases/RC6_712_INTEGRAL_AUDIT_20261022.md) |
| `docs/releases/RC6_717_RUNTIME_VISUAL_CLOSURE_20261022.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/releases/RC6_717_RUNTIME_VISUAL_CLOSURE_20261022.md) |
| `docs/releases/RC6_MONDAY_OPEN_AUDIT_20261101.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/releases/RC6_MONDAY_OPEN_AUDIT_20261101.md) |
| `docs/superpowers/plans/2026-11-08-porota-rootless-metadata.md` | [Git](https://github.com/mbalbo2023/Porota-trading/blob/9d3f89d2826b46d842a80b740bc24e272aa81e8d/docs/superpowers/plans/2026-11-08-porota-rootless-metadata.md) |
