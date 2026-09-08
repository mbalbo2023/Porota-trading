# POROTA TRADING RC6 — CHECKPOINT CAUCIONES / RESPUESTAS SOPORTE PPI

Fecha: 2026-09-08
Rama canónica: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`
Fuente: dos respuestas escritas de soporte PPI aportadas por el usuario en esta conversación (07/09/2026 y 08/09/2026).

## 1. Hallazgos confirmados por soporte PPI

### 1.1 Discovery / naming / plazo

PPI indicó explícitamente:

- endpoint de búsqueda read-only: `/api/1.0/MarketData/SearchInstrument?name=1&market=byma&type=CAUCIONES`;
- en `name` puede ingresarse la cantidad de días;
- ticker = `MONEDA + cantidad de días`;
- ejemplos: `PESOS1`, `PESOS120`, `DOLAR1`, `DOLAR120`;
- ejemplos de response observados por soporte incluyen:
  - `ticker`
  - `description`
  - `currency`
  - `type=CAUCIONES`
  - `market=BYMA`
  - `isin=null`
  - `cajaValoresCode=0`
  - `nominalInPrice=1`.

El número incluido en el ticker identifica la cantidad de días de la caución.

- `PESOS1` corresponde a una caución por 1 día.
- Los días son **días corridos**.
- El cómputo comienza **el mismo día en que se carga la orden**.
- Ejemplo explícito de soporte: una caución a 3 días cargada un viernes queda caucionada viernes, sábado y domingo, y el lunes a primera hora el saldo ya está líquido.
- Fines de semana y feriados participan en el conteo como días corridos.
- PPI indicó que la fecha/hora de vencimiento no viene explícita en Client API y debe derivarse del ticker y la fecha de concertación/carga.

### 1.2 Market data / tasa / book semantics

PPI confirmó:

- la tasa de caución se expone en la propiedad `price`;
- aplica en:
  - `/api/1.0/MarketData/Book?ticker=PESOS1&type=CAUCIONES&settlement=INMEDIATA`
  - `/api/1.0/MarketData/Current?ticker=PESOS2&type=CAUCIONES&settlement=INMEDIATA`;
- ejemplo: `price: 20.1` representa TNA 20,1% anual;
- para **caución colocadora** se deben usar los datos de `Bids`;
- el `quantity` del book representa el monto de la posición **tomadora**.

Estos campos deben tratarse como semántica contractual provista por PPI, no inferida desde la UI.

### 1.3 Mínimos y step de cantidad

PPI informó mínimos:

- ARS: **100.000**;
- USD / DOLAR: **100**.

PPI además confirmó:

Para cauciones en ARS:
- valores enteros son válidos;
- ejemplo: ARS 100001 es válido;
- fracciones no son válidas;
- ejemplo: ARS 100000,50 no es válido;
- incremento/step: ARS 1.

Para cauciones en USD:
- incremento/step: USD 1;
- tratar cantidad como entero, no fraccionario.

Por lo tanto quedan contractualizados:

- `CAUCION_ARS_MIN_QUANTITY=100000`
- `CAUCION_USD_MIN_QUANTITY=100`
- `CAUCION_ARS_QUANTITY_STEP=1`
- `CAUCION_USD_QUANTITY_STEP=1`
- `CAUCION_QUANTITY_INTEGER_ONLY=TRUE`

### 1.4 Convención de interés

Soporte PPI confirmó para cauciones BYMA:

- day-count: días corridos / 365;
- convención: Actual/365;
- NO usar base 360;
- aplica tanto a ARS como a USD.

Fórmula indicada:

`Interés bruto = Capital × (TNA / 100) × Plazo / 365`

Ejemplo de soporte:

- Capital: 100000
- TNA: 20,1%
- Plazo: 7 días
- Interés bruto aproximado: 385,48

### 1.5 Budget / respuestas numeradas de soporte

En el email del 07/09 PPI respondió además:

- `13- con el endpoint Order/Budget`
- `14- Si`
- `15- No`
- `16- No`

Como en el material aportado aquí no están reproducidas las preguntas originales numeradas 13, 14, 15 y 16, NO se asigna semántica adicional a esos tres `Sí/No` ni se infiere qué capacidad concreta confirman o niegan.

Sólo puede conservarse de forma segura que PPI vinculó la respuesta 13 al endpoint `Order/Budget`.

Antes de convertir 14/15/16 en reglas operativas hay que recuperar el texto exacto de esas preguntas originales.

## 2. Implicancias para POROTA

Estos puntos dejan de estar `NOT_PROVEN` a nivel contractual:

- `CAUCION_SEARCH_ENDPOINT=MarketData/SearchInstrument`
- `CAUCION_SEARCH_NAME_CAN_BE_TERM_DAYS=TRUE`
- `CAUCION_TERM_DAYS_SOURCE=TICKER_SUFFIX`
- `CAUCION_MARKET=BYMA`
- `CAUCION_SETTLEMENT_MARKETDATA=INMEDIATA` para los ejemplos provistos;
- `CAUCION_RATE_FIELD=price`
- `CAUCION_RATE_UNIT=PERCENT_TNA_ANNUAL`
- `CAUCION_PLACED_SIDE_BOOK_SOURCE=Bids`
- `CAUCION_BOOK_QUANTITY_MEANING=TAKER_POSITION_AMOUNT`
- `CAUCION_DAY_COUNT=ACTUAL_365`
- `CAUCION_TERM_CALENDAR_DAYS=TRUE`
- `CAUCION_START_DATE=ORDER_LOAD_DATE`
- `CAUCION_QUANTITY_INTEGER_ONLY=TRUE`
- `CAUCION_ARS_MIN_QUANTITY=100000`
- `CAUCION_USD_MIN_QUANTITY=100`
- `CAUCION_ARS_QUANTITY_STEP=1`
- `CAUCION_USD_QUANTITY_STEP=1`
- `CAUCION_API_EXPLICIT_MATURITY_FIELD=NO`
- `CAUCION_BUDGET_ENDPOINT_REFERENCED_BY_PPI=Order/Budget`

## 3. Regla de derivación temporal

La evidencia de soporte permite modelar el plazo como N días corridos desde la fecha de carga/concertación. El ejemplo viernes + 3 días implica fondos líquidos el lunes.

Sin embargo, NO debe inventarse todavía una regla completa para todos los casos de calendario en que el instante de disponibilidad caiga en un día/horario no operativo. La respuesta de soporte no especifica explícitamente:

- timezone concreto (por nombre IANA o offset);
- hora exacta de corte/liquidación;
- qué ocurre si una fecha de disponibilidad teórica cae en un día sin proceso de liquidación;
- calendario operativo técnico usado por Client API para timestamping.

Por lo tanto:

- usar fecha local de mercado sólo cuando esté resuelta por configuración canónica;
- no hardcodear timezone nuevo a partir de esta respuesta;
- la lógica de fecha debe ser testeada contra sandbox/API cuando auth/entorno lo permita.

## 4. Pendientes de Cauciones que siguen abiertos

Aunque las dos respuestas cierran muchos campos críticos, `CAUCIONES_AUTO_PLACEMENT=false` debe mantenerse.

Todavía falta demostrar o reconciliar:

1. API execution support real con credenciales válidas;
2. recuperar las preguntas originales 13–16 para mapear exactamente las respuestas de soporte;
3. Budget/Confirm/Cancel semantics en sandbox, sin usar producción para probar;
4. lado exacto / payload de Budget y Confirm para caución colocadora versus tomadora;
5. fees/comisiones/impuestos exactos por caución;
6. price/rate rounding y precisión contractual de TNA;
7. profundidad/paginación completa del libro;
8. saldo disponible / settlement exacto, especialmente semántica de DOLAR;
9. timezone/cutoff exactos;
10. reglas de disponibilidad si el evento de liquidación cae fuera de ventana operativa;
11. DOM/API/Contract Evidence reconciliation;
12. tests de casos límite: viernes, fin de mes, fin de año, feriados, USD y ARS.

## 5. Cambio de semáforo recomendado

Ahora:

- naming/plazo: GREEN por evidencia de soporte;
- SearchInstrument para cauciones: GREEN contractual;
- `price` como TNA: GREEN contractual;
- caución colocadora desde `Bids`: GREEN contractual;
- `quantity` book = monto tomador: GREEN contractual;
- mínimos ARS 100000 / USD 100: GREEN contractual;
- días corridos: GREEN;
- Actual/365: GREEN;
- quantity integer + step 1 ARS/USD: GREEN;
- maturity explicit API field: GREEN como `NO`, derivación requerida;
- `Order/Budget` mencionado por PPI: GREEN como referencia de endpoint, NO como prueba de operabilidad completa;
- timezone/cutoff: YELLOW;
- fees: YELLOW;
- exact payload/order semantics: YELLOW;
- API execution: RED/NOT_PROVEN mientras PPI Production API auth siga bloqueada y no exista prueba sandbox suficiente;
- READY_PAPER: NO;
- AUTO_PLACEMENT: FALSE.

## 6. Próxima validación segura recomendada

PPI recomienda expresamente usar sandbox para estudiar los requests/responses técnicos.

Cuando el sandbox API esté disponible, probar únicamente en sandbox y con aislamiento explícito:

1. `SearchInstrument` para distintos plazos y monedas;
2. `Current` y `Book` read-only;
3. validar Bids/quantity/TNA contra contrato de soporte;
4. `Order/Budget` sólo en sandbox para conocer payload/semántica;
5. no usar `Confirm` hasta tener un test plan de sandbox explícito y demostrar que no existe riesgo de tocar producción;
6. documentar response sanitizado, sin secretos;
7. no promover a READY_PAPER hasta completar fees, settlement y reglas de ejecución.

## 7. Seguridad

- No realizar pruebas de orden en producción.
- Sandbox es el ambiente recomendado por PPI para estudiar requests/responses de ejecución.
- Mantener `real_orders_sent=0`.
