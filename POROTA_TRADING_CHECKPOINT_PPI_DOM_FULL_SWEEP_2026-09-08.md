# POROTA TRADING — CHECKPOINT PPI DOM FULL SWEEP 2026-09-08

Fecha: 2026-09-08
Estado: EVIDENCIA EMPÍRICA NUEVA / PENDIENTE DE INTEGRACIÓN
Rama: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`

Este archivo complementa `POROTA_TRADING_CHECKPOINT_PPI_DOM_API_OPERABILITY_ALERTING_2026-09-08.md`.

## 1. Resultado del Full DOM Sweep autenticado

Ejecución: `POROTA RC6 PPI DOM FULL SWEEP + COVERAGE`
UTC: `20260908T040124Z`

Seguridad confirmada durante la prueba:

- observer preflight: `ok|PRODUCTION_PAPER|0`;
- 16/16 familias autenticadas;
- `real_orders_sent=0`;
- `ORDER_ROUTES_VISITED=NO`;
- mutaciones permitidas: NO;
- mutaciones first-party PPI no conocidas: `0`;
- mutaciones third-party abortadas: `224`;
- DB writes: NO;
- Contract Evidence import: NO;
- observer postflight: `ok|PRODUCTION_PAPER|0`.

## 2. Familias relevadas y cobertura observada

| Familia | Filas iniciales | Filas únicas | Estado de cobertura observado |
|---|---:|---:|---|
| FCI | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| FCI Exterior | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Acciones | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Acciones USA | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Bonos | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Cauciones | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| CEDEARs | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| ETF | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Futuros | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Letras | 30 | 30 | LIKELY_COMPLETE |
| Licitaciones | 3 | 3 | LIKELY_COMPLETE |
| ON | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Opciones | 50 renderizadas | 43 únicas | POSSIBLY_TRUNCATED_50 + DUPLICATE/ROW-IDENTITY AUDIT REQUIRED |
| Índices | 17 | 17 | LIKELY_COMPLETE |
| Monedas | 30 | 30 | LIKELY_COMPLETE |
| Tasas | 50 | 50 | POSSIBLY_TRUNCATED_50 |

IMPORTANTE: `LIKELY_COMPLETE` no equivale a cobertura demostrada. Significa únicamente que la página presentó menos de 50 filas, sin crecimiento por scroll ni paginación explícita detectada. Debe reconciliarse contra un universo esperado o una fuente independiente antes de marcar `COMPLETE`.

## 3. Hallazgo arquitectónico

La captura DOM autenticada es técnicamente viable y estable para Contract Evidence de cotizaciones, pero el límite repetido de 50 filas en muchas familias indica que el DOM visible inicial NO debe tratarse como universo completo.

No se detectó paginación explícita `Siguiente/Next` ni crecimiento por scroll en estas pruebas. Por lo tanto se debe investigar una estrategia de enumeración completa que preserve read-only, por ejemplo:

- filtros/búsqueda de lista;
- particionado por prefijo/ticker;
- datos internos ya cargados en el frontend sin exponerlos todos en la tabla;
- endpoint de búsqueda read-only;
- reconciliación contra universo de instrumentos API.

No se debe importar el snapshot de 50 filas como si fuera cobertura total.

## 4. Campos útiles observados por familia

La UI/DOM aporta evidencia de mercado y contractual útil, entre otros:

- Acciones: especie, último, variación, bid/ask, cantidades, volumen, apertura, mínimo, máximo, cierre anterior, última actualización.
- CEDEARs: lo anterior + ratio.
- Bonos/ON: lo anterior + TIR y modified duration.
- Letras: vencimiento + TNA.
- Cauciones: días, último operado, monto tomador/colocador, TNA tomadora/colocadora, volumen, vencimiento, OHLC y última actualización.
- Futuros: precios, bid/ask, volumen, OHLC, ajuste anterior.
- Licitaciones: nombre, inversión mínima, moneda/especie, tasa, plazo, fecha fin, estado.
- FCI: categoría, moneda, plazo de rescate, horario límite, rendimientos, riesgo, patrimonio.
- FCI Exterior: categoría, rendimientos y riesgo.
- Índices/Monedas/Tasas: datos de referencia útiles para contexto/decisión, no necesariamente familias ejecutables.

## 5. Hallazgo específico Opciones

La página renderizó 50 filas pero solo 43 filas únicas según el snapshot. Antes de usar esta fuente como evidencia canónica se requiere auditar:

- duplicados reales vs filas repetidas por render/virtualización;
- identidad canónica de la opción;
- subyacente;
- vencimiento;
- strike;
- call/put;
- moneda/mercado;
- consistencia con API/Contract Evidence.

Estado actual: YELLOW.

## 6. Regla de operabilidad API — permanece abierta

El Full DOM Sweep demuestra visibilidad y extracción web, NO ejecutabilidad por API.

Para cada familia debe mantenerse separada la clasificación:

- `WEB_VISIBLE`
- `DOM_EXTRACTABLE`
- `API_SEARCHABLE`
- `API_MARKETDATA`
- `API_HISTORY`
- `API_ORDER_SUPPORTED`
- `API_CANCEL_SUPPORTED`
- `SETTLEMENT_SUPPORTED`
- `CONTRACT_FIELDS_SUFFICIENT`
- `READY_PAPER`

No promover ninguna familia por inferencia. Índices, Monedas y Tasas, por ejemplo, pueden ser fuentes de contexto sin ser instrumentos de orden. FCI, FCI Exterior, Licitaciones, Cauciones, Opciones, Futuros, ETF y Acciones USA/Exterior requieren evidencia específica de capacidad API antes de habilitar ejecución.

## 7. Dashboard > Scraping / Contract Evidence

El semáforo futuro debe reflejar al menos dos dimensiones separadas:

### Salud de la captura

- AUTH
- LAST_SUCCESS
- AGE
- SLA/TTL
- SCHEMA
- COVERAGE
- ROW_COUNT
- EXPECTED_UNIVERSE
- SOURCE/FALLBACK

### Readiness para operatoria

- API_EXECUTABLE
- MARKET_DATA_FRESH
- CONTRACT_FRESH
- HISTORY_FRESH
- SETTLEMENT_KNOWN
- TICK_STEP_KNOWN
- FEES_KNOWN
- FAMILY_BLOCKED
- BLOCK_REASON

Una fuente puede estar GREEN como scraper y la familia seguir RED para ejecución API.

## 8. Alerting / staleness

Mantener como pendiente crítico:

- warning por primer fallo no material;
- YELLOW por atraso, cobertura parcial o schema drift con fallback suficiente;
- RED si el dato crítico excede `max_staleness`, desaparece sin fallback o genera incertidumbre material para la decisión;
- fail-closed selectivo por familia/instrumento;
- market data, históricos, backfill, observer, dashboard e introspección deben continuar;
- Telegram/SRE deben informar causa, último dato válido, edad, fallback y bloqueo aplicado;
- recuperación debe ser explícita y deduplicada.

## 9. Próximos pasos canónicos

1. Resolver cobertura >50 de las familias truncadas sin entrar en `/Operar`.
2. Auditar duplicación/identidad de Opciones.
3. Construir matriz de capacidad PPI API familia por familia.
4. Definir universo esperado por familia para poder medir cobertura real.
5. Definir SLA/TTL/max_staleness por dato/familia.
6. Integrar DOM Contract Evidence V1 en SHADOW / NO_AUTO_ACTIVATION.
7. Reconciliar DOM vs API vs History Store.
8. Implementar semáforo Dashboard > Scraping.
9. Integrar alerting/introspección/Telegram.
10. Solo después evaluar `READY_PAPER` por familia.

## 10. Invariantes

- RC6 permanece sin órdenes reales.
- `real_orders_sent=0` absoluto.
- Browser evidence read-only.
- No usar browser para ejecutar operaciones.
- No considerar 50 filas como cobertura total.
- No confundir fuente visible con instrumento ejecutable por API.
- Cualquier dato contractual crítico desconocido => fail-closed para la decisión afectada.
