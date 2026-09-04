# RC4 — Inventario de scripts

- Scripts clasificados: **38**
- `SCRIPT_REVIEW_REQUIRED`: **0**

| Script | Clasificación | Systemd | Decisión / razón |
|---|---|---|---|
| `deploy/systemd/porota-contract-evidence-dashboard-hf6.service` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-contract-evidence-hf6.service` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-contract-evidence-hf6.timer` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-introspeccion-hf5.service` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-introspeccion-hf5.timer` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-introspection-publish.service` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-introspection-publish.timer` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-log-export-hf6.service` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-log-export-hf6.timer` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-scheduler-export-hf6.service` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `deploy/systemd/porota-scheduler-export-hf6.timer` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `scripts/build_v17_ledger_preflight.py` | BUILD_OR_DEPLOY | — | Herramienta explícita de build/deploy; no corre sola. |
| `scripts/build_v17_ppi_probe.py` | BUILD_OR_DEPLOY | — | Herramienta explícita de build/deploy; no corre sola. |
| `scripts/build_v17_preinstall.py` | BUILD_OR_DEPLOY | — | Herramienta explícita de build/deploy; no corre sola. |
| `scripts/porota-introspeccion-hf5-manual.sh` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `scripts/porota_apply_daily_results_responsive_hf6.sh` | LEGACY_PATCH_OR_MIGRATION | — | Parche/migración histórica; no es camino canónico RC4. |
| `scripts/porota_apply_dashboard_ux_logs_hf6.sh` | LEGACY_PATCH_OR_MIGRATION | — | Parche/migración histórica; no es camino canónico RC4. |
| `scripts/porota_apply_dynamic_risk_gate_hf6.sh` | LEGACY_PATCH_OR_MIGRATION | — | Parche/migración histórica; no es camino canónico RC4. |
| `scripts/porota_apply_history_dashboard_metrics_hf6.sh` | LEGACY_PATCH_OR_MIGRATION | — | Parche/migración histórica; no es camino canónico RC4. |
| `scripts/porota_apply_history_source_rank_hf6.sh` | LEGACY_PATCH_OR_MIGRATION | — | Parche/migración histórica; no es camino canónico RC4. |
| `scripts/porota_apply_scheduler_dashboard_hf6.sh` | LEGACY_PATCH_OR_MIGRATION | — | Parche/migración histórica; no es camino canónico RC4. |
| `scripts/porota_contract_evidence_dashboard_overlay_hf6.sh` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `scripts/porota_contract_evidence_hf6_runtime.sh` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `scripts/porota_contract_evidence_trusted_rc4.sh` | SCHEDULER_ACTIVE_RC4 | systemd/porota-contract-evidence-rc4.service | Referenciado por systemd/porota-contract-evidence-rc4.service |
| `scripts/porota_contract_patch_finalize_hf6.sh` | LEGACY_PATCH_OR_MIGRATION | — | Parche/migración histórica; no es camino canónico RC4. |
| `scripts/porota_export_runtime_logs_hf6.sh` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `scripts/porota_export_scheduler_state_hf6.sh` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `scripts/porota_ppi_authenticated_all_families_hf6.sh` | LEGACY_MANUAL_AUTH_SCRAPER | — | Scraper HF6 legacy manual; RC4 scheduler no lo referencia. |
| `scripts/porota_publish_introspection_github_hf6.sh` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `scripts/porota_retire_legacy_preopen_hf6.sh` | OPS_MANUAL_CLASSIFIED | — | Script manual conservado; no hay referencia systemd automática. |
| `scripts/v17_diagnostico_instrumentos.py` | OPS_DIAGNOSTIC | — | Diagnóstico/chequeo manual o CI; no scheduler. |
| `scripts/v17_host_general_backup.py` | OPS_CLI_OR_WORKER | — | Worker/CLI explícito; su automatización depende de systemd/versionado. |
| `scripts/v17_ledger_preflight.py` | OPS_DIAGNOSTIC | — | Diagnóstico/chequeo manual o CI; no scheduler. |
| `scripts/v17_ppi_public_probe.py` | OPS_DIAGNOSTIC | — | Diagnóstico/chequeo manual o CI; no scheduler. |
| `scripts/v17_preinstall_readonly.py` | BUILD_OR_DEPLOY | — | Herramienta explícita de build/deploy; no corre sola. |
| `scripts/v17_prepare_directory.py` | OPS_MANUAL_CLASSIFIED | — | Script manual conservado; no hay referencia systemd automática. |
| `scripts/v17_rc3_hf2_deploy.py` | BUILD_OR_DEPLOY | — | Herramienta explícita de build/deploy; no corre sola. |
| `scripts/v17_rc3_hf6_deploy.py` | BUILD_OR_DEPLOY | — | Herramienta explícita de build/deploy; no corre sola. |

## Política

- Ningún script sin referencia systemd se interpreta automáticamente como huérfano.
- Patchers/migraciones históricas se conservan por trazabilidad pero no forman el camino canónico RC4.
- El scraper autenticado legacy no forma parte del scheduler RC4.
- Antes de deploy, los servicios RC4 deben referenciar únicamente archivos presentes en este inventario.
