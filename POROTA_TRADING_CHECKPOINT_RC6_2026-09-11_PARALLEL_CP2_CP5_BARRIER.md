# POROTA TRADING RC6 — CHECKPOINT PARALLEL CP2–CP5 BARRIER — 2026-09-11

## Scope
Checkpoint canónico incremental posterior a la barrera paralela CP2–CP5. No reemplaza checkpoints anteriores; los continúa.

## Rama / HEAD auditado
- Rama: `fix/rc6-w10-sector-map-binding-20260910`
- HEAD auditado antes de este checkpoint: `80f6fe69b95431349ddfdbb4a123f232821331fd`
- Runtime productivo PAPER observado: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
- No se realizó deploy, restart ni mutación de runtime durante esta barrera.

## Seguridad innegociable
- `MODE=PRODUCTION_PAPER`.
- `REAL_ORDER_CAPABILITY=BLOCKED`.
- `real_orders_sent=0` como invariante de runtime.
- Rutas de órdenes reales: `NOT_CALLED` en la validación focalizada.
- Feed genérico de noticias: permanece OFF.
- GDELT/event risk: solamente `SHADOW_ONLY`.

## GitHub Actions — barrera paralela
Run `34555422350` — `RC6 CP2-CP5 Parallel Next Proof 2026-09-11`: `SUCCESS`.

Jobs 3/3 `SUCCESS`:
1. `cp2_cp5_focused_tests`.
2. `cp3_exact_importer_runtime`.
3. `cp4_exact_a3_identity`.

Además, sobre el mismo HEAD:
- Run `34555422270` — `RC6 W10 Forward Fix Validation 2026-09-10`: `SUCCESS`.
- Run `34555422304` — `RC6 Post-W10 Next Failure RCA 2026-09-10`: `SUCCESS`.

## CP2 — UX / operational validation
Estado de esta barrera: `SOURCE_AND_FOCUSED_TESTS_GREEN`.

La suite focalizada validó en conjunto:
- `tests/test_rc6_validation_dashboard_operational_wiring.py`
- `tests/test_rc6_table_headers_visible_sticky.py`
- `tests/test_rc6_risk_gdelt_dashboard.py`
- `tests/test_rc6_gdelt_event_risk_job.py`

Resultado: `100%`, `CP2_CP5_FOCUSED_TESTS=GREEN`, `REAL_ORDER_ROUTES=NOT_CALLED`.

No se declara CP2 desplegado todavía: falta materializar la siguiente barrera integrada junto con CP3/CP4 antes de tocar runtime.

## CP5 — GDELT / UX
Estado de esta barrera: `SOURCE_AND_FOCUSED_TESTS_GREEN`.

Se conserva la política aprobada:
- GDELT lee/escribe su store dedicado de event-risk.
- Consumo de dashboard local/read-only respecto de esa evidencia.
- Sin feed genérico de noticias.
- `SHADOW_ONLY`; no habilita decisiones ni órdenes.

## CP3 — Contract Evidence
Estado: `ROOT_CAUSE_LOCALIZED`, todavía bloqueante para 18/18.

Prueba exacta de runtime, sin mutaciones:
- `contract_evidence_v2_runs=106`
- `contract_evidence_v2_current=519`
- `contract_evidence_v2_snapshots=924`
- último run `CONTRACT_EVIDENCE_STATIC` observado: `2026-09-10T21:34:24Z`, `records=0`, `changed=0`, `state=AMARILLO`.
- máximos `observed_at` de las familias canónicas permanecen alrededor de `2026-09-09T20:06Z`.

Hallazgo causal comprobado:
- `/usr/local/lib/porota-contract-evidence-rc6/rc6_contract_capture_importer.py` sólo itera `(raw.get("endpoints") or {}).items()` para materializar snapshots.
- Un capture STATIC puede terminar autenticado y aceptado pero con `records=0` si no trae hechos normalizados en `endpoints`.
- Por lo tanto el problema ya no se trata como “login/auth desconocido”; el bloqueante está en la ruta de materialización capture → endpoints normalizados → Contract Evidence canonical.

Siguiente acción CP3: inspección exacta del `rc6_trusted_browser_contract_collector.py` y del shape de captures recientes; corregir solamente con evidencia explícita, sin inferir valores contractuales ni económicos desde rutas/DOM si no están presentes.

## CP4 — A3 historical FUTUROS
Estado: `ROOT_CAUSE_LOCALIZED`, todavía bloqueante para 18/18.

Prueba exacta de runtime, sin mutaciones:
- `a3_history_ingest_state_rc6` tiene `40` filas `instrument_type=FUTUROS`.
- Contract Evidence canónico contiene símbolos exactos ROFEX tales como `DLR/SEP26`, `DLR/OCT26`, `DLR/NOV26`, etc.
- `ppi_intraday_contract_state` también contiene esos símbolos Porota y varios muestran fuente/volumen intradía confirmado.
- Sin embargo A3 historical continúa `ALIGNMENT_UNVERIFIED` / `CEM_SYMBOL_FAMILY_NOT_EXACT` para esas filas.

El artifact `a3_catalog_alignment_latest.json` sigue reportando `exact_overlap_total=0`, `closing_price_requests=0`, `execution_allowed=false`, pero fue medido el `2026-09-06`; es un artifact stale frente al estado actual de CE/A3.

En source ya existe `fd_a3_identity_mapper_rc6.py` con mapping determinístico y fail-closed para contratos simples DLR (`DLR/SEP26` ↔ forma compacta PPI). La ruta de adquisición histórica actual en `ew_a3_history_rc6.py` todavía no consume ese mapper. Próxima acción CP4: wiring mínimo, determinístico y testeado; variantes/spreads no demostrados deben seguir fail-closed.

## Estado global
- CP2: AMARILLO → source/focused tests GREEN; pendiente barrera integrada/deploy.
- CP3: AMARILLO — root cause localizado; fix pendiente.
- CP4: AMARILLO — root cause localizado; wiring/test pendiente.
- CP5: AMARILLO → source/focused tests GREEN; pendiente barrera integrada/deploy.
- W10 forward validation: GREEN.
- `GLOBAL_RC6=YELLOW`.
- `GO_18_OF_18=NO`.

## Estrategia de continuación — paralela
Track A / CP3: collector + capture shape → fix de materialización sólo si los datos están explícitos → focused tests.

Track B / CP4: integrar mapper DLR simple en adquisición A3 → focused tests → conservar fail-closed para aliases/spreads no verificados.

Track C / CP2+CP5: mantener source congelado/verde y ejecutar regresión integrada en paralelo a A/B.

Barrera antes de deploy: todos los tracks green + suite integrada green + invariantes PAPER/0 órdenes + checkpoint. Sólo entonces preparar deploy transaccional y postflight; no hay deploy ciego.
