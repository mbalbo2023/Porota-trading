# POROTA TRADING RC6 — CP0 PLAN Y HALLAZGOS

Fecha: 2026-09-11
Branch de trabajo: `fix/rc6-w10-sector-map-binding-20260910`
Branch HEAD al iniciar CP0: `ce73d7729ca756e9162c0509539c8b14d3e25e03`
SHA desplegado auditado en runtime: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Modo obligatorio: `PRODUCTION_PAPER`
Ordenes reales observadas: `0`
Real order routes: NO CALLED

## Correccion funcional obligatoria

El feed general de noticias/`financial_news` fue desactivado intencionalmente por decision funcional previa porque no agregaba valor. Su falta de frescura NO es defecto, NO es blocker y NO debe reactivarse.

GDELT, si se utiliza, se trata solamente como fuente estructurada de `event-risk`/riesgo contextual en SHADOW o segun contrato RC6, nunca como reactivacion del feed general de noticias ni como habilitador autonomo de trading.

## Hallazgos confirmados antes de cambios

1. Runtime seguro: `PRODUCTION_PAPER|0`.
2. Functional Health timer activo/enabled y ultimo servicio `success`.
3. Contract Evidence timer activo/enabled y servicio `success`.
4. Capturas CE: 79; ultima `contract_20260910T213256Z.json`, mtime 2026-09-10T21:34:24Z.
5. `contract_evidence_v2_runs` siguio ejecutando hasta 2026-09-10T21:34:24Z, pero `contract_evidence_v2_current` y snapshots tienen ultimo `observed_at=2026-09-09T20:06:20.770262+00:00`: existe gap captura/run -> materializacion canonical.
6. Dashboard scraping autenticado muestra Full Browser/Static/Dynamic/Cauciones/Auctions/Derivatives AMARILLO en distintas corridas, con numerosos registros no mapeados y ejecuciones con records=0; no se debe ocultar.
7. Historicos canónicos SI existen: `history_canonical_v2=49143`, `history_versions_v2=49791`, ultimo dato 2026-09-10.
8. `market_historical_ohlcv=107538` permanece con ultimo dato 2026-08-24: fuente legacy no debe confundirse con store canonical v2.
9. Dashboard /historicos: cobertura ANY 31.4%; 49143 filas canonicas; FULL_OHLC fresh >=30: 224/834, >=90: 218/834, >=180: 167/834; stale>=90: 6. Ultimo lote PPI reportado 20/40 completos + 20 parciales usables, 0 fallas duras, 67/246 acumulado del lote legacy.
10. `history_attempt_ledger_v2=17360`; la clasificacion de rechazos/pendientes debe auditarse antes de ampliar carga nocturna.
11. En Riesgo existe persistencia real (`paper_daily_risk`, `paper_risk_capital`, `paper_events`) y W10 sector concentration figura BINDING_PAPER. No reactivar feed general de noticias.
12. GDELT: modulos existen, pero no hay timer con nombre GDELT (`GDELT_TIMERS=0`); hay 1 timer relacionado con event-risk/news/sentiment. Falta certificar wiring funcional de event-risk sin reactivar news feed.
13. Capturas del operador muestran defecto UX: varias tablas carecen de cabecera visible, por lo que filas/columnas no son interpretables.
14. Captura /validacion: `Historial por dia` indica `Todavia no hay jornadas registradas en el ledger RC6`, inconsistente con dias y actividad PAPER ya observados; requiere RCA de wiring/fuente del ledger, no inventar jornadas.
15. Captura /en-vivo: Liquidaciones pendientes muestra `SNAPSHOT_GREATER_THAN_CURRENT_RECEIVABLES`, 14 confirmaciones pendientes y 23 ya liberadas/no listadas; mantener fail-closed y auditar semantica sin inventar cutoff del broker.

## Auditoria de ascendencia de olas contra el SHA desplegado e47eeff

- W01A integration tip: NO ancestor exacto del desplegado.
- W01B SLA/scraping: SI ancestor.
- W02 PPI130 runtime: SI ancestor.
- W03 integration tip exacto: NO ancestor.
- W04 integration tip exacto: NO ancestor.
- W05 integration tip exacto: NO ancestor.
- W06: SI ancestor.
- W07: SI ancestor.
- W08: SI ancestor.
- W09, W10, W12, W13, W14, W15, W16, W17, W18 tienen evidencia previa de integracion/ascendencia o equivalentes, pero cada una debe certificarse funcionalmente en runtime antes de marcar 18/18 GREEN.

La ausencia del tip exacto NO implica automaticamente ausencia funcional: se debe demostrar por archivos, wiring, DB, timers y dashboard del candidato desplegado. No re-deployar una ola solo por divergencia de SHA si su funcionalidad ya fue convergida por otro commit.

## Plan obligatorio por checkpoints

### CP1 — Matriz 1-18 funcional
Certificar para cada ola: codigo presente, wiring activo, persistencia, scheduler/timer, salida dashboard, pruebas, seguridad. Resultado: GREEN/YELLOW/RED y accion exacta. No repetir pruebas ya superadas.

### CP2 — UX + ledger de validacion
Corregir cabeceras visibles en todas las tablas afectadas, conservando scroll horizontal en tablet. RCA y forward-fix de `Historial por dia` vacio: identificar tabla/fuente, verificar si existe evidencia diaria y materializar solo evidencia real. Validar tablet/Voice Access.

### CP3 — Scraping / Contract Evidence
Cerrar pipeline: captura trusted -> parser/normalizador -> CE v2 current/snapshots -> readiness/dashboard. Explicar y corregir `records=0`, wildcard/unmapped y stale canonical. Read-only, sin `/Operar`, sin confirmar, sin amount/price, sin auto-READY.

### CP4 — Historicos / noche
Auditar estados/errores/rechazos por familia y fuente, separar NO_DATA legitimo de error reparable, maximizar cobertura incremental sin saturar PPI, dejar daily/reconcile/weekend/postclose/candle-integrity listos y con evidencia de proximo trigger. Correr manualmente solo si es seguro y necesario; no duplicar timers activos.

### CP5 — Riesgo / GDELT
Mantener feed general de noticias OFF. Certificar event-risk/GDELT como señal estructurada independiente: source, fetch, persistencia, freshness, mapping, dashboard y autoridad. Nunca habilita trading por una noticia aislada.

### CP6 — Olas pendientes en paralelo
Lanzar/reparar solamente las olas funcionalmente pendientes según CP1, en paralelo donde no compartan superficies mutables. Evitar re-deploy redundante. Toda mutacion: preflight PRODUCTION_PAPER/orders0 -> forward fix -> focused tests -> runtime proof -> postflight.

### CP7 — Integracion total
Full suite + CLEAN TREE RC6 + W10 binding + W12 CE + W13 history + W14 health + UX + risk + settlement + calendars + no real routes.

### CP8 — Pre-noche y continuidad
Checkpoint final antes de dejar procesos nocturnos: SHA exacto, runs, timers active/enabled, proximo trigger, cobertura inicial, CE freshness inicial, expected deltas, alertas y criterio de exito para la mañana siguiente.

## Regla de checkpoints

Crear un nuevo checkpoint Markdown despues de cada transicion material CP1..CP8. Cada checkpoint debe incluir: SHA, run IDs, estado por semaforo, pruebas ejecutadas, resultado runtime, invariantes de seguridad, hallazgos nuevos, pendientes exactos y siguiente accion. Esto es obligatorio para poder continuar desde otro chat sin reconstruir contexto.

## Estado CP0

`CP0=GREEN`
`GLOBAL_RC6=YELLOW`
`GO_18_OF_18=NO`
`PRODUCTION_PAPER=YES`
`REAL_ORDERS=0`
`GENERAL_NEWS_FEED=INTENTIONALLY_OFF`
`NEXT=CP1_MATRIX_1_18 + CP2/CP3/CP4_PARALLEL_DIAGNOSTICS`
