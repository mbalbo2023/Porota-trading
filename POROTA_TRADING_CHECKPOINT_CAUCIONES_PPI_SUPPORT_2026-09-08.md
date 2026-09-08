# POROTA TRADING RC6 — CHECKPOINT CAUCIONES / RESPUESTA SOPORTE PPI

Fecha: 2026-09-08
Rama canónica: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`
Fuente: respuesta escrita de soporte PPI aportada por el usuario en esta conversación.

## 1. Hallazgos confirmados por soporte PPI

### 1.1 Naming y plazo

- El número incluido en el ticker identifica la cantidad de días de la caución.
- Ejemplo explícito: `PESOS1` corresponde a una caución por 1 día.
- Los días son **días corridos**.
- El cómputo comienza **el mismo día en que se carga la orden**.
- Ejemplo de soporte: una caución a 3 días cargada un viernes queda caucionada viernes, sábado y domingo, y el lunes a primera hora el saldo ya está líquido.
- Fines de semana y feriados participan en el conteo como días corridos.
- PPI indicó que la fecha/hora de vencimiento no viene explícita en Client API y debe derivarse del ticker y la fecha de concertación/carga.

### 1.2 Cantidad / step

Para cauciones en ARS:
- valores enteros son válidos;
- ejemplo: ARS 100001 es válido;
- fracciones no son válidas;
- ejemplo: ARS 100000,50 no es válido;
- incremento/step: ARS 1.

Para cauciones en USD:
- incremento/step: USD 1;
- tratar cantidad como entero, no fraccionario.

### 1.3 Convención de interés

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

## 2. Implicancias para POROTA

Estos puntos dejan de estar `NOT_PROVEN` a nivel contractual:

- `CAUCION_TERM_DAYS_SOURCE=TICKER_SUFFIX`
- `CAUCION_DAY_COUNT=ACTUAL_365`
- `CAUCION_TERM_CALENDAR_DAYS=TRUE`
- `CAUCION_START_DATE=ORDER_LOAD_DATE`
- `CAUCION_QUANTITY_INTEGER_ONLY=TRUE`
- `CAUCION_ARS_QUANTITY_STEP=1`
- `CAUCION_USD_QUANTITY_STEP=1`
- `CAUCION_API_EXPLICIT_MATURITY_FIELD=NO`

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

Aunque la respuesta cierra varios campos críticos, `CAUCIONES_AUTO_PLACEMENT=false` debe mantenerse.

Todavía falta demostrar o reconciliar:

1. API execution support real con credenciales válidas;
2. Budget/Confirm/Cancel semantics en sandbox, sin usar producción para probar;
3. fees/comisiones/impuestos exactos por caución;
4. price/rate rounding y precisión contractual de TNA;
5. profundidad/paginación completa del libro;
6. saldo disponible / settlement exacto, especialmente semántica de DOLAR;
7. timezone/cutoff exactos;
8. reglas de disponibilidad si el evento de liquidación cae fuera de ventana operativa;
9. DOM/API/Contract Evidence reconciliation;
10. tests de casos límite: viernes, fin de mes, fin de año, feriados, USD y ARS.

## 5. Cambio de semáforo recomendado

Antes:
- Cauciones: YELLOW / no READY_PAPER, con day-count, quantity step y plazo contractual pendientes.

Ahora:
- naming/plazo: GREEN por evidencia de soporte;
- días corridos: GREEN;
- Actual/365: GREEN;
- quantity integer + step 1 ARS/USD: GREEN;
- maturity explicit API field: GREEN como `NO`, derivación requerida;
- timezone/cutoff: YELLOW;
- fees: YELLOW;
- API execution: RED/NOT_PROVEN mientras PPI Production API auth siga bloqueada;
- READY_PAPER: NO;
- AUTO_PLACEMENT: FALSE.

## 6. Seguridad

- No realizar pruebas de orden en producción.
- Sandbox es el ambiente recomendado por PPI para estudiar requests/responses de ejecución.
- Mantener `real_orders_sent=0`.
