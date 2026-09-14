# POROTA TRADING — REQUISITOS DE OPERABILIDAD Y MAPA DE FUENTES POR FAMILIA — 2026-09-14

## Estado

**INVESTIGACIÓN EXHAUSTIVA INICIADA / EVIDENCIA READ-ONLY / NO HABILITA ÓRDENES REALES.**

Este documento responde una pregunta concreta: **qué necesita Porota para poder estudiar y operar profesionalmente cada familia, qué fuente aporta cada dato y qué falta antes de declarar `READY_PAPER_PPI`.**

No reduce el alcance a Acciones + CEDEAR. La política sigue siendo abarcar todas las familias posibles soportadas por el universo canónico, sin fabricar readiness donde falta evidencia.

## Invariantes

- `PRODUCTION_PAPER` durante la investigación.
- `REAL_ORDERS_SENT=0`.
- No se ejecutaron `place_order`, `cancel_order`, `place_caucion`, `subscribe_fci`, `redeem_fci` ni equivalentes.
- PPI sigue siendo autoridad del **contrato de ejecución PPI**.
- IOL puede ser autoridad/primaria de analítica o market data cuando su cobertura es mejor, pero no autoriza por sí solo una orden PPI.
- Datos regulatorios/estables pueden venir de BYMA, BCRA, Tesoro, CNV o emisor; no sustituyen disponibilidad/condiciones activas del broker.
- No inferir `price_tick` ni `quantity_step` a partir de decimales.
- Toda decisión futura debe conservar `provider`, `provider_instrument_id`, `market`, `currency`, `settlement`, `source_timestamp`, `ingest_timestamp`, `freshness`, `quality_flags` y `provenance` por campo.

---

# 1. Definición canónica de “operable”

Un instrumento no debe pasar a `READY_PAPER_PPI` hasta reunir cinco capas independientes:

1. **IDENTITY_READY**
   - familia;
   - especie/ticker exacto;
   - provider ID;
   - mercado;
   - moneda;
   - variantes ARS/D/C cuando existan;
   - subyacente/emisor cuando corresponda.
2. **MARKET_DATA_READY**
   - última cotización;
   - bid/ask y profundidad cuando corresponda;
   - timestamp/freshness;
   - volumen;
   - histórico suficiente;
   - calendario aplicable.
3. **ANALYTICS_READY**
   - indicadores/riesgo según familia;
   - renta fija: TIR, duration, flujos, paridad, accrued, etc.;
   - opciones: IV/Greeks y riesgo;
   - cauciones: tasa efectiva, plazo, costo neto;
   - FCI: VCP/NAV y reglas de rescate.
4. **CONTRACT_READY_PPI**
   - PPI confirma especie habilitada/AVAILABLE;
   - market/currency;
   - settlement;
   - unidad/lote/nominal;
   - mínimo;
   - quantity step;
   - price tick;
   - horario/cut-off;
   - fees/restricciones;
   - requisitos especiales (margen, garantía, DDJJ, etc.).
5. **RISK_READY**
   - tamaño máximo;
   - liquidez/spread;
   - stale gate;
   - provider-divergence gate;
   - reglas específicas de vencimiento, ejercicio, marginación, calendario, corporate actions o cashflow.

Estados recomendados:

- `DISCOVERED`
- `MARKET_DATA_READY`
- `ANALYTICS_READY`
- `CONTRACT_READY_PPI`
- `RISK_READY`
- `READY_PAPER_PPI`
- `HOLD_DATA`
- `HOLD_CONTRACT`
- `HOLD_RISK`
- `PROVIDER_DIVERGENCE`
- `NOT_CURRENTLY_OPERABLE`

---

# 2. Jerarquía de fuentes

## 2.1 Ejecución PPI

**Autoridad primaria:** PPI API / PPI Web autenticada.

Campos que no deben ser sustituidos silenciosamente por IOL:

- disponibilidad actual;
- identidad exacta operable en PPI;
- settlement habilitado;
- moneda/mercado del flujo PPI;
- mínimos/steps/ticks requeridos por PPI;
- fees y restricciones del broker;
- margen/garantía/cut-off;
- configuración de orden.

Evidencia actual confirmada en PPI Web autenticada:

- `/api/Ordenes/InstrumentosOperables`
- `/api/Ordenes/CaucionesOperables`
- `/api/Ordenes/ConfiguracionOperatoriaSimplificada`

Run `34865452877` confirmó AL30 explícito y 120 filas de cauciones. PPI entregó identidad, moneda, comisiones, auction flag y decimales para AL30, pero **`quantity_step` y `price_tick` siguen sin evidencia semántica explícita**.

## 2.2 Market data / analytics

**IOL API** queda aprobada como segunda fuente estructurada read-only para:

- instrument info;
- quote/order book;
- daily history;
- intraday trades;
- fixed-income analytics;
- caucion rates;
- options chain + Greeks;
- corporate events;
- FCI listing.

## 2.3 Fuentes oficiales estables

- BYMA: especificaciones de productos, CEDEAR ratios/mercado, futuros, calendarios y reglas de mercado.
- Tesoro / Argentina.gob.ar: condiciones de Letras y Bonos soberanos, licitaciones y reaperturas.
- BCRA: familias históricas BCRA (LEBAC/LELIQ/Notas), referencias monetarias y series oficiales.
- CNV / emisor / administradora: ON, FCI, hechos relevantes, reglamentos y términos legales.

Regla: **oficial estable valida el instrumento; PPI autoriza el contrato de ejecución PPI.**

---

# 3. Ejemplos representativos por familia

## 3.1 ACCIONES — ejemplo GGAL

### Evidencia IOL actual

`GGAL`, BCBA:

- tipo: `ACCIONES`;
- moneda: ARS;
- lote IOL: 1;
- settlement informado: T1;
- símbolos relacionados: `GGAL`, `GGALD`;
- histórico diario IOL 2026-09-01..2026-09-14: OHLCV disponible;
- intradiario IOL 2026-09-14: secuencia timestamp/precio/volumen disponible.

### Para `READY_PAPER_PPI` falta exigir

- PPI `instrument_id` exacto;
- `AVAILABLE` actual;
- market/currency;
- settlement exacto PPI;
- mínimo/lote/quantity step;
- price tick;
- horario/calendario BYMA;
- fees;
- stale/spread/liquidity gate.

### Fuente de cada bloque

- identidad/contrato de orden: PPI;
- quote/order book/histórico/intradiario: PPI + IOL cross-check;
- calendario/reglas de mercado: BYMA;
- corporate events: emisor/BYMA/CNV, IOL como convenience source.

### Veredicto de ejemplo

`ANALYTICS_READY_CANDIDATE`, pero `READY_PAPER_PPI` sólo cuando el contrato PPI quede explícito.

---

## 3.2 CEDEAR — ejemplo AAPL BCBA

### Evidencia IOL actual

`AAPL`, BCBA:

- tipo: `CEDEARS`;
- ARS;
- lote 1;
- T1;
- relacionados: `AAPL`, `AAPLD`, `AAPLC`;
- histórico diario IOL probado 2026-09-01..2026-09-14.

### Requisitos adicionales a una acción local

- ratio de conversión CEDEAR/acción;
- calendario dual Argentina + mercado de origen;
- feriados del mercado de origen;
- dividendos/corporate actions del subyacente;
- mapping ARS/D/C;
- política ante mercado USA cerrado pero BYMA abierto;
- CCL/MEP restrictions cuando correspondan.

### Fuentes

- ejecución PPI: PPI;
- market data local: PPI + IOL;
- ratio/listado: BYMA/emisor del CEDEAR;
- subyacente/origin calendar: mercado de origen + Porota canonical calendar;
- corporate actions: emisor/origin market, IOL como cross-check.

### Veredicto

Es familia prioritaria PAPER, pero strict readiness requiere contrato PPI por especie y doble calendario.

---

## 3.3 ETF / CEDEAR de ETF — ejemplo IVV BCBA

### Evidencia IOL actual

`IVV`, BCBA:

- descripción: `CEDEAR ISHARES CORE S&P 500 ETF`;
- tipo IOL: `CEDEARS`;
- ARS;
- lote 1;
- T1;
- relacionados `IVV`, `IVVD`, `IVVC`;
- quote 2026-09-14 ~14:21 ART: último 1769 ARS, bid 1769, ask 1770;
- histórico diario 2026-09-01..2026-09-14 disponible.

BYMA confirmó en 2026 nuevas altas de CEDEARs de ETF, incluyendo IVV.

### Requisitos especiales

- distinguir `CEDEAR_ETF` de acción/CEDEAR común;
- ratio;
- subyacente ETF;
- leverage/inverse flag cuando aplique;
- calendario origen;
- tracking/structural risk;
- contrato PPI.

### Fuentes

- listado/ratio/estructura: BYMA + emisor;
- quote/history: IOL + PPI;
- operabilidad PPI: PPI.

---

## 3.4 ACCIONES USA — ejemplo AAPL NASDAQ

### Evidencia IOL actual

`AAPL`, NASDAQ:

- tipo `ACCIONES`;
- USD;
- lote 1;
- T1;
- quote 2026-09-14 ~14:21 ART: 334.27 USD, bid 334.20 / ask 334.27.

### Para operar por PPI

Debe existir una identidad PPI de Acción USA distinta del CEDEAR local y un contrato específico:

- venue;
- currency USD;
- settlement;
- horario USA;
- feriados USA;
- fees/impuestos;
- minimum/step/tick;
- FX/cash availability rules;
- restricciones regulatorias del broker.

No mezclar `AAPL NASDAQ` con `AAPL BCBA` por el ticker textual.

---

## 3.5 BONOS — ejemplo AL30

### Evidencia PPI actual

AL30 apareció explícitamente en `InstrumentosOperables` con:

- `instrument_id`;
- ticker;
- currency;
- provider name;
- comisiones;
- market fee rate;
- auction flag;
- quantity decimal places;
- price decimal places;
- `quantity_step=None`;
- `price_tick=None`.

### Evidencia IOL actual

`AL30`, BCBA:

- tipo `TIT. PUBLICOS`;
- ARS;
- lote 100;
- T1;
- `AL30/AL30D/AL30C`;
- quote/order book;
- histórico diario;
- clean/dirty price;
- accrued interest;
- technical value;
- parity;
- TIR/TEM;
- Macaulay/modified duration;
- issue/maturity/settlement dates;
- cashflow de intereses/amortización/residual.

### Falta contractual PPI

- settlement exacto validado desde PPI;
- semántica de nominal/cantidad;
- mínimo;
- quantity step si existe;
- price tick si existe;
- horario y disponibilidad actual;
- cualquier restricción especial.

### Arquitectura correcta

- **PPI:** contrato de ejecución.
- **IOL:** analítica de renta fija y market data.
- **Tesoro:** términos jurídicos/económicos estables.

AL30 es el caso patrón para Bonos.

---

## 3.6 LETRAS — ejemplo S30O6

### Evidencia IOL actual

`S30O6`:

- descripción: `Letras Del Tesoro Cap $ V 30/10/2026`;
- tipo `Letras`;
- ARS;
- lote 100;
- T1;
- quote 2026-09-14 ~14:20 ART: último 131.609; bid 131.594 / ask 131.610;
- histórico diario 2026-09-01..2026-09-14 disponible;
- fixed-income analytics disponible.

Analytics observadas:

- TIR aproximada: 25.6518% anual;
- TEM aproximada: 1.9211%;
- tasa del instrumento reportada: 2.55% mensual;
- maturity: 2026-10-30;
- remaining days: 45 en el snapshot;
- nominal analítico: 100.

Tesoro oficial confirma que S30O6 es una LECAP capitalizable en pesos con vencimiento 30/10/2026.

### Subcaso importante: D30O6

`D30O6` IOL:

- Letra dólar-linked cero cupón 30/10/2026;
- ARS;
- lote 100;
- T1;
- quote/order book disponible;
- **`get_fixed_income_analytics` devolvió `not_found`**.

Esto demuestra por qué Porota necesita fuentes múltiples: en D30O6 el market data IOL existe, pero la analítica específica debe venir del Tesoro/otra fuente canónica o calcularse en Porota con los términos oficiales.

### Para READY_PPI

Mismos contract gates que Bonos: identity PPI, settlement, nominal/min/step/tick, disponibilidad, horarios, fees.

---

## 3.7 ON — ejemplo YMCJO

### Evidencia IOL actual

`YMCJO`, YPF Clase XVIII venc. 30/09/2033:

- tipo `Obligaciones Negociables`;
- ARS;
- lote 100;
- T1;
- relacionados: `YMCJO`, `YMCJD`, `YMCJC`;
- quote 2026-09-14 ~14:13 ART: último 161620, bid 161580 / ask 161750;
- histórico diario 2026-09-01..2026-09-14 disponible;
- fixed-income analytics disponible.

Analytics observadas:

- clean/dirty price;
- accrued interest;
- technical value;
- parity;
- TIR ~6.6459%;
- Macaulay duration ~4.6845;
- modified duration ~4.3926;
- maturity 2033-09-30;
- cashflow de cupones y amortizaciones hasta vencimiento.

### Fuentes

- contrato PPI: PPI;
- quote/history/fixed analytics: IOL + PPI cross-check;
- términos jurídicos, covenants, emisión, rating/hechos relevantes: CNV/emisor/calificadora.

### Falta PPI

- exact PPI identity/availability;
- settlement;
- nominal/min/step/tick;
- fees/horarios/restricciones.

YMCJO queda como ejemplo patrón de ON.

---

## 3.8 CAUCIONES — ejemplo 1 día ARS colocadora

### Evidencia PPI actual

`CaucionesOperables`: 120 filas con al menos:

- `dias`;
- `descripcion`;
- `pildoraDescripcion`;
- `cantidadDecimales`;
- `cantidadDecimalesPrecio`.

### Evidencia IOL actual, snapshot 2026-09-14

Colocadora ARS:

- 1 día: 21.5, venc. 2026-09-15, mínimo 100000 ARS;
- 2 días: 21.0, mínimo 100000;
- 3 días: 21.0, mínimo 100000.

Colocadora USD:

- 1 día: 0.992, mínimo USD 100;
- 2 días: 0.75;
- 3 días: 0.65.

Tomadora ARS:

- 1 día: 21.6, mínimo 1000 ARS;
- 2 días: 21.4;
- 3 días: 21.1.

Tomadora USD:

- 1 día: 1.0, mínimo USD 100;
- 2 días: 1.09;
- 3 días: 1.7.

Estas tasas son **time-sensitive** y sólo sirven como evidencia de capacidad/campo, no como parámetros permanentes.

### Falta para PPI

- mapear exactamente moneda/tipo/plazo de cada fila;
- tasa/precio semántico PPI;
- mínimo/máximo PPI;
- amount step;
- settlement;
- garantía/aforo tomadora;
- disponibilidad;
- horarios;
- fees/costo neto.

### Regla

IOL puede alimentar analytics y cross-check; PPI debe autorizar el contrato PPI.

---

## 3.9 OPCIONES — ejemplo GFGC7000OC

### Evidencia IOL actual, chain GGAL venc. 2026-10-16

Ejemplo `GFGC7000OC`:

- underlying: GGAL;
- call;
- strike 7000;
- vencimiento 2026-10-16 15:30;
- spot de underlying en snapshot: 6860;
- bid 365.0;
- ask 368.571;
- IV 0.4614;
- theoretical price 368.46;
- delta 0.5218;
- gamma 0.000424;
- theta -7.41;
- vega 8.1118;
- rho 2.822;
- volumen 455;
- `is_stale=false`.

### Para operar profesionalmente faltan

- contrato PPI de la opción exacta;
- multiplier/contract size;
- lot/quantity step;
- price tick;
- settlement;
- exercise style;
- assignment/exercise rules;
- margen para short;
- vencimiento y auto-close;
- spread/liquidity gate;
- IV freshness;
- límites delta/gamma/vega;
- stress tests;
- calendario de vencimiento.

### Estado

`IN_PROGRESS_RESEARCH_ONLY`. Opciones permanecen fuera del objetivo inmediato de READY, reversiblemente.

---

## 3.10 FUTUROS — ejemplo Futuro de GGAL

BYMA publica actualmente futuros de renta variable sobre:

- GGAL;
- MELI;
- SPY.

Especificación oficial BYMA observada:

- moneda de negociación: ARS;
- tamaño de contrato: 100 títulos para futuros de acciones;
- tamaño de contrato: 10 títulos para futuros de CEDEARs;
- cantidad mínima: 1 contrato;
- alteración mínima de precio: sigue al subyacente;
- vencimientos: mes en curso + dos meses;
- último día: último día hábil del mes;
- mark-to-market diario y garantías.

Fuente pública: `https://www.byma.com.ar/productos/productos-financieros/futuros-de-renta-variable`.

### Para READY_PPI

PPI debe confirmar:

- contrato exacto/listed symbol;
- disponibilidad;
- margin requirement;
- collateral/aforo;
- settlement;
- trading hours;
- fees;
- price tick/quantity step si el broker los restringe;
- expiration handling.

IOL connector actual no expone un reader específico de futuros; no se debe inventar cobertura IOL.

### Estado

`HOLD_CONTRACT + HOLD_RISK` hasta obtener PPI + margin model.

---

## 3.11 FCI — ejemplo IOLDOLD / IOL Dólar Ahorro Plus

IOL read-only listing devolvió:

- asset `IOLDOLD`;
- descripción `IOL Dólar Ahorro Plus`;
- tipo `renta_fija_dolares`;
- USD;
- `operable=true` en IOL.

También apareció `ADCGLOA` / Adcap Renta Dólar.

### Requisitos para FCI PPI

- PPI fund ID exacto;
- disponibilidad suscripción/rescate;
- moneda;
- mínimo de suscripción;
- mínimo de rescate/saldo mínimo;
- cut-off;
- settlement de rescate;
- VCP/NAV y timestamp;
- fees;
- reglamento/prospecto;
- feriados/cut-off especiales.

FCI no es intradía como una acción; requiere un state machine diferente.

### Estado

`DEFERRED` para objetivo intradía inmediato, **sin borrar datos ni soporte**.

---

## 3.12 FCI EXTERIOR

La familia existe en el alcance histórico/contractual PPI, pero **en esta ronda no existe evidencia suficiente de una especie actual concreta** para usarla como ejemplo sin inventar.

### Ejemplo de prueba obligatoria

Seleccionar el **primer FCI_EXTERIOR que PPI marque AVAILABLE actualmente** y capturar:

- fund/provider ID;
- currency;
- minimum;
- subscription cut-off;
- redemption cut-off;
- redemption settlement;
- VCP/NAV;
- fees;
- holidays;
- reglamento/prospecto.

### Estado

`DEFERRED / NEEDS_CURRENT_PPI_EXAMPLE`.

No se fabricará un ticker sólo para completar una tabla.

---

## 3.13 LICITACIONES — ejemplo D30O6 reapertura

Una licitación no debe modelarse como una orden secundaria normal.

Ejemplo oficial reciente: reapertura de `D30O6`, Letra dólar-linked cero cupón con vencimiento 30/10/2026, incluida en licitación del Tesoro de septiembre de 2026.

Fuente oficial: Argentina.gob.ar / Secretaría de Finanzas.

### Datos necesarios

- issuer;
- instrumento;
- moneda de denominación/suscripción;
- fecha de licitación;
- fecha de liquidación;
- parámetro a licitar (precio/tasa);
- minimum VNO;
- monto máximo;
- canal PPI;
- ventana/cut-off PPI;
- reglas de adjudicación;
- fees;
- resultado/adjudicación.

### Estado canónico propuesto

No usar `READY_PAPER_PPI` secundario. Crear flujo separado:

- `PRIMARY_OFFER_DISCOVERED`
- `PRIMARY_OFFER_CONTRACT_READY_PPI`
- `PRIMARY_OFFER_ANALYTICS_READY`
- `PRIMARY_OFFER_PAPER_READY`

No ejecutar licitación real durante esta investigación.

---

## 3.14 ÍNDICES — ejemplo S&P Merval

Un índice es **market data/benchmark**, no una especie que Porota deba enviar directamente como orden salvo que exista un derivado negociable separado.

### Datos necesarios

- index ID;
- provider;
- constituents/weights cuando se usen;
- value/timestamp;
- calendar;
- methodology/version.

### Uso

- régimen de mercado;
- benchmark;
- beta/correlación;
- risk factor;
- no `READY_PPI` directo.

Estado: `ANALYTICS_ONLY`.

---

## 3.15 LEBAC / NOBAC — familia legacy

BCRA publica historial de subastas de LEBAC desde marzo 2002 hasta diciembre 2018. En esta investigación **no se encontró evidencia oficial actual 2026 de una LEBAC vigente negociable equivalente**.

Fuente BCRA: `https://www.bcra.gob.ar/operaciones-pases-subastas-letras/`.

### Regla de Porota

- conservar familia/datos históricos si existen;
- no marcar READY por pertenecer al catálogo histórico;
- exigir PPI `AVAILABLE` actual + instrumento vigente + contrato completo.

Estado provisional:

`NOT_CURRENTLY_OPERABLE_UNLESS_PPI_PROVES_AVAILABLE`.

NOBAC debe tratarse con el mismo criterio: no inventar vigencia; exigir evidencia actual del BCRA/mercado/PPI.

---

# 4. Matriz resumida de autoridad por campo

| Campo | Fuente primaria | Cross-check | ¿Obligatorio para READY_PPI? |
|---|---|---|---|
| provider instrument ID | PPI | IOL alias map | Sí |
| availability | PPI | ninguno | Sí |
| family | PPI canonical | IOL/BYMA | Sí |
| market | PPI | IOL/BYMA | Sí |
| currency | PPI | IOL | Sí |
| settlement | PPI | IOL | Sí |
| lot/nominal/minimum | PPI | IOL/official | Sí |
| quantity step | PPI explícito | official equivalent | Sí cuando aplica |
| price tick | PPI explícito | BYMA/official equivalent | Sí cuando aplica |
| trading hours | PPI + market | BYMA/origin market | Sí |
| quote/order book | freshest valid PPI/IOL | otro provider | No contractual, sí market-data gate |
| daily history | PPI + IOL | reconciled | Analytics |
| intraday | PPI/IOL | reconciled | Analytics/strategy |
| fees broker | PPI | tariff docs | Sí |
| TIR/duration/parity | IOL | Porota/PPI/official | No contractual |
| bond cashflow | IOL + issuer/Treasury | PPI DatosTecnicos | Analytics/validation |
| CEDEAR ratio | BYMA/emisor | PPI/IOL metadata | Sí para correcta unidad económica |
| corporate events | issuer/market | IOL | Risk |
| option Greeks | IOL | Porota recompute | Risk |
| option multiplier | PPI/BYMA | official | Sí |
| future contract size | BYMA | PPI | Sí |
| margin/guarantee | PPI | BYMA/A3 | Sí |
| caucion rate | PPI/IOL | dual-source | Analytics |
| caucion minimum/step | PPI | IOL | Sí |
| FCI cut-off/settlement | PPI/administradora | CNV | Sí |
| auction terms | Tesoro/emisor | PPI | Sí para primary flow |

---

# 5. Hallazgos que cambian el diseño

1. **PPI no fracasó.** Los endpoints contractuales actuales existen y ya entregaron AL30 + 120 cauciones.
2. **IOL cubre mucho más que un simple fallback de precios.** Ya demostró:
   - Acción: metadata, history, intraday;
   - CEDEAR: metadata/history;
   - CEDEAR ETF: metadata/quote/history;
   - Acción USA: metadata/quote;
   - Bono: quote/history/fixed-income analytics;
   - Letra S30O6: quote/history/fixed-income analytics;
   - ON YMCJO: quote/history/fixed-income analytics;
   - Cauciones: ARS/USD, colocadora/tomadora, rates/minimum/due dates;
   - Opciones: chain + IV + Greeks;
   - FCI: catálogo estructurado.
3. **IOL no es universal.** D30O6 tuvo market data pero no fixed-income analytics; futuros no tienen reader específico en el connector actual.
4. **Fuentes oficiales siguen siendo necesarias.** Ejemplos:
   - Tesoro para términos de S30O6/D30O6;
   - BYMA para futuros y CEDEAR/ETF;
   - BCRA para LEBAC legacy;
   - CNV/emisor para ON/FCI.
5. **El cuello de botella real es ahora el contrato PPI**, sobre todo step/tick/minimum/settlement/availability por familia.

---

# 6. Diseño de implementación recomendado

## 6.1 Adapters

### `PPIContractProvider`

Read-only durante research:

- `get_operable_identity()`
- `get_execution_config()`
- `get_settlement_terms()`
- `get_minimums_steps_ticks()`
- `get_fees()`
- `get_availability()`

### `IOLReadOnlyProvider`

Sin métodos mutativos:

- `get_instrument_info()`
- `get_quote()`
- `get_order_book()`
- `get_daily_history()`
- `get_intraday_history()`
- `get_fixed_income_analytics()`
- `get_caucion_rates()`
- `get_options_chain()`
- `get_corporate_events()`
- `list_fci()`

### `OfficialTermsProvider`

- Treasury/BCRA/BYMA/CNV/emisor terms;
- immutable/stable metadata;
- calendar/contract specs.

## 6.2 Canonical record

Cada campo debe guardar:

```text
canonical_field
value
source_provider
source_instrument_id
source_endpoint_or_document
effective_at
observed_at
freshness_seconds
quality
conflict_state
```

## 6.3 Reconciliation rules

- No unir por ticker textual solamente.
- Matching mínimo: family + market + currency + provider identity + settlement.
- ARS/D/C son identidades de negociación distintas aunque pertenezcan al mismo activo económico.
- BCBA AAPL != NASDAQ AAPL.
- CEDEAR ETF != acción local.
- D30O6 market data disponible no implica fixed-analytics disponible.

---

# 7. Stopper matrix al cierre de esta ronda

| Familia | Market data | Analytics | Contract PPI | Riesgo específico | Estado |
|---|---|---|---|---|---|
| ACCIONES | fuerte | fuerte | incompleto strict | estándar | CORE PAPER / contract hardening |
| CEDEAR | fuerte | fuerte | incompleto strict | doble calendario/ratio | CORE PAPER / contract hardening |
| ETF/CEDEAR ETF | fuerte en IVV | suficiente | pendiente | estructura/ratio | HOLD_CONTRACT |
| ACCIONES USA | IOL confirmado AAPL | suficiente | PPI pendiente | calendar/FX | HOLD_CONTRACT |
| BONOS | fuerte | fuerte IOL | incompleto PPI | FI risk | HOLD_CONTRACT |
| LETRAS | fuerte S30O6 | fuerte S30O6; parcial D30O6 | PPI pendiente | instrument-specific | HOLD_CONTRACT |
| ON | fuerte YMCJO | fuerte YMCJO | PPI pendiente | credit/liquidity | HOLD_CONTRACT |
| CAUCIONES | fuerte dual-source parcial | tasa/min IOL | semántica PPI incompleta | guarantee/aforo | HOLD_CONTRACT |
| OPCIONES | chain/Greeks fuerte IOL | fuerte | PPI pendiente | alto/específico | RESEARCH_ONLY |
| FUTUROS | official terms | partial | PPI pendiente | margin/MTM | HOLD_CONTRACT_RISK |
| FCI | catálogo IOL | específico | PPI pendiente | cut-off/VCP | DEFERRED |
| FCI_EXTERIOR | insuficiente | insuficiente | PPI pendiente | specific | DEFERRED |
| LICITACIONES | official terms | calculable | PPI primary-flow pending | allocation/cut-off | SEPARATE_FLOW |
| INDICES | official/market | analytics only | N/A directo | benchmark | ANALYTICS_ONLY |
| LEBAC/NOBAC | legacy | historical | no evidencia current | N/A | NOT_CURRENT unless PPI proves |

---

# 8. Próximas tareas obligatorias

## P0

1. Terminar AL30 PPI: settlement + nominal/min + quantity step + price tick + availability/horario.
2. Terminar Cauciones PPI: moneda/tipo/plazo + rate semantics + min/max/step + settlement + guarantee/aforo + horario.
3. Repetir contract-capture PPI por una muestra de:
   - GGAL;
   - AAPL CEDEAR;
   - IVV CEDEAR ETF;
   - S30O6;
   - YMCJO.
4. Determinar si `price_tick`/`quantity_step` vienen de PPI o deben tomarse de una regla oficial semánticamente equivalente.

## P1

5. Codificar `IOLReadOnlyProvider` sin métodos de mutación.
6. Construir `provider_identity_map`.
7. Implementar `source_provenance` por campo.
8. Implementar `PROVIDER_DIVERGENCE`.
9. Comparar PPI vs IOL OHLCV para GGAL/AAPL/AL30/S30O6/YMCJO.
10. Medir profundidad histórica y retención intradiaria real de IOL.

## P2

11. Investigar futuros PPI con ejemplo GGAL.
12. Mantener opciones como research-only hasta risk module.
13. Mantener FCI/FCI exterior fuera del intradía pero documentados.
14. No iniciar scraping IOL masivo salvo campo crítico demostrado como API-missing.

---

# 9. Conclusión

La estrategia correcta no es elegir entre PPI o IOL. Es:

> **PPI Contract Authority + IOL Structured Market/Analytics + Official Terms + Porota Reconciliation/Risk.**

Con esta arquitectura, Porota puede estudiar profesionalmente muchas más familias sin quedar bloqueada por una API única, pero sólo declarará `READY_PAPER_PPI` cuando el contrato PPI necesario para construir una orden válida esté explícitamente cerrado.

No se ejecutó ninguna orden real durante esta investigación.
