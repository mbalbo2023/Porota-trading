# POROTA TRADING — Checkpoint calidad semántica histórico PPI API

Fecha: 2026-09-12
Rama: `ops/rc6-ppi-fullfamily-history-20260912`
Modo: diagnóstico read-only; runner histórico NO detenido ni reiniciado.

## Veredicto ejecutivo

El problema observado no es corrupción SQLite ni un error general del parser OHLC. El mismo endpoint PPI `MarketData/Search` y el mismo validador producen series totalmente coherentes para instrumentos líquidos/control, pero PPI devuelve series degradadas, congeladas o parcialmente inconsistentes para determinados tickers, incluso dentro de una misma familia.

Por lo tanto:

1. NO relajar el contrato FULL_OHLC global.
2. NO sintetizar ni reparar open/high/low/volume.
3. Mantener validación fila por fila.
4. Separar `sin datos` de `datos recibidos pero semánticamente inválidos`.
5. Tratar series repetidas con volumen cero como evidencia degradada/stale, no como N observaciones independientes de contexto.
6. Al cerrar el API pass, enrutar residuales a PPI Web read-only y después IOL sólo si persisten huecos.

## Evidencia de control

### ACCIONES — GGAL — control PPI documentado

- Settlement: A-48HS
- Filas: 245
- OHLC válidas: 245/245
- Rechazos: 0
- Valores distintos: max 196, min 196, open 191, price 197, volume 245
- Medianas: max 7400, min 7050, open 7210, price 7175

Conclusión: endpoint, SDK, parser y regla OHLC funcionan correctamente cuando PPI entrega barras históricas normales.

## CEDEARS

### AVYC — degradado

- Estado runner: DONE_EMPTY
- Filas PPI: 244
- OHLC inválidas: 244/244
- open=0: 244/244
- high=min: 244/244
- volume=0: 244/244
- max/min tienen sólo 1 valor distinto: 8.34
- price tiene sólo 5 valores distintos en 244 fechas; mediana 0.32
- previousClose=0 y marketChange=0 en toda la muestra

Ejemplo: `openingPrice=0`, `max=8.34`, `min=8.34`, `price=0.43`, `volume=0`.

### AAPL — control de la MISMA familia

- Estado runner: ALREADY_COVERED
- Filas PPI: 245
- OHLC válidas: 245/245
- Rechazos: 0
- max 204 valores distintos, min 205, open 194, price 198, volume 245

Conclusión CEDEARS: NO es una incompatibilidad de familia. AVYC presenta una serie PPI degradada/congelada; AAPL demuestra que PPI sí entrega CEDEAR FULL_OHLC correcto.

## Obligaciones Negociables (ON)

### YMCPO — degradado/congelado

- Estado runner: DONE_EMPTY
- Filas: 244
- OHLC inválidas: 244/244
- high=min: 244/244
- volume=0: 244/244
- openingPrice fijo: 80000 en las 244 fechas
- price/max/min sólo 5 valores distintos
- mediana price=max=min: 77933.77
- previousClose=0 y marketChange=0

Ejemplo: `open=80000`, `high=min=close=78667.73`, `volume=0`; open queda por encima de high.

### YMCIC — control misma familia

- Estado runner: ALREADY_COVERED
- Filas: 245
- OHLC válidas: 202
- OHLC inválidas: 43
- max 128 valores distintos, min 123, open 138, price 126, volume 234
- algunos defectos reales: close > high o high/min=0 en filas puntuales

Conclusión ON: PPI puede entregar buen histórico en esta familia, pero también mezcla filas defectuosas. La política correcta es conservar filas válidas y rechazar sólo las defectuosas; no rechazar todo el payload ni relajar OHLC.

## FUTUROS

### DLR/AGO27M

- Filas: 8
- OHLC inválidas: 8/8
- open=0: 8/8
- high=min: 8/8
- high=min=0 en 2 filas
- volume=0 en 6 filas
- aparecen volúmenes extremadamente grandes en las otras filas

### DLR/DIC26A

- Filas: 7
- OHLC inválidas: 7/7
- open=0 en 4/7
- high=min en 7/7
- high=min=0 en 2 filas
- volume=0 en 4/7

Conclusión FUTUROS: en los contratos probados, el endpoint histórico PPI no entrega una barra FULL_OHLC utilizable. Mantener `price` sólo como evidencia degradada, y resolver FULL_OHLC mediante residual PPI Web/IOL si existe una fuente válida. No inferir open/high/low desde close.

## OPCIONES

### PAMV3900DI — degradado/congelado

- Filas: 38
- OHLC inválidas: 38/38
- open=max=min=119 en las 38 fechas
- close=169 en las 38 fechas
- volume=0 en 37/38
- todos los campos de precio tienen 1 solo valor distinto durante toda la ventana

### PAMC3700OC — control misma familia

- Estado runner: ALREADY_COVERED
- Filas: 36
- OHLC válidas: 29
- OHLC inválidas: 7
- max 28 valores distintos, min 24, open 23, price 27, volume 32

Conclusión OPCIONES: tampoco es un problema de familia completa. Existen series válidas y series congeladas/defectuosas dentro de OPCIONES.

## Hallazgo transversal: `previousClose`, `marketChange`, `marketChangePercent`

En las muestras consultadas mediante histórico PPI, incluyendo GGAL válido, `previousClose` y `marketChange` permanecieron en 0 y `marketChangePercent` en null. No deben usarse para reparar ni reinterpretar OHLC histórico.

## Riesgo adicional en close-only

El salvataje `close-only` preserva fecha+close sin inventar OHLC, lo cual es seguro como evidencia. Sin embargo, una serie como AVYC (244 fechas, sólo 5 precios distintos, volumen cero siempre), YMCPO (244 fechas, sólo 5 precios distintos, volumen cero siempre) o PAMV3900DI (38 fechas con exactamente el mismo close) puede representar carry-forward/stale provider data y no 244/38 observaciones económicas independientes.

Recomendación: conservar estas filas como evidencia, pero agregar clasificación `STALE_OR_NO_TRADE` / `PROVIDER_CARRIED` y NO computarlas como profundidad de contexto o barras independientes hasta ser reconciliadas.

## Mejora de estados propuesta (posterior al runner activo)

El runner actualmente usa `DONE_EMPTY` tanto para payload vacío como para `provider_rows>0, valid_rows=0`. Conviene separar:

- `DONE_NO_PROVIDER_ROWS`
- `DONE_PROVIDER_INVALID`
- `DONE_PARTIAL`
- `DONE_VALID`
- `ALREADY_COVERED`
- `ERROR`

Esto mejora diagnóstico y el manifest residual sin cambiar seguridad ni lógica de trading.

## Estado del runner durante las pruebas

Los probes fueron sólo lectura / GET PPI. El servicio permaneció `active`, PID del runner 5842, `SAFETY=PRODUCTION_PAPER|0`, y la ingesta siguió avanzando. En el último snapshot de esta investigación: `BATCH=266`, `DONE=484`, `PENDING=1454`, `ERRORS=22`, `CANONICAL_ROWS=321202`.

No se detuvo, reinició ni lanzó un segundo writer histórico.
