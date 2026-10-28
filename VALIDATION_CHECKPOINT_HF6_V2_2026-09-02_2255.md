# POROTA HF6-v2 — checkpoint de validación agrupada

Fecha operativa: 2026-09-02 (America/Argentina/Buenos_Aires).

> Evidencia WIP. No autoriza deploy.

## Seguridad/runtime observado

- Observer antes: running, RestartCount=0, rootfs read-only, imagen `porota-trading-bot:17.0.0-rc3-hf6`.
- `PRAGMA quick_check=ok`.
- Runtime: `PRODUCTION_PAPER | WAITING_MARKET | MARKET_CLOSED | PPI OK | real_orders_sent=0`.
- Observer después: sin cambios.
- `POROTA_RUNTIME_MUTATION=NO`.
- `REAL_ORDERS_SENT_BY_THIS_VALIDATION=0`.
- `DEPLOY_EXECUTED=NO`.

## Invariantes confirmados en la rama WIP

- Data912 no puede actuar como fuente de ejecución.
- A3 no se consulta sincrónicamente como validación secundaria cuando PPI tiene el snapshot live completo/fresco.
- Una divergencia background A3/PPI no puede poner en HOLD una decisión PPI válida.
- Existe política de riesgo concurrente dinámico separada del viejo límite financiero fijo de posiciones.
- El planner de cauciones de cierre no tiene order routing.

## A3 CEM público validado desde el Droplet

- Swagger público: accesible.
- `products`: HTTP 200, JSON válido, 36 productos observados.
- `symbols`: HTTP 200, JSON válido, 545 símbolos observados.
- Esto habilita uso WIP de CEM como fuente oficial A3 de `reference data`/universo para derivados, siempre fuera del hot path y sin capacidad de ejecución.
- `closing-prices` y `tick-prices` NO se declaran todavía validados por contenido en este checkpoint: la salida compacta no incluyó su resultado y debe revisarse el reporte detallado.
- reMarkets autenticado continúa separado de CEM público y no es requisito para aprovechar `products/symbols`.

## Estado de tests

- `DAILY_RESPONSIVE_PATCH_CHECK_RC=0`.
- `GROUPED_TEST_RC=1`.
- Dictamen global: `YELLOW_OR_RED_REVIEW_DETAIL`.
- No se promueve ni cablea al runtime ninguna pieza que dependa de la batería hasta identificar y corregir el test exacto fallido.
- No se repite toda la batería para diagnosticar; se extrae primero el bloque de fallo del reporte persistido.

## Política de fuentes reafirmada

PPI sigue siendo broker y fuente live primaria. Si PPI tiene los datos live completos, frescos y válidos requeridos por una decisión, Porota decide con PPI. A3/CEM/Data912 no intervienen sincrónicamente ni bloquean por divergencias background. A3/CEM aportan contrato, históricos, reference data y auditoría asíncrona. Data912 es exclusivamente histórico batch.

## Próximo paso

1. Extraer del reporte `porota_hf6_v2_validation_20260903T015521Z.txt` el fallo pytest y cualquier respuesta/error de `closing-prices`/`tick-prices`.
2. Corregir sólo la causa real del test.
3. Reejecutar únicamente la batería afectada o una validación agrupada final cuando corresponda.
4. Continuar con integración WIP de History Store v2, Contract Evidence, UX, Scheduler, Logs, riesgo concurrente y cash sweep.
5. Antes de cualquier deploy: presentar alcance exacto, tests, migraciones, timers, familias READY/HOLD, rollback y limpieza de disco; requerir autorización explícita.
