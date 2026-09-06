# POROTA TRADING — RC6 PPI HISTORY PRIME RESULT

**Fecha:** 2026-09-06  
**Workflow run:** `34066107476`  
**Ops branch:** `ops/rc6-ppi-history-prime-20260906`  
**Workflow commit:** `6b98e447d702b5d6de59f834bc22a93acdb0a954`  
**Live SHA verificado:** `db26c76723bb988c956589c572b87cbcb4191731`

## Objetivo

Ejecutar un único lote acotado de históricos PPI en modo background/read-only, fuera de rueda y sin tocar estrategia, configuración ni capacidad de órdenes.

## Preflight

Se verificó:

- branch live exacta `hotfix/rc6-cedear-us-labor-day-20260906`;
- SHA live exacto `db26c...`;
- observer running, restart=0, readonly=true;
- dashboard running, restart=0;
- imagen activa etiquetada con commit `db26c...`;
- observer DB quick_check=ok;
- history DB quick_check=ok;
- modo `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- fase de mercado `CLOSED`;
- A3/Contract Evidence no estaban ejecutando jobs pesados activos.

## Hallazgo previo importante

Al tomar el snapshot BEFORE se observó que el observer ya había ejecutado automáticamente un lote de background ingestion apenas antes del workflow:

- `last_attempt_at=2026-09-06T23:08:19Z`;
- lote automático: `11/40` completos;
- `7152` filas válidas observadas;
- cobertura acumulada: `67/246`;
- status fuente: `AMARILLO`.

Por lo tanto, el scheduler/background ingest sí estaba activo y avanzando por sí mismo. El lote manual forzado iniciado ~2 minutos después resultó redundante desde el punto de vista de cadencia. No deben forzarse más lotes PPI esta noche; debe respetarse nuevamente el backoff/TTL normal.

## Lote manual bounded

Resultado:

- batch limit: `40`;
- payloads completos: `6/40`;
- filas válidas observadas: `6896`;
- fallidos/no completos: `34`;
- status fuente: `AMARILLO`;
- `real_orders_sent=0`;
- no se realizó network order test;
- no se cambió estrategia ni configuración.

## Comparación BEFORE / AFTER

BEFORE:

- coverage legacy `production_history`: `67/246`;
- `history_canonical_v2`: `48,323` filas;
- `history_versions_v2`: `48,691` filas;
- attempt states: `VALID_PAYLOAD=63`, `PARTIAL=169`, `EMPTY_OR_INVALID=10`, `ERROR=4`.

AFTER:

- coverage legacy `production_history`: `67/246`;
- `history_canonical_v2`: `48,323` filas;
- `history_versions_v2`: `48,691` filas;
- attempt states sin cambio agregado;
- source_sync actualizado al nuevo lote `6/40`, `6896` filas válidas observadas.

Conclusión: el lote fue técnicamente sano pero **no aumentó coverage ni filas canónicas**. Esto confirma que el problema P1 no es que el scheduler esté detenido; el problema es efectividad/calidad/priorización de históricos.

## Interpretación técnica

La implementación RC6 selecciona lotes históricos acotados y actualiza `production_history_attempts` por identidad. Un resultado completo sólo eleva `production_history` cuando el payload es `VALID_PAYLOAD`; respuestas parciales/invalidas se preservan como evidencia pero no reemplazan la última descarga completa.

La evidencia de esta noche indica que:

- el background ingestion automático funciona;
- PPI responde y entrega miles de filas;
- gran parte de los instrumentos no produce payload completo aceptable bajo los validadores actuales;
- los lotes pueden volver a gastar capacidad en identidades ya cubiertas mientras otras siguen sin cobertura, por lo que debe auditarse la priorización de targets;
- no se deben relajar validadores para aumentar artificialmente coverage.

## Acción siguiente P1

Sin más llamadas PPI forzadas esta noche:

1. analizar offline las 40 identidades del lote y sus estados;
2. separar `PARTIAL`, `EMPTY_OR_INVALID` y `ERROR` por causa;
3. determinar si `HIGH_NONPOSITIVE`, `OHLC_INCONSISTENT`, JSON defectuoso o semántica de instrumento explican la falta de ingestión canónica;
4. revisar si `_historical_targets()` debe priorizar identidades nunca cubiertas antes de refrescar identidades ya cubiertas, preservando rate-limit/backoff;
5. cualquier cambio de algoritmo debe ir con tests y no desplegarse durante la rueda del lunes.

## Postflight

- observer running, restart=0, readonly=true;
- dashboard running, restart=0;
- DB observer quick_check=ok;
- DB history quick_check=ok;
- modo `PRODUCTION_PAPER`;
- fase `CLOSED`;
- `real_orders_sent=0`;
- real-order capability permanece bloqueada.

**Estado:** `RC6_PPI_HISTORY_PRIME=GREEN` operacionalmente; `PPI_PRODUCTION_HISTORY=AMARILLO` por cobertura/calidad.
