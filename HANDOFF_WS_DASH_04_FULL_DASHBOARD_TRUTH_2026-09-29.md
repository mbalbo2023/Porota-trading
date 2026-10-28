# Handoff WS-DASH-04 — Full Dashboard Truth

## Entrega

- PR: https://github.com/mbalbo2023/Porota-trading/pull/370
- Rama: `work/ws-dash-04-full-dashboard-truth-20260929`
- Base: `deploy/rc6-pr69-isolated-20260915` @ `7fe4f905d34ca573c3a0bc414f3c258ad6b0bba2`
- Commit de implementación publicado: `f66a490d235d5c6d43547218beaebeed196b0a53`
- Scope: dashboard/read-only exclusivamente.

El head final es el head visible del PR; este archivo de handoff se publica como un commit documental posterior y no modifica código ejecutable.

## Resultado funcional

- Todas las rutas registradas quedaron inventariadas programáticamente: 55 registros / 53 paths.
- Readiness usa sólo `candidate_identity_v2`.
- Catálogo usa `financial_instrument_catalog`.
- Contrato usa `contract_evidence_v2_current`.
- Histórico queda aislado como histórico.
- Aprendizaje histórico y salud actual de fuentes se muestran en bloques distintos.
- IOL distingue `LIVE`, `CACHE_FRESH`, `CACHE_STALE`, `SOURCE_UNAVAILABLE`, con source-path y LKG.
- Universo, En vivo, Análisis, Instrumentos y Trading dejaron de aplicar el recorte legacy Acciones/CEDEARs.
- Scalping muestra modo, universo READY, elegibilidad event-driven, scan, contratos, candidatos, fills, blockers, costo/edge, EOD/MaxHold y timestamps.
- Caución colocadora muestra estado real persistido sin exigir garantía genérica.
- Locale es-AR y Voice Access: máximo 10 filas visibles, Mostrar más/menos, ARIA, foco y estados con texto.

## Evidencia

- Suite completa local: 2.263 tests; 2.259 passed, 4 skipped, 0 failed, 0 errors.
- Smoke HTTP 200: 16 superficies/API core.
- `py_compile`: GREEN.
- `git diff --check`: GREEN.
- Secret scan: GREEN; 889 archivos; 0 findings.
- Auditoría: `AUDITORIA_FULL_DASHBOARD_RC6_2026-09-29.md`.
- Matriz ejecutable: `DASHBOARD_TRUTH_MATRIX_RC6.json`.
- Los checks GitHub definitivos se asocian al head del PR y son requisito previo para marcarlo ready.

## Límites y seguridad

- No deploy ejecutado.
- No merge ejecutado.
- Motor financiero/runtime behavior: no tocado.
- PPI Watch: no tocado.
- Productiva: sin escrituras.
- Rutas reales: no llamadas.

## Estado de errores

- P0 conocidos dentro del scope: 0.
- P1 conocidos dentro del scope: 0.
- Contradicciones conocidas de verdad del dashboard: 0.
- Warnings convertidos en GREEN sin evidencia: 0.
- Datos inventados: 0.

## Próximo actor

Integración puede revisar y, si corresponde, integrar el PR. La validación runtime y cualquier deploy pertenecen al workstream integrador autorizado; este workstream no los ejecuta.

## Ownership

`WRITE_OWNER=RELEASED` al quedar el PR ready con checks GREEN.
