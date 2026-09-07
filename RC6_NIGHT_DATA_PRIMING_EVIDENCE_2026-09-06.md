# RC6 — NIGHT DATA PRIMING EVIDENCE — 2026-09-06

Workflow: `RC6 night data priming 2026-09-06`  
Run: `34068494819`  
Resultado: `SUCCESS`  
Runtime objetivo verificado: `hotfix/rc6-cedear-us-labor-day-20260906` @ `db26c76723bb988c956589c572b87cbcb4191731`.

## Seguridad / preflight

- BYMA operational day: `NO` durante la ejecución.
- observer DB quick_check: `ok`.
- history DB quick_check: `ok`.
- mode: `PRODUCTION_PAPER`.
- `real_orders_sent=0`.
- A3 no fue forzado.
- Contract Evidence browser no fue forzado.
- estrategia no cambió.
- runtime code no cambió.

## BEFORE

- `production_history_attempts`:
  - `EMPTY_OR_INVALID=10`
  - `ERROR=4`
  - `PARTIAL=169`
  - `VALID_PAYLOAD=63`
- legacy complete coverage informada por PPI job: `67/246`.
- último lote antes de esta corrida: `6/40` completos, `6896` filas válidas vistas, `34` no completos/fallidos.
- `history_canonical_v2`:
  - rows: `48,323`
  - identities: `261`
  - first day: `2025-09-04`
  - latest day: `2026-09-04`
- fuentes canónicas:
  - `PPI_PRODUCTION_HISTORY`: `47,440` filas / `236` identidades
  - `DATA912_POROTA_BATCH`: `883` filas / `25` identidades
- `history_versions_v2=48,691`.

## PPI bounded priming

El lote PPI forzado fue read-only y acotado. Resultado:

- `valid_rows_reported=8,108`.
- estados después del lote:
  - `EMPTY_OR_INVALID=10`
  - `ERROR=2`
  - `PARTIAL=169`
  - `VALID_PAYLOAD=65`
- `real_orders_sent=0`.

Interpretación:
- dos identidades salieron de `ERROR` y pasaron a `VALID_PAYLOAD`;
- el lote mejoró de `6/40` a `11/40` series completas y de `6,896` a `8,108` filas válidas vistas;
- la cobertura legacy completa permaneció `67/246`, lo que confirma que esa métrica no refleja por sí sola todo el material salvado por History Store v2;
- no se debe repetir inmediatamente: respetar TTL/backoff de PPI.

## Data912 reconciliation — batch 40

Resultado:
- selected: `40`
- successful: `2`
- without_history: `20`
- failed: `18`
- remaining_after_selection: `29`
- canonical_updates reportados por el sink: `243`
- protected_rows por precedencia: `243`
- versions_appended: `0`
- `execution_allowed=false`

Interpretación importante:
- Data912 encontró material en dos identidades, pero las 243 potenciales actualizaciones quedaron protegidas por la política de precedencia existente;
- no sobreescribió PPI ni agregó versiones canónicas en esta corrida;
- esto es comportamiento fail-safe correcto, no un fallo de integridad;
- 20 identidades no devolvieron histórico y 18 fallaron: requiere análisis por símbolo/familia, no más llamadas indiscriminadas esta noche.

## AFTER

- `history_canonical_v2`: se mantuvo en `48,323` filas / `261` identidades.
- fuentes canónicas se mantuvieron:
  - PPI `47,440` / `236`
  - Data912 `883` / `25`
- versions: `48,691`.
- DB quick checks: `OK`.
- observer: `PRODUCTION_PAPER`.
- `real_orders_sent=0`.

## Candle integrity

Última ejecución observada durante la prueba:
- status: `GREEN`
- `checked_versions=5000`
- `dirty_bars=0`
- missing tables: ninguna
- quick_check: `ok`
- read_only: `true`
- violations: ninguna
- worker state: `RUNNING`
- cursor: `49,764`.

## Conclusión

Priming `GREEN` desde el punto de vista operacional y de seguridad. Hubo mejora cualitativa PPI (`ERROR 4→2`, `VALID_PAYLOAD 63→65`, lote `6/40→11/40`) pero no crecimiento del store canónico en esta corrida. Data912 confirmó la precedencia sin contaminar PPI. El siguiente trabajo útil es mejorar priorización/clasificación y contrastar una fuente independiente (IOL), no aumentar presión de red esta noche.
