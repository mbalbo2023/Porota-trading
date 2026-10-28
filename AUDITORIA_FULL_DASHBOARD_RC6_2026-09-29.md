# Auditoría full del dashboard RC6 — 2026-09-29

## Dictamen

La contradicción conocida entre runtime y dashboard quedó eliminada en el código del dashboard. La proyección de UI usa una autoridad única por concepto, no deriva readiness desde históricos ni desde caches IOL, y no restringe el universo visible a Acciones/CEDEARs.

Esta entrega es exclusivamente read-only/dashboard. No modifica el motor financiero, no llama rutas reales, no toca PPI Watch, no escribe productiva y no hace deploy.

## Baseline y método

- Base revalidada: `deploy/rc6-pr69-isolated-20260915` @ `7fe4f905d34ca573c3a0bc414f3c258ad6b0bba2`.
- Inventario ejecutable: `scripts/rc6_dashboard_route_inventory.py` importa la aplicación real y enumera `app.routes`.
- Resultado: 55 registros de ruta, 53 paths únicos, cero rutas sin clasificación.
- El inventario completo, métodos HTTP, endpoint, superficie, datasets, autoridad, `as_of`, stale/unknown, navegación, paginación, accesibilidad y locale están en `DASHBOARD_TRUTH_MATRIX_RC6.json`.
- `/panel` y `/cierre` no están registrados en la aplicación de este baseline; por lo tanto no se inventaron superficies. Sus funciones visibles existentes están cubiertas por `/`, `/vivo`, `/reportes` y las rutas declaradas en el inventario.
- Smoke HTTP local autenticado: 200 en las 16 superficies/API core (`/`, En vivo, Trading, familia, Universo, Instrumentos, Validación, Análisis, Aprendizaje, Scalping, Riesgo, Históricos, Reportes, Sistema, Salud y `/api/dashboard/truth`).

## Matriz canónica

| Concepto | Autoridad única | Regla de presentación |
|---|---|---|
| Runtime / modo / seguridad | `observer_state` | `real_orders_sent` y modo no se recalculan en cada página |
| Catálogo | `financial_instrument_catalog` | inventario, no permiso operativo |
| RUNTIME_READY | `candidate_identity_v2` | `can_simulate=1 AND status=AVAILABLE` sobre la identidad completa |
| Contrato / provenance | `contract_evidence_v2_current` | Evidence v2 no redefine READY |
| Histórico | `history_canonical_v2` / candle store | sólo histórico/selector/análisis; nunca readiness |
| STRATEGY_ELIGIBLE | `trade_gate_evaluations` | event-driven; no existe un censo estático inventado |
| IOL actual | cache por sección + source state + LKG | sólo `LIVE`, `CACHE_FRESH`, `CACHE_STALE`, `SOURCE_UNAVAILABLE` |
| Aprendizaje histórico | `decision_evidence_latest` | snapshot al momento de la decisión, separado de salud actual |
| Scalping | worker + contratos + candidatos + fills | modo, scan, contrato, blockers, costo/edge, EOD/MaxHold y timestamps reales |
| Caución colocadora | ledger + allocator + treasury | source, policy, ventana, tasa, mínimo, caja, reserva, budget, HOLD y liquidez |
| Timers | scheduler/systemd evidence | stale/unknown explícito |

## ERROR → RCA → FIX → GUARD → TEST → EVIDENCIA

### 1. Readiness falso en `/analisis`

- ERROR: histórico/cache legacy mostraba `0/N` aunque runtime tenía 5.558 READY.
- RCA: `history_canonical_v2` y comparación IOL se usaban como autoridad de readiness.
- FIX: selector e informe siguen históricos; la matriz READY ahora se obtiene de `candidate_identity_v2` y el contrato de Evidence v2.
- GUARD: la proyección canónica no consulta `production_history`, `catalog_family_coverage` ni `candidate_universe` para readiness.
- TEST: `test_projection_uses_only_the_canonical_authorities_for_counts` y `test_cross_route_readiness_numbers_cannot_diverge`.
- EVIDENCIA: total canónico de fixture 5.558 y familias 126 / 683 / 1.674 / 31 / 2.041 / 1.003 idénticos entre Análisis, Instrumentos y Trading.

### 2. Alcance hardcodeado a dos familias

- ERROR: Universo, En vivo y Trading excluían o rotulaban como deshabilitadas las demás familias.
- RCA: filtros SQL y textos pre-WS14, más una lectura de `ready_paper_count` inexistente.
- FIX: todas las familias provienen de la proyección canónica; decisiones/gates usan identidades completas READY.
- GUARD: test negativo para filtros Acciones/CEDEARs y frases legacy.
- TEST: `test_live_and_motor_use_canonical_readiness_without_two_family_filter`, `test_universe_source_uses_candidate_identity_v2_and_all_families`.
- EVIDENCIA: `Universo operativo — todas las familias`, sin `candidate_universe` en superficies activas.

### 3. Aprendizaje histórico presentado como salud actual

- ERROR: `IOL: UNAVAILABLE` de una decisión pasada parecía describir el presente.
- RCA: dos tiempos semánticos compartían el mismo bloque.
- FIX: “Fuentes al momento de la decisión” permanece histórico y se agregó “Salud actual de fuentes”.
- GUARD/TEST: `test_learning_keeps_decision_time_evidence_separate_from_current_health`.
- EVIDENCIA: los estados actuales conservan source-path y LKG sin reescribir decisiones.

### 4. IOL sin semántica de cache

- ERROR: ausencia de source podía convertirse en cero o en un falso estado live.
- RCA: vocabularios de proveedor/cache no estaban normalizados por sección.
- FIX: normalización cerrada a cuatro estados, preservando `raw_source_state` y `last_known_good_at`.
- GUARD/TEST: `test_iol_live_cache_fresh_cache_stale_and_unavailable_are_distinct`.
- EVIDENCIA: source unavailable con LKG fresco se ve `CACHE_FRESH`, nunca `LIVE` ni cero inventado.

### 5. Scalping incompleto

- ERROR: faltaban semántica humana inequívoca, universo READY vs elegibilidad, scan, blockers, costo/edge y estado de salida.
- RCA: la pantalla mezclaba contadores locales y no proyectaba todas las evidencias persistidas.
- FIX: muestra modo, RUNTIME_READY, elegibilidad event-driven, escaneados, contratos, candidatos, fills, top blockers, costo/edge, timestamps, supervisor EOD y Max Hold.
- GUARD/TEST: `test_scalping_modes_and_caucion_placing_semantics_are_explicit` más suite de scalping existente.
- EVIDENCIA: `ACTIVE_OBSERVE: NO ABRE POSICIONES` y `ACTIVE_PAPER: fills exclusivamente simulados`.

### 6. Caución legacy

- ERROR: el dashboard trataba garantía como requisito genérico y ocultaba el estado real de la colocadora.
- RCA: bloque piloto anterior al ledger/allocator/treasury PAPER.
- FIX: panel de colocadora PAPER basado en persistencia real; campos ausentes son `NO_VERIFICADO`.
- GUARD/TEST: guard semántico de caución y suite ledger/treasury/cash sweep.
- EVIDENCIA: source, ventana, tasa, mínimo, policy, caja, reserva, sweep budget, candidato, razón HOLD, intento, liquidez y vencimiento visibles.

### 7. Locale y accesibilidad

- ERROR: formatos visuales estadounidenses y listas extensas perjudicaban lectura/voz.
- RCA: formateo disperso y default global de 20 filas.
- FIX: números humanos es-AR; default transversal de 10 filas; Mostrar más/menos, ARIA, foco preservado, texto además de color, códigos técnicos relegados a detalle/title.
- GUARD/TEST: tests de locale, Voice Access, tablas progresivas, labels, foco y smoke de 30 decisiones/25 cierres.
- EVIDENCIA: `1.234.567,89`, `ARS $ 1.234.567,89`, `PAGE_SIZE=10`.

## Validación

- `python3 -m py_compile ...`: GREEN.
- `git diff --check`: GREEN.
- Tests focales dashboard/scalping/caución: GREEN.
- Suite completa: 2.263 tests; 2.259 passed, 4 skipped, 0 failed, 0 errors.
- Único warning: deprecación preexistente de `datetime.utcnow()` en `rc6_report_retention.py`; no es inconsistencia de dashboard ni cambio de scope.

## Límites de evidencia

No se ejecutó deploy ni se afirmó estado de runtime productivo posterior al cambio. Los valores 5.558 y por familia son el baseline canónico recuperado; los tests demuestran igualdad cross-route sin inventar un nuevo snapshot productivo. La validación runtime/deploy corresponde al integrador autorizado posterior.

## Inconsistencias conocidas al cierre

Ninguna inconsistencia conocida dentro del scope WS-DASH-04. Las rutas no registradas (`/panel`, `/cierre`) están declaradas como ausentes, no simuladas.
