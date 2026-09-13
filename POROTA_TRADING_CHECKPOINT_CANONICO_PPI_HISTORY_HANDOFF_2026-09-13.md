# POROTA TRADING — CHECKPOINT CANÓNICO PPI HISTORY HANDOFF — 2026-09-13

## Propósito
Este es el checkpoint canónico para continuar en otro chat sin perder contexto. Si contradice checkpoints anteriores, este manda para el estado operativo del 13-sep-2026. Debe leerse junto con `POROTA_TRADING_PROMPT_CONTINUIDAD_PPI_HISTORY_HANDOFF_2026-09-13.md`, `POROTA_TRADING_CHECKPOINT_PPI_WEB_RESIDUAL_HANDOFF_2026-09-13.md`, `POROTA_TRADING_CHECKPOINT_DATA_LIFECYCLE_INGESTION_PENDING_2026-09-13.md`, `POROTA_TRADING_CHECKPOINT_CONTRACT_COVERAGE_PENDING_2026-09-13.md` y `POROTA_TRADING_CHECKPOINT_PPI_HISTORY_SEMANTICS_2026-09-12.md`.

## Objetivo global
Completar histórico previous-365d para el universo canónico PPI, todas las familias posibles, con trazabilidad y sin síntesis. Orden de fuentes aprobado: PPI API -> PPI Web autenticado read-only sólo para residual -> IOL sólo para residual post-Web. Nunca dos historical writers simultáneos. Histórico no implica READY_PAPER.

## Rama / identidad
Repo `mbalbo2023/Porota-trading`.
Rama `ops/rc6-ppi-web-residual-ready-20260913`.
Run PPI API `PPI-HIST-20260912-001`.
Run PPI Web `PPI-WEB-RESIDUAL-20260913-001`.
Servicio PPI Web `porota-ppi-web-residual-rc6.service`.
Servicio PPI API que debe seguir inactivo `porota-ppi-fullfamily-history-rc6.service`.
Root durable `/opt/porota-ingest/ppi-web-residual`.

## Cierre PPI API
Universo exacto: 1960. PPI API quedó cerrado con 1960 tareas totales, 0 PENDING/RETRYABLE/RUNNING y servicio histórico inactivo. Safety validado `ok|PRODUCTION_PAPER|0|0|1960`. No volver a usar porcentajes viejos de avance API.
Residual congelado: 679 = HARD_PROVIDER_ERROR 22 + NO_PROVIDER_ROWS 360 + PARTIAL_VALID 281 + PROVIDER_INVALID 16.

## Manifest residual inmutable
Runnable PPI Web no-FCI: 642. FCI deferred: 37. Política FCI: `DEFERRED_PENDING_CHECKPOINT_NOT_DONE_EMPTY`.
Hashes congelados:
- runnable file `62d6280f1090e32f477917049166e5a942c755c36235f652264469f3dd895e9e`
- deferred file `6cdb5554c06e1a6514c65999391a7ab59624fd0f50bc4a885ddc2ffea928f99b`
- runnable rows `4a2abad6f5b6f8b2001b15fcb5da56ca5fdafb8267b4fe82095621082ea67342`
- deferred rows `bef43e86c43778e7df278c0a7d3728d7f9d3e7bfeabea4eee68279dedce37fad`
No regenerar ni editar manifest mientras el run siga en progreso.

## Stack PPI Web y seguridad
Reutilizar el stack existente; no inventar scraper nuevo. Trusted profile `/home/porotaadmin/porota-browser-lab/chrome-profile`, Python `/opt/porota-contract-evidence-venv/bin/python`, Chrome `/usr/bin/google-chrome-stable`, browser user `porotaadmin`, browser lock `/run/lock/porota-ppi-web-browser.lock`.
Auth ya fue probada como `AUTHENTICATED_TRUSTED_DEVICE`; no tratar login/Chrome lock como problema salvo evidencia nueva.
Read-only estricto: GET/HEAD/OPTIONS; cero órdenes reales; prohibido ejecutar operaciones o cambios de cuenta/seguridad y prohibido registrar material de autenticación sensible.

## Histórico PPI Web probado
Endpoint interno autenticado funcional `/api/Cotizaciones/Item/{itemId}/Historico/{plazo}`. El raw puede exceder 365d y debe filtrarse exactamente. Discovery real ya resolvió AVYC, TX28D, opciones y futuros; alias durable importante `MRCTO` canónico -> `MRCAC` PPI Web. No fuzzy matching silencioso.
Settlement: desde 27-may-2024 la liquidación normal BYMA pasó a T+1/24hs; CI sigue T+0. Las identidades históricas A-48HS son legacy y no prueban operabilidad actual. Usar `PlazosOperables` real; no universalizar IDs de plazo sin evidencia.

## Runner durable
`state.sqlite3` y `status.json` son fuente de verdad junto con systemd/journal. Estados por tarea: PENDING, RUNNING, DONE_VALID, DONE_PARTIAL, DONE_EMPTY, ERROR. Recupera huérfanas RUNNING a PENDING. PPI API válido tiene precedencia sobre PPI_WEB_HISTORY. No síntesis/interpolación/repair. FCI no entra al runnable. DONE_EMPTY sólo para provider-empty realmente resuelto.

## Incidentes y correcciones ya hechos
1. Start run `34743435197` falló antes de arrancar por permiso de lectura de `manifest_meta.json`. Fix commit `d86557ce371e2fde5e0f5b049527bb240e996ac8`: lectura runtime protegida vía sudo, sin relajar permisos.
2. Start run `34743578283`: safety y manifest verdes, service llegó active, pero workflow falló esperando status. Diagnóstico demostró que el problema real era collector.
3. Diagnose run `34743720432`: encontró reinicios por `PPI_WEB_COLLECT_FAILED:UNKNOWN`, state/status durables y safety correcto.
4. Causa práctica: root `batches/` no accesible al browser user. Hotfix commit `d1dd7d0c0c1cc4971639e515c16fce7b81f0c5c0`, run `34743873661`: owner del root de batches corregido, servicio reiniciado y exigencia de progreso real.
Resultado hotfix: `BATCHDIR_HOTFIX=GREEN`, `SCRAPER_REAL_PROGRESS=YES`.

## Último snapshot validado
Snapshot del run `34743873661` alrededor de `2026-09-13T06:55:36Z`:
- service active/running
- MainPID 1287913
- NRestarts 0 después del hotfix
- run status RUNNING
- total 642
- terminal 5
- DONE_PARTIAL 5
- PENDING 634
- RUNNING 3
- progress 0.78%
- current observado: `AL35C | BONOS | BYMA | A-24HS`
- heartbeat `2026-09-13T06:55:36.757204+00:00`
- safety post `ok|PRODUCTION_PAPER|0`
Este snapshot es histórico: el próximo chat debe consultar estado actual antes de modificar nada.

## Pendientes obligatorios / plan de acción
1. Monitorear el run PPI Web hasta terminalizar las 642 tareas. Reportar siempre RUN_STATUS, TOTAL, TERMINAL, PENDING, RUNNING, DONE_VALID, DONE_PARTIAL, DONE_EMPTY, ERROR, PROGRESS_PCT, CURRENT, HEARTBEAT, SERVICE, NRESTARTS, SAFETY y FCI_DEFERRED.
2. Diagnosticar cualquier ERROR por causa; retry sólo con causa conocida. Nunca borrar state.sqlite3 ni resetear a ciegas.
3. Al completar PPI Web, reconciliar canónico: PPI API válido no sobreescrito, provenance correcta, previous365d exacto, duplicados cero o explicados, sin síntesis.
4. Generar residual post-Web inmutable.
5. Ejecutar IOL únicamente para residual post-Web; no sobreescribir fuentes de mayor autoridad.
6. Resolver los 37 FCI/FCI Exterior: discovery estable, IDs/claseFCIId, histórico/frecuencia, NAV-cuota-rendimiento-cotización, reglas/minimos read-only. Nunca DONE_EMPTY por falta de discovery.
7. Completar validación semántica Web↔PPI API en una identidad actualmente operable 24HS/CI: comparar O/H/L/C/V en fechas solapadas y confirmar semántica del close; no usar legacy A-48HS como control principal.
8. Formalizar convivencia/migración legacy 48HS vs actual 24HS sin series artificiales duplicadas.
9. Cerrar cobertura contractual por familia: DISCOVERY, HISTORICO, CONTRACT_METADATA, OPERABILITY_RULES, READY_PAPER. Cauciones requiere plazos, tasa, moneda, vencimiento, mínimos, lote/step, identificador, histórico y elegibilidad.
10. Diseñar estrategia delta/incremental: watermark, idempotencia, dedupe, provenance, gaps/stale y refresh selectivo.
11. Definir rolling 365d: operacional vs raw/auditoría, prune vs archive, transaccional, preservando hashes/provenance/attempts y excepciones de lookback.
12. Revisar individualmente los 18 productores/timers suspendidos: propósito, fuente, frecuencia, universo, settlement, dedupe, stale/error, semántica, solapamiento, costo, calendario, delta/365d, restart/resume y observabilidad. No reactivar sólo por systemd verde.
13. Matriz de utilidad de fuentes PPI API/PPI Web/IOL/BYMA-A3/Data912/Yahoo/otras: autoridad, cobertura, calidad, latencia, contrato, estabilidad, redundancia y precedencia. Retirar/relegar fuentes que no aporten valor.
14. Mantener pendiente de doble calendario Argentina/EE.UU. para CEDEARs/acciones USA/ETF y evitar decisiones en feriados externos aplicables.
15. Validación final antes de restaurar timers: universo reconciliado, histórico consolidado, dupes/gaps/stale auditados, PRODUCTION_PAPER|0, cero órdenes reales, sin writers superpuestos, FCI resuelto o política explícita, contratos/operabilidad cerrados, source review y lifecycle/delta listos.
16. Reactivar productores uno por uno, con evidencia funcional útil.

## No hacer
No iniciar segundo writer; no volver a full PPI API; no tocar manifest congelado; no borrar state; no restaurar 18 timers todavía; no introducir IOL/Yahoo/Data912 antes del residual post-Web; no considerar histórico=READY_PAPER; no hacer operaciones reales.

## Estado consolidado al cerrar este chat
`PPI_API_CLOSEOUT=GREEN`
`PPI_WEB_AUTH=GREEN`
`PPI_WEB_PREFLIGHT=GREEN`
`MANIFEST_FREEZE=GREEN`
`BATCHDIR_HOTFIX=GREEN`
`SCRAPER_REAL_PROGRESS=YES`
`MASS_SCRAPING_STARTED=YES`
`FULL_HISTORY_COMPLETE=NO`
`FCI_COMPLETE=NO`
`IOL_FALLBACK_STARTED=NO`
`FINAL_INTEGRITY_VALIDATION=NO`
`18_PRODUCER_TIMERS_RESTORED=NO`
`RECOVERABLE_FROM_OTHER_CHAT=YES`
