# POROTA TRADING RC6 — CP1 MATRIZ FUNCIONAL Y RCA PARALELO

Fecha: 2026-09-11
Branch: `fix/rc6-w10-sector-map-binding-20260910`
SHA que disparó CP1: `6ef45b1527e873b59878cc55bbb9731d47d46f00`
Runtime auditado: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Workflow: `RC6 CP1 Parallel Runtime Audit 2026-09-11`
Run: `34546549518`
Resultado: `SUCCESS` (4/4 jobs SUCCESS)
Mutaciones runtime: NINGUNA
Seguridad: `PRODUCTION_PAPER|0`
Rutas de orden real: NO llamadas
Feed general de noticias: INTENCIONALMENTE OFF / NO REACTIVAR

## Resultado ejecutivo

CP1 confirma que la mayoría de las capacidades existen en el runtime, pero no corresponde declarar 18/18 GREEN todavía. Hay cuatro gaps concretos que requieren forward-fix: UX/cabeceras + ledger de validación, Contract Evidence materialization, ampliación/calidad histórica, y wiring operativo GDELT/event-risk SHADOW. No se debe redeployar una ola solamente porque el tip de su branch no sea ancestor del SHA desplegado: el candidato contiene convergencia posterior y hay que juzgar funcionalidad real.

## Matriz funcional provisional 1–18

| Ola | Estado CP1 | Evidencia / gap |
|---|---|---|
| W1 | AMARILLO | W1B SLA/scraping es ancestor; W1A tip no. Safety/observability runtime existe. Certificar equivalencia funcional, no redeploy ciego. |
| W2 | VERDE | Tip PPI130 runtime ancestor; runtime PPI/PAPER seguro. |
| W3 | AMARILLO | Tip Browser V3 no ancestor, pero trusted browser/capturas existen. Certificar convergencia en W12. |
| W4 | AMARILLO | Tip Phase A no ancestor; raw/evidence storage existe. Falta equivalencia exacta antes de cierre. |
| W5 | AMARILLO | Dashboard CE existe y es visible, pero materialización actual está stale/incompleta. |
| W6 | AMARILLO | History/web shadow está integrado; históricos funcionan, cobertura sigue parcial. |
| W7 | AMARILLO | Event-risk contract existe en runtime, SHADOW_ONLY; no hay wiring operativo propio demostrado. |
| W8 | AMARILLO | UX/live wiring existe, pero tablas no usan `<thead>` y cabeceras no permanecen interpretables durante scroll. |
| W9 | VERDE funcional / tip divergente | Contratos familiares y readiness visibles; no redeploy por ancestry solamente. |
| W10 | VERDE | Sector concentration BINDING vigente; no reabrir sin regresión. |
| W11 | AMARILLO | GDELT module existe, pero no hay unit/timer/caller productivo; sólo tests y contrato/dashboard import. Requiere wiring SHADOW separado del news feed. |
| W12 | AMARILLO | Captura trusted y timer funcionan; pipeline capture→canonical v2 no actualiza todas las familias/corridas. |
| W13 | AMARILLO | Planner/reconcile/timers existen; cobertura histórica parcial y Futures A3 alignment unverified. |
| W14 | VERDE | Functional Health ya activo/enabled y con servicios success según auditorías previas. |
| W15 | VERDE integrado | Forward lab/MFE-MAE integrado; pendiente sólo full integrated gate final. |
| W16 | VERDE integrado | Backup/retention/disk governance integrado; no cambios destructivos. |
| W17 | AMARILLO | Base tablet/Voice Access integrada, pero defecto actual de cabeceras obliga recertificación UX. |
| W18 | VERDE integrado | Control-plane/recovery sin auto rollback; pendiente gate final conjunto. |

## CP1-A — UX / tablas / validación

Páginas autenticadas inspeccionadas: `/en-vivo`, `/universo-operativo`, `/validacion`, `/instrumentos`, `/historicos`, `/riesgo`, `/sistema?section=scraping`.

Hallazgo estructural: las tablas tienen celdas `<th>` pero casi todas carecen de `<thead>` (`THEAD=0`). Ejemplos:
- `/en-vivo`: 3 tablas, 7/6/5 TH, todas THEAD=0.
- `/universo-operativo`: 5 tablas, 14/11/7/11/7 TH, todas THEAD=0.
- `/instrumentos`: 1 tabla, 10 TH, THEAD=0.
- `/historicos`: 5 tablas, 6/6/6/7/7 TH, todas THEAD=0.
- `/sistema?section=scraping`: 1 tabla, 7 TH, THEAD=0.
- `/riesgo`: 3 tablas y sí usan THEAD=1.

Conclusión: no es ausencia de nombres en HTML; el problema es semántica/UX de encabezado y persistencia visual al hacer scroll en tablas muy largas. Forward-fix: envolver header row en `<thead>` donde falte + CSS sticky seguro dentro del scroll horizontal, sin destruir estructura ni accesibilidad.

`/validacion` no tiene tablas y muestra literalmente `Todavía no hay jornadas registradas en el ledger RC6.`. La fuente es `/app/en_validation_project_dashboard_rc6.py` (líneas ~100/125). DB sí contiene actividad real: `paper_daily_risk=52`, history ledger, intraday state y 227735 puntos intraday. Por lo tanto hay que auditar la tabla/fuente específica usada por esa pantalla y materializar únicamente jornadas reales; no inventar jornadas.

## CP1-B — Contract Evidence / scraping

Servicio `/etc/systemd/system/porota-contract-evidence-rc6.service`, timer cada 5 minutos, read-only. Las ejecuciones recientes marcan `GREEN_NOT_DUE`, sin browser, PPI calls 0 y real orders 0. Esto es correcto para el poll scheduler.

Persistencia:
- `contract_evidence_v2_runs=106`
- `contract_evidence_v2_current=519`
- `contract_evidence_v2_snapshots=924`
- último canonical por familias: 2026-09-09 20:06 UTC aproximadamente.
- trusted captures: 79; última `contract_20260910T213256Z.json`, 2026-09-10 21:34:24 UTC.
- última corrida STATIC: 2026-09-10 21:34:24 UTC, `AMARILLO`, records=0.
- DOM_SEMANTIC del 09/09: 104 mapped, 493 unmatched, contract completeness NOT_PROVEN.
- FULL_BROWSER del 09/09: records=40, mapped=0, wildcard=40.

Gap confirmado: producer/poll corre, pero las capturas nuevas no se transforman en evidencia canonical útil/fresca. Además algunas capturas inmediatamente anteriores a la última siguen con permisos que impiden lectura directa desde el usuario de auditoría; la última captura sí es legible. No reabrir autenticación: W12 auth ya fue probado; investigar selector/parser/materializer y permisos residuales únicamente.

## CP1-C — Históricos

Attempt ledger al momento de CP1: 17544 aprox. estados:
- `VALID_ROWS_WITH_REJECTIONS`: 12138
- `VALID_PAYLOAD`: 4695
- `EMPTY_OR_INVALID`: 711

Los 711 EMPTY_OR_INVALID pertenecen a CEDEARS en el corte observado.

Rechazos acumulados: `559096`, razones:
- `OHLC_INCONSISTENT`: 370802
- `HIGH_NONPOSITIVE`: 156750
- `OPEN_NONPOSITIVE`: 31544

No se aflojarán validaciones para aumentar cobertura artificialmente. Hay que determinar si son filas proveedor no-mercado/ajustadas/cabeceras/shape o datos realmente inválidos y conservar fail-closed.

A3 Futures: 40 estados `ALIGNMENT_UNVERIFIED`, 320 intentos, 0 empty, último intento 2026-09-10 18:30:10-03, sin last_success. Esto es gap real para W13.

History canonical v2:
- total 49143 rows
- ACCIONES: 12340, 55 símbolos, latest 2026-09-10
- BONOS: 883, 25 símbolos, latest 2026-09-04
- CEDEARS: 35920, 182 símbolos, latest 2026-09-10

Conclusión: ingesta no está muerta; el cuello es cobertura por familia/calidad y Futures A3, más backfill incremental. Preparar corrida nocturna bounded y no duplicar timers activos.

## CP1-D — Riesgo / GDELT

Regla reafirmada: `GENERAL_NEWS_FEED_POLICY=INTENTIONALLY_OFF_DO_NOT_REENABLE`.

`fk_gdelt_shadow_feed_rc6.py` está presente y realiza GET read-only a GDELT DOC 2.0 con query packs estructurados por tipo de evento. Normaliza a `EventEvidence` con provenance, timestamps y source tier. `fi_event_risk_shadow_rc6.py` define explícitamente `SHADOW_ONLY`, sin BUY/SELL, broker, DB ni network.

No existe unit/timer GDELT/event-risk activo encontrado. Callers encontrados en runtime: tests y `zz_wave8_dashboard_live_rc6.py` importando el contrato; no hay scheduler productor persistente demostrado. Por lo tanto W11 está implementado como librería/contrato pero NO operacionalizado.

La tabla `financial_news` existe históricamente pero debe permanecer fuera de esta reparación porque el feed general de noticias fue desactivado intencionalmente.

## Siguiente ejecución autorizada por el plan

1. CP2 forward-fix UX + RCA/materialización ledger de validación.
2. CP3 forward-fix Contract Evidence capture→normalizer→canonical v2, preservando read-only/fail-closed.
3. CP4 historia: RCA de calidad + bounded night backfill + A3 Futures alignment, sin relajar checks.
4. CP5 GDELT/event-risk: scheduler/persistencia SHADOW independiente; generic news sigue OFF.
5. Ejecutar CP2–CP5 en paralelo sólo sobre superficies no conflictivas, con workflows independientes y pre/post safety.
6. Crear checkpoint individual al cerrar cada CP.

## Estado

`CP1=GREEN_AUDIT_COMPLETE`
`GLOBAL_RC6=YELLOW`
`GO_18_OF_18=NO`
`PRODUCTION_PAPER=YES`
`REAL_ORDERS=0`
`GENERAL_NEWS_FEED=INTENTIONALLY_OFF`
`NEXT=CP2_CP3_CP4_CP5_PARALLEL_FORWARD_FIX`
