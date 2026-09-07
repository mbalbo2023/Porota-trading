# RC6 — HISTORY QUALITY DEEP AUDIT — 2026-09-06

Workflow: `RC6 history quality deep audit 2026-09-06`  
Run: `34069228668`  
Resultado: `SUCCESS`  
Tipo: 100% read-only, `NETWORK_CALLS=NO`, `RUNTIME_CHANGED=NO`.

## Seguridad

- observer exacto: `porota-trading-bot:17.0.0-rc6`, restart=0, readonly rootfs=true;
- observer DB quick_check: `ok`;
- history DB quick_check: `ok`;
- `PRODUCTION_PAPER`;
- `real_orders_sent=0`.

## Hallazgo 1 — los rechazos están muy concentrados

Al corte posterior al priming existen `218.878` rechazos de filas PPI registrados explícitamente.

Por causa:
- `OHLC_INCONSISTENT`: `145.292` filas / `179` identidades;
- `HIGH_NONPOSITIVE`: `61.188` filas / `87` identidades;
- `OPEN_NONPOSITIVE`: `12.398` filas / `2` identidades.

Por familia:
- CEDEARS `OHLC_INCONSISTENT`: `123.485` filas / 146 símbolos;
- CEDEARS `HIGH_NONPOSITIVE`: `51.752` / 73 símbolos;
- CEDEARS `OPEN_NONPOSITIVE`: `12.398` / 2 símbolos;
- ACCIONES `OHLC_INCONSISTENT`: `21.810` / 33 símbolos;
- ACCIONES `HIGH_NONPOSITIVE`: `9.436` / 14 símbolos.

Los peores casos observados incluyen:
- `AVYC`: 6.843 `OPEN_NONPOSITIVE`;
- `IRSAC`: 5.698 `OHLC_INCONSISTENT`;
- `VODC`: 5.555 `OPEN_NONPOSITIVE`;
- `AMATC`: 4.800 `OHLC_INCONSISTENT`;
- `NVSC`: 4.351 `OHLC_INCONSISTENT`.

## Hallazgo 2 — fuerte concentración en variantes C/D

Sin inferir equivalencia financiera sólo por sufijo, el agrupamiento diagnóstico muestra:
- CEDEARS terminados en `C`, `OHLC_INCONSISTENT`: 67.993;
- CEDEARS `C`, `HIGH_NONPOSITIVE`: 37.603;
- CEDEARS `D`, `OHLC_INCONSISTENT`: 30.710;
- CEDEARS `D`, `HIGH_NONPOSITIVE`: 12.022;
- ACCIONES `C`, `OHLC_INCONSISTENT`: 19.529;
- ACCIONES `C`, `HIGH_NONPOSITIVE`: 9.407.

Esto refuerza la hipótesis de semántica/serie específica de las variantes de negociación y hace especialmente útil un contraste con otra fuente oficial/estructurada como IOL. No autoriza reparar o copiar precios desde la especie base.

## Hallazgo 3 — la priorización PPI actual desperdicia casi la mitad del batch

Simulación exacta del ordering actual de `_historical_targets()` después del priming:
- primer lote de 40:
  - `22` identidades ya `VALID_PAYLOAD` / legacy complete;
  - `18` `PARTIAL`;
  - sólo `18/40` necesitaban realmente completar la serie según la semántica legacy.

Una priorización diagnóstica fail-closed, sin cambiar runtime, produjo para el mismo universo:
- `0` legacy complete;
- `10` `EMPTY_OR_INVALID`;
- `30` `PARTIAL`;
- `40/40` identidades necesitadas.

Conclusión: existe una mejora potencial muy grande de eficiencia sin aumentar rate/calls. Debe implementarse con tests, freshness/TTL y protección contra starvation, no como parche apresurado durante la rueda.

## Hallazgo 4 — el PPI historical target actual no representa todas las familias

`production_history_attempts` observado contiene sólo:
- ACCIONES: 55 identidades (`25 PARTIAL`, `30 VALID_PAYLOAD`);
- CEDEARS: 191 identidades (`10 EMPTY_OR_INVALID`, `145 PARTIAL`, `36 VALID_PAYLOAD`).

No aparecen BONOS en estos intentos PPI, aunque el History Store sí tiene `883` filas / `25` identidades BONOS provenientes de `DATA912_POROTA_BATCH`.

La causa visible en código es que `_historical_targets()` filtra `candidate_universe` por `(can_simulate=1 OR instrument_type='INDICES')`. Esto puede ser correcto para el universo PAPER operable, pero **no es suficiente como definición de universo histórico completo** si el objetivo es mantener históricos de todas las familias disponibles en PPI.

Pendiente P1:
- separar `PAPER_TRADING_UNIVERSE` de `HISTORY_INGEST_UNIVERSE`;
- el histórico debe seleccionar familias según capacidad del endpoint/contrato de history, no según `can_simulate`;
- preservar reglas contractuales específicas: no forzar OHLC sobre familias que no tengan esa semántica;
- validar por familia antes de ampliar.

## Store canónico actual por familia/fuente

- ACCIONES / PPI: `12.144` filas, `55` identidades, 2025-09-04 → 2026-09-04;
- CEDEARS / PPI: `35.296` filas, `181` identidades, 2025-09-04 → 2026-09-04;
- BONOS / Data912: `883` filas, `25` identidades, 2025-09-05 → 2026-09-04.

Total continúa `48.323` filas / `261` identidades.

## Decisiones

1. No relajar validadores OHLC.
2. No fuzzy-mapear variantes C/D con la especie base.
3. No hacer más presión PPI inmediatamente: respetar backoff.
4. Priorizar el proof IOL para comparar anomalías de las series problemáticas.
5. Diseñar una priorización PPI que no consuma 22/40 llamadas en identidades completas.
6. Separar explícitamente universo de trading y universo histórico.
7. Estos hallazgos son `P1` de calidad/cobertura; no crean por sí solos un blocker P0 del PAPER del lunes.
