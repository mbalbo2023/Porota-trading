# WS-CLOSE-PROGRAMMING-05 — matriz final de programación

Esta matriz conserva íntegramente #458 y su adenda A–N, #460 A–J y #462 con O–V. Es un contrato de aceptación de programación; no inicia una auditoría independiente. El estado final permanece sin resolver hasta que el owner publique la evidencia nativa del candidato exacto.

El JSON contiene **1,106 cláusulas/átomos, 976 unidades fuente, cero líneas de contenido omitidas**, 36 bundles de evidencia, 103 paths y 463 símbolos de tests. Cada ID estable resuelve su cita, sección y línea del body versionado, sus callers y pruebas reales.

[Matriz machine](WS_CLOSE_PROGRAMMING_05_FINAL_MATRIX.json). Las citas exactas aparecen una vez en `source_units`; `requirements[].source` conserva el contexto de los átomos. `coverage` resuelve `coverage_sets`, cuyos bundles resuelven archivos, callers, edges AST y tests. El resultado de cada test se liga al JUnit gobernado del único Predeploy final.

| Clase | Estado permitido | Alcance |
|---|---|---|
| I | `IMPLEMENTED_AND_TESTED` | Implementación y contrato de tests disponibles; satisfacción final exige gates exactos. |
| S | `IMPLEMENTED_SHADOW_AWAITING_OPEN_RUNTIME` | Código, callers y tests SHADOW existentes; medición real OPEN/OOS aún externa. |
| X | `NO_VERIFICADO_EXTERNAL_RUNTIME_ONLY` | Sólo resultados futuros de proveedor/rueda/OOS; código de medición/consumo ya referenciado. |

Las clases I/S no atribuyen GREEN a un HEAD o workflow desconocido. Los resultados nativos de los diez gates están sin asignar. Ninguna clase autoriza diferir desarrollo hasta después del auditor.

## Fuentes e inputs preservados

| Fuente/input | Identidad | Interpretación actual |
|---|---|---|
| #458 | actualizado 2026-10-03T22:38:08Z | Body completo y A–N, hash y citas conservados. |
| #460 | actualizado 2026-10-04T01:05:03Z | Body completo A–J; condición BLOCKED anterior superseded por #462. |
| #462 | Body 01:20:46Z, metadata 02:44:03Z; revalidado 02:51 UTC | Body SHA256 sin cambios, O–V obligatoria; único cierre permitido `READY_FOR_INDEPENDENT_AUDIT`. |
| Productivo | `da697c6e6c2274579f9e4a112fabc4327475dd35` | Input autorizado; nueva revalidación nativa antes del cierre final. |
| #461 frozen | `fa3a8d64f74fa45181808bd46d8a75131d5450cf` | Se conserva `BLOQUEADO_POR_453` como historia, superseded; no se editan sus documentos frozen. |
| #453 anterior | `d738db79a452c699b50aed00ac539e57e1cc3b04` | Superseded; sus cuatro fallos no son criterio de éxito actual. |
| #453 corregido | `2b36429013ec5e3accf4176cf3beaabfd5e33232` | Input real GREEN 2549/2549; Predeploy 37169254173 y release nativo preservados. |
| Base integrada | `ed519ad65a206f246a8a12faeef99fb56194acca` / tree `3c82ff8f4065976cbc327e829264cc71d80eca40` | Input de integración; no sustituye la identidad del próximo commit final. |

La identidad del candidato final se obtiene de `porota-frozen-candidate.json`, no de un ownSHA escrito dentro de este mismo commit. #456 incluye #454 y #457 incluye #450; no se reaplican como inputs independientes.

## Cumplimiento O–V y callers concretos

| Mandato | Implementación/caller | Evidencia de aceptación |
|---|---|---|
| O | `promotion.py` + policy OFF → benchmark recommendation; observer `_cycle_plan`, scalping `select_runtime_batch`, worker y `run_shadow` consumen aprobación/config exactas. | `CAPACITY_PROMOTION`; OFF 20/40, ON sintético, stale/missing/fingerprint/approval seguros; tests de caller canónico. |
| P | `orchestrator.py` y `live.py` asignan abiertas → SCALPING_HOT → strategy HOT/WARM → discovery; ranking propio en `families.py`. | `FINANCIAL_PRIORITY`; fairness no fuerza HOT ilíquido ni cuotas iguales; concentración se mide. |
| Q | Reader real → `RuntimePPIBudget` → `GlobalPPIBudget` SQLite compartido antes de cada endpoint/retry. | `GLOBAL_PPI_BUDGET`; reservas críticas, races multi-process, 429/session/408/5xx, backpressure, métricas y serialidad. |
| R | Native decision snapshots → `ShadowRuntime.tick` → `evaluate_runtime_entry_signals`; evaluadores pre-registrados por estrategia. | `ENTRY_SIGNAL_LAB` + `NATIVE_CAPTURE_FINAL`; mismo vector causal, labels posteriores/OOS, costos separados, ningún BUY automático. |
| S | `family_reports` + `strategy_route` emiten diez políticas incluso sin catálogo; handler especializado, selección propia o event-driven. | `FAMILY_POLICIES`; status/owner/cadence/source/deep eligibility/reason/entry_authority, sin fallback equity. |
| T | `resolve_field` en runtime y rutas legacy autorizadas `consolidate/reconcile`, collectors/caches existentes. | `SOURCE_AUTHORITY`; PPI primario, field provenance, conflictos, identidades/unidades ambiguas, native clock y SOURCE_UNAVAILABLE por path. |
| U | Worker → `evaluate_runtime_funnel` con plan, snapshots inmutables y eventos/fills reales spot/FUT. | `PROSPECTIVE_FUNNEL`; CATALOG_READY→NET_PNL, currencies/denominadores separados, partial/restart/net reconciliado y causales late/missed. |
| V | Todos los callers anteriores + caller end-to-end + guards FUT/spot + suite/artifact canónico. | `FINAL_RUNTIME_ACCEPTANCE`, `SHARED_FUT_SPOT_RISK`, diez gates nativos; no desarrollo diferido ni autoridad automática. |

Las pruebas nuevas `test_rc6_final_programming_runtime.py` y `test_rc6_final_shared_risk.py` cubren los cruces del runtime. Las 12 pruebas locales de riesgo compartido pasaron: BINDING DLR dos meses/tercer rechazo sin caja, configuración de cap, exposición y emergencia combinadas, carreras SQLite y pérdidas cerradas sin netear winners. Ese resultado intermedio no reemplaza el artifact final.

## Bundles y cobertura de todos los requisitos

| Bundle | Paths principales | Tests referenciados |
|---|---|---|
| `GOVERNANCE` | `AGENTS.md`, `ops/policy/porota-policy.yaml`, `ops/policy/test-policy.yaml` (+1 en JSON) | 10 símbolos |
| `SUITE_ARTIFACT` | `ops/policy/test-policy.yaml`, `.github/workflows/porota-predeploy-v2.yml`, `scripts/porota_build_deploy_bundle_v2.py` (+3 en JSON) | 30 símbolos |
| `HOST_CONTROL_PLANE` | `scripts/porota_apply_host_control_plane_v2.py`, `ops/policy/host-control-plane-reconciliation-v2.json`, `docs/audits/RC6_RESIDUAL_OPS_CLOSURE_2026-10-03.md` | 5 símbolos |
| `DEPLOY_GUARDS` | `.github/workflows/porota-deploy-v2-promote.yml`, `scripts/rc6_deploy_preopen_gate.py`, `scripts/rc6_deploy_failure_cleanup.py` | 65 símbolos |
| `RUNTIME_WIRING` | `bv_paper_runtime.py`, `rc6_shadow_runtime/worker.py`, `rc6_dynamic_universe/live.py` (+1 en JSON) | 38 símbolos |
| `PERSISTENCE` | `rc6_shadow_runtime/persistence.py`, `rc6_shadow_runtime/worker.py`, `rc6_dynamic_universe/runtime.py` (+1 en JSON) | 39 símbolos |
| `PREOPEN` | `rc6_dynamic_universe/tradeability.py`, `rc6_shadow_runtime/preopen.py`, `rc6_shadow_runtime/worker.py` | 55 símbolos |
| `TRADEABILITY` | `rc6_dynamic_universe/tradeability.py`, `rc6_dynamic_universe/common.py` | 23 símbolos |
| `DISCOVERY` | `rc6_dynamic_universe/tradeability.py`, `rc6_dynamic_universe/orchestrator.py`, `rc6_shadow_runtime/worker.py` | 61 símbolos |
| `SCALPING` | `cf_intraday_scalping.py`, `rc6_dynamic_universe/orchestrator.py`, `rc6_dynamic_universe/live.py` | 52 símbolos |
| `CAPACITY_BENCHMARK` | `rc6_dynamic_universe/benchmark.py`, `rc6_dynamic_universe/capacity.py`, `scripts/rc6_ppi_capacity_benchmark.py` (+2 en JSON) | 21 símbolos |
| `SHADOW_SHARED_ALLOCATION` | `rc6_dynamic_universe/orchestrator.py`, `rc6_dynamic_universe/live.py` | 45 símbolos |
| `SOURCES` | `rc6_dynamic_universe/sources.py`, `rc6_shadow_runtime/worker.py`, `rc6_shadow_runtime/families.py` (+3 en JSON) | 43 símbolos |
| `FAMILY_ROUTING` | `rc6_dynamic_universe/routing.py`, `rc6_shadow_runtime/families.py`, `rc6_shadow_runtime/worker.py` (+1 en JSON) | 35 símbolos |
| `CEDEAR_FEATURES` | `rc6_dynamic_universe/tradeability.py`, `rc6_shadow_runtime/families.py`, `rc6_dynamic_universe/routing.py` | 34 símbolos |
| `OPTIONS` | `rc6_dynamic_universe/routing.py`, `rc6_shadow_runtime/families.py` | 35 símbolos |
| `FIXED_INCOME` | `rc6_shadow_runtime/families.py`, `rc6_dynamic_universe/routing.py`, `fe_fixed_income_nominal_contract_rc6.py` | 11 símbolos |
| `TREASURY_FUNDS` | `rc6_shadow_runtime/families.py`, `rc6_dynamic_universe/routing.py`, `bt_caucion_paper.py` (+1 en JSON) | 28 símbolos |
| `COSTS_ECONOMICS` | `rc6_performance/costs.py`, `rc6_performance/shadow.py`, `rc6_dynamic_universe/economics.py` (+1 en JSON) | 65 símbolos |
| `EXIT_LAB` | `rc6_shadow_runtime/lab.py`, `rc6_performance/replay.py`, `rc6_performance/shadow.py` (+2 en JSON) | 30 símbolos |
| `NATIVE_SIGNAL_LINEAGE` | `be_paper_engine.py`, `rc6_performance/lineage.py`, `rc6_performance/capture.py` (+2 en JSON) | 39 símbolos |
| `TELEMETRY` | `rc6_dynamic_universe/orchestrator.py`, `rc6_shadow_runtime/stages.py`, `rc6_shadow_runtime/worker.py` (+1 en JSON) | 42 símbolos |
| `NO_CURVE_FITTING` | `rc6_dynamic_universe/tradeability.py`, `rc6_shadow_runtime/lab.py`, `rc6_performance/shadow.py` (+2 en JSON) | 66 símbolos |
| `FUTURES` | `be_paper_engine.py`, `rc6_paper_family_lifecycle.py`, `rc6_ppi_future_contract_policy.py` (+3 en JSON) | 37 símbolos |
| `CAPACITY_PROMOTION` | `rc6_dynamic_universe/promotion.py`, `ops/policy/rc6-dynamic-capacity-v1.json`, `scripts/rc6_ppi_capacity_benchmark.py` (+5 en JSON) | 17 símbolos |
| `FINANCIAL_PRIORITY` | `rc6_dynamic_universe/orchestrator.py`, `rc6_dynamic_universe/live.py`, `rc6_dynamic_universe/routing.py` (+1 en JSON) | 63 símbolos |
| `GLOBAL_PPI_BUDGET` | `rc6_ppi_global_budget.py`, `bd_ppi_readonly_guard.py`, `rc6_dynamic_universe/promotion.py` (+3 en JSON) | 32 símbolos |
| `ENTRY_SIGNAL_LAB` | `rc6_shadow_runtime/entry_signals.py`, `rc6_shadow_runtime/worker.py`, `rc6_performance/shadow.py` (+3 en JSON) | 21 símbolos |
| `FAMILY_POLICIES` | `rc6_dynamic_universe/routing.py`, `rc6_shadow_runtime/families.py`, `rc6_shadow_runtime/worker.py` (+3 en JSON) | 47 símbolos |
| `SOURCE_AUTHORITY` | `rc6_shadow_runtime/source_authority.py`, `rc6_shadow_runtime/families.py`, `rc6_shadow_runtime/worker.py` (+3 en JSON) | 50 símbolos |
| `PROSPECTIVE_FUNNEL` | `rc6_shadow_runtime/funnel.py`, `rc6_shadow_runtime/entry_signals.py`, `rc6_shadow_runtime/worker.py` (+4 en JSON) | 31 símbolos |
| `NATIVE_CAPTURE_FINAL` | `be_paper_engine.py`, `cf_intraday_scalping.py`, `rc6_performance/capture.py` (+2 en JSON) | 25 símbolos |
| `SHARED_FUT_SPOT_RISK` | `be_paper_engine.py`, `dh_paper_dynamic_risk_gate_hf6.py`, `de_concurrent_risk_capacity_hf6.py` (+3 en JSON) | 38 símbolos |
| `FINAL_RUNTIME_ACCEPTANCE` | `be_paper_engine.py`, `rc6_shadow_runtime/worker.py`, `rc6_dynamic_universe/promotion.py` (+3 en JSON) | 18 símbolos |
| `SCALPING_SNAPSHOT_FIX_FORWARD` | `cf_intraday_scalping.py`, `bd_ppi_readonly_guard.py` | 14 símbolos |
| `PPI_BUDGET_AUXILIARY_FIX_FORWARD` | `rc6_ppi_global_budget.py`, `bd_ppi_readonly_guard.py` | 7 símbolos |

Cada cláusula de la matriz machine tiene al menos un bundle con archivos/callers/tests reales. El validator preserva los 1106 IDs originales, compara las 976 citas contra los bodies hasheados, resuelve todos los paths/símbolos y confirma edges AST de los diez bundles nuevos. La comprobación semántica de aceptación requiere sus pruebas del JUnit final.

## Gates finales sin autorreferencia

El único artifact autorizado proviene del workflow `.github/workflows/porota-predeploy-v2.yml` del head exacto. Sus miembros `porota-frozen-candidate.json`, `porota-governed-tests.json`, `porota-governed-tests.xml`, collection/exclusions, manifests, imagen y evidencia runtime se resuelven por paths relativos únicos. El prefijo de packaging (`tmp/` cuando corresponde) lo confirma el listado nativo del artifact, sin cambiar los nombres semánticos.

| Gate | Evidencia que debe resolver el owner |
|---|---|---|
| `PRODUCT_HEAD` | Native current deploy/product ref equals authorized product input; retrieval time recorded in final PR attestation. |
| `INPUTS_TREE` | One candidate from product includes exact #461 + corrected #453 and all O-V changes with complete path/provenance list; #454/#450 not reapplied. |
| `GOVERNED_SUITE` | Exact final head repository-root discovery: status=GREEN, exit_code=0, discovered=executed>0; failures=errors=skipped=xfail=0; only pre-existing versioned exclusion; JUnit agrees with JSON and collection. |
| `PREDEPLOY_V2` | Native canonical porota-predeploy-v2 workflow run and every required job conclude success at PR head_sha equal FROZEN.candidate_sha. |
| `FROZEN_ARTIFACT` | Native artifact ID/digest associated with the exact run; downloaded digest verified, build_once=true; frozen tree/source/bundle hashes match and exact image/tar/config pass integrity/import/compile/runtime/PAPER/secret/dependency gates without rebuild. |
| `WRITE_OWNER_RELEASE` | Corrected #453 WRITE_OWNER released in native #446/#453 comments before integration; final #462 owner releases all paths after exact final gates. |
| `NO_DEPLOY_OWNER` | Native final owner declaration DEPLOY_OWNER NOT_ACQUIRED; no independent audit, merge, deploy, SSH, provider/production DB/host or PPI Watch mutation. |
| `PAPER_PPIWATCH` | Exact source/artifact tests and native path/provenance attest PRODUCTION_PAPER/SIMULATION, real_orders_sent=0, real routes NOT_CALLED, PPI Watch untouched. |
| `CALLER_COMPLETENESS` | All new code has real canonical runtime/workflow callers; final governed JUnit includes every mapped test; all programmed O-V contracts and cross-acceptance cases pass, no known development task remains. |
| `FINAL_PR_MATRIX` | One final PR freezes this matrix; source coverage and reference validation pass; native PR/head/tree/run/artifact attestation resolves every gate. Readiness may then be READY_FOR_INDEPENDENT_AUDIT. |

Una attestation machine en el PR/comment nativo después de GREEN relacionará PR/head/tree, workflow/run/jobs, artifact ID/digest descargado, frozen/image/tar/config hashes, manifest/JUnit/collection y hashes de estos documentos. También enlazará release final de WRITE_OWNER y declarará DEPLOY_OWNER NOT_ACQUIRED. Así se puede congelar el commit una sola vez y publicar evidencia posterior sin editarlo para introducir su propio SHA.

El predicate exige `head_sha = run_head_sha = frozen.candidate_sha`, tree/manifest coherentes, discovered=executed>0, failures/errors/skipped/xfail=0, exit_code=0, exclusión gobernada previa sin añadidos, build-once y exact image GREEN. Hasta resolver esas condiciones el campo `readiness_state` permanece null.

## Resultados futuros externos

Capacidad y latencia/freshness PPI OPEN, cobertura/missed discovery reales, densidad Scalping, calibración/edge/rentabilidad OOS y CEDEAR/IV/Greeks externos no disponibles quedan X. Sus consumidores, mediciones, provenance y estados unknown ya tienen paths y tests. No se inicia auditoría, merge, deploy, SSH, provider calls, DB/host productivos ni PPI Watch.

## Regresión global corregida antes de congelar

La primera suite global ejecutó 3005 casos: 2991 PASS y 14 fallos, sin errores ni skips. Ese resultado RED se conserva como evidencia de RCA y no cumple el criterio de éxito.

| Fallos | Causa | Fix / guard existente preservado |
|---|---|---|
| 12 | Store standalone de Scalping sin `decision_evidence_snapshots` | `init_schema` crea la misma tabla/index canónicos de #456 antes del loop; sin DDL en evaluaciones. |
| 1 | Mock legacy de budget truthy inducía acceso a `Store.open_positions` | Comportamiento opcional budget/read_scope exige `budget_enabled is True`; OFF y mocks mantienen el flujo previo. |
| 1 | Contrato BASELINE/RECHECK de cursor | Assignment original conservado; override APPROVED separado. |

Sólo cambió `cf_intraday_scalping.py`; ninguno de los 14 tests anteriores fue editado, debilitado o excluido. El focal posterior pasó **220/220**, failures/errors/skipped/xfail=0, con SHA de JUnit y refs de los 14 guards en `fix_forward_acceptance_history` y `SCALPING_SNAPSHOT_FIX_FORWARD`. La suite consolidada posterior se resuelve exclusivamente por `ARTIFACT_SUITE` del Predeploy canónico del head/tree congelados y la attestation nativa; no se inventa un resultado local desconocido.

## Segunda regresión global y concurrencia SQLite corregidas

La segunda suite conservó los 3005 casos: 3004 PASS y un fallo, failures=1/errors=skipped=xfail=0. SQLite eliminaba su journal entre `exists()` y `stat()` durante inicialización concurrente. El resultado RED, su JUnit y las iteraciones de stress v1 (2/3 GREEN) y v2 (1/2 GREEN) quedan preservados en `budget_auxiliary_fix_forward_history`; no satisfacen el cierre.

`_check_one` lee `lstat` una vez y `_check_path` reutiliza esos mismos metadatos para cuota. Sólo `FileNotFoundError` admite ausencia transitoria; alias, paths protegidos, cuota y errores EACCES/EIO siguen rechazados. El constructor reutiliza el bootstrap completo sin nuevas escrituras de schema ni DDL durante restart. Las iteraciones fallidas también expusieron upgrades de lock por page cap antes de admitir escritura: `acquire/start/finish/report_error` llaman `_begin_write`, que ejecuta `BEGIN IMMEDIATE` y después `_limit_pages`, con espera SQLite acotada a 50 ms. Las llamadas de proveedor no mantienen esa transacción.

El guard original `test_two_processes_cannot_claim_the_same_full_budget` mantiene exactamente su AST y assertions. Quince casos nuevos comprueban desaparición de auxiliares, alias/cuota, errores de metadata, restart bajo un writer real y admisión frente a otro writer cambiante. El focal posterior pasó **67/67**, sin failures/errors/skipped/xfail; v3 pasó **20/20** ejecuciones pytest aisladas con dos procesos reales cada una. Cada digest y JUnit de las 25 ejecuciones v1/v2/v3 fue validado. Los hooks/callers/tests y hashes finales están en `PPI_BUDGET_AUXILIARY_FIX_FORWARD`. La suite completa se descubre automáticamente y su resultado final se exige por `ARTIFACT_SUITE` del head exacto; ninguno de estos focals atribuye un GREEN consolidado desconocido.
