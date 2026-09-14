# POROTA TRADING — MATRIZ INICIAL DE COBERTURA PPI vs IOL — 2026-09-14

Estado: **INICIAL / EN INVESTIGACIÓN**

Esta matriz no habilita instrumentos. Su propósito es separar claramente:

- datos de mercado;
- analítica;
- datos contractuales;
- reglas de ejecución del broker.

Leyenda:

- `YES`: evidencia actual suficiente de que la fuente expone el concepto.
- `PARTIAL`: expone parte del concepto, falta semántica/cobertura.
- `UNKNOWN`: aún no investigado de forma suficiente.
- `N/A`: no corresponde.
- `BROKER-AUTH`: obligatorio desde el broker de ejecución.

## Matriz por familia

| Familia | Campo / capacidad | PPI API/Web | IOL API | Fuente preferida para análisis | Obligatorio para READY_PPI | Estado / comentario |
|---|---|---:|---:|---|---:|---|
| ACCIONES | identidad/ticker | YES | YES | PPI + IOL cross-check | YES | PPI ya integrado; IOL probado con GGAL |
| ACCIONES | quote/order book | YES/PARTIAL según canal | YES | dual-source | NO | investigar tolerancia/freshness |
| ACCIONES | histórico diario | YES | YES | PPI primary + IOL cross-check | NO | validar profundidad/huecos IOL |
| ACCIONES | intradiario | YES/PARTIAL | YES | dual-source | NO | validar retención IOL |
| ACCIONES | lot/min/step/tick | PARTIAL | PARTIAL | N/A | YES | no inferir step/tick |
| CEDEAR | identidad + ARS/D/C | YES/PARTIAL | YES | dual-source | YES | IOL probado con AAPL/AAPLD/AAPLC |
| CEDEAR | quote/order book | YES/PARTIAL | YES | dual-source | NO | pendiente reconciliación por plazo/moneda |
| CEDEAR | histórico | YES | YES | dual-source | NO | 7 CEDEAR siguen con HISTORY_GAP en PPI histórico |
| CEDEAR | calendario US/ARG | PARTIAL | UNKNOWN | Porota canonical calendar | YES para elegibilidad | investigar doble calendario y eventos |
| BONOS | identidad operable | YES | YES | PPI para ejecución, IOL para cross-check | YES | AL30 explícito en PPI + IOL |
| BONOS | quote/order book | YES/PARTIAL | YES | dual-source | NO | IOL probado AL30 |
| BONOS | settlement | PARTIAL | YES | PPI authoritative | YES | IOL reporta T1 para AL30; confirmar PPI exacto |
| BONOS | lot/unidad | PARTIAL | YES | PPI authoritative para ejecución | YES | IOL AL30 units_per_lot=100 |
| BONOS | price/quantity decimals | YES | UNKNOWN | PPI | PARTIAL | no equivalen a tick/step |
| BONOS | quantity step | UNKNOWN | UNKNOWN | PPI | YES si broker lo exige | blocker actual |
| BONOS | price tick | UNKNOWN | UNKNOWN | PPI | YES si broker lo exige | blocker actual |
| BONOS | TIR/TEM | UNKNOWN/PARTIAL | YES | IOL analytics | NO | IOL AL30 verificado |
| BONOS | duration | UNKNOWN/PARTIAL | YES | IOL analytics | NO | IOL AL30 verificado |
| BONOS | clean/dirty price | UNKNOWN/PARTIAL | YES | IOL analytics | NO | IOL AL30 verificado |
| BONOS | technical value/parity | UNKNOWN/PARTIAL | YES | IOL analytics | NO | IOL AL30 verificado |
| BONOS | cashflow/amortization | PPI DatosTecnicos pendiente | YES | IOL analytics, PPI cross-check si disponible | NO para análisis; sí términos básicos para validación | IOL AL30 verificado |
| BONOS | maturity/issue dates | PPI DatosTecnicos pendiente | YES | dual-source | NO | IOL AL30 verificado |
| LETRAS | identidad | PARTIAL/UNKNOWN | UNKNOWN | TBD | YES | muestra representativa pendiente |
| LETRAS | quote/history | PARTIAL/UNKNOWN | capability exists, specific sample pending | TBD | NO | investigar ticker real |
| LETRAS | analytics renta fija | UNKNOWN | likely YES, sample pending | IOL candidate | NO | requiere prueba concreta |
| ON | identidad | PARTIAL | UNKNOWN | PPI + IOL | YES | PPI route /Operar/Ons observado; muestra IOL pendiente |
| ON | quote/history | PARTIAL | capability exists, sample pending | dual-source | NO | investigar muestra líquida |
| ON | fixed-income analytics | UNKNOWN | likely YES, sample pending | IOL candidate | NO | pendiente prueba concreta |
| CAUCIONES | especies/plazos operables | YES | YES/PARTIAL | PPI contract + IOL analytics | YES | PPI 120 rows; IOL 1/2/3 day rates |
| CAUCIONES | tasa | PARTIAL | YES | IOL + PPI cross-check | NO | identificar semántica PPI exacta |
| CAUCIONES | mínimo | UNKNOWN/PARTIAL | YES | PPI authoritative for PPI execution | YES | IOL ARS min 100000 observado |
| CAUCIONES | amount/quantity step | UNKNOWN | UNKNOWN | PPI | YES | blocker |
| CAUCIONES | garantía/aforo tomadora | UNKNOWN | PARTIAL capability (guarantee assets) | broker-specific | YES para tomadora | investigar sin mutación |
| CAUCIONES | due date | PARTIAL | YES | dual-source | YES | validar equivalencia calendario |
| OPCIONES | cadena strikes/vencimientos | UNKNOWN/PARTIAL | YES | IOL analytics | NO | IOL GGAL verificado |
| OPCIONES | bid/ask | UNKNOWN/PARTIAL | YES | IOL analytics | NO | IOL GGAL verificado |
| OPCIONES | IV/Greeks | UNKNOWN | YES | IOL analytics | NO | IOL GGAL verificado, null cuando no hay mercado suficiente |
| OPCIONES | multiplier/contract size | UNKNOWN | UNKNOWN | PPI contract / market rules | YES | blocker antes de PAPER ready |
| OPCIONES | exercise/assignment rules | UNKNOWN | UNKNOWN | official market + broker | YES | investigación separada |
| ETF | identidad/quote/history | PARTIAL/UNKNOWN | capability exists | dual-source | YES | familia específica pendiente |
| FUTUROS | identidad/quote/history | PARTIAL/UNKNOWN | IOL docs say market coverage exists, connector-specific tool not yet identified | TBD | YES | fuera de prioridad inmediata |
| FCI | catálogo | PPI data exists but deferred | YES | provider-specific | N/A immediate | fuera de intradía; no borrar |

## Matriz de autoridad por concepto

| Concepto | Autoridad primaria propuesta | Fallback / cross-check | Regla |
|---|---|---|---|
| `broker_operable_identity` | PPI | ninguno para READY_PPI | IOL no autoriza PPI |
| `broker_availability` | PPI | ninguno | fail-closed |
| `broker_market_currency` | PPI | IOL cross-check | divergencia => HOLD |
| `broker_settlement` | PPI | IOL cross-check | no mezclar T0/T1 |
| `quantity_step` | PPI explícito | official exchange/broker evidence if semantically equivalent | no inferir |
| `price_tick` | PPI explícito | official exchange/broker evidence if semantically equivalent | no inferir |
| `minimum` | PPI para ejecución | IOL cross-check | broker-specific |
| `quote` | freshest valid provider | other provider | provenance obligatorio |
| `order_book` | freshest valid provider | other provider | mismo market/term/currency |
| `daily_history` | PPI + IOL | reconciled | detectar gaps/divergence |
| `intraday_history` | freshest provider | other provider | freshness + retention |
| `fixed_income_analytics` | IOL candidate | PPI/official cross-check | no confundir analytics con contract |
| `options_greeks` | IOL candidate | Porota recompute later | require freshness |
| `caucion_rate` | dual-source | compare PPI/IOL | mismo plazo/moneda/tipo |

## Pruebas read-only ya completadas

### IOL AL30

Confirmado:

- `TIT. PUBLICOS`;
- ARS;
- lote 100;
- T1;
- AL30/AL30D/AL30C;
- quote + bid/ask;
- TIR/TEM;
- duration;
- valor técnico/paridad;
- cashflows.

### IOL GGAL

Confirmado:

- `ACCIONES`;
- ARS;
- lote 1;
- T1.

### IOL AAPL BCBA

Confirmado:

- `CEDEARS`;
- ARS;
- lote 1;
- T1;
- AAPL/AAPLD/AAPLC.

### IOL Cauciones

Confirmado:

- tasas 1/2/3 días;
- due dates;
- mínimo ARS observado.

### IOL Opciones GGAL

Confirmado:

- chain por vencimiento;
- call/put;
- strike;
- bid/ask;
- IV;
- theoretical price;
- delta/gamma/theta/vega/rho;
- volumen;
- stale flag.

## Próximas muestras obligatorias

1. Acción líquida: validar histórico + intradiario IOL contra PPI.
2. CEDEAR líquido: validar histórico + intradiario IOL contra PPI.
3. AL30: validar histórico IOL + PPI y terminar contrato PPI.
4. Letra: seleccionar ticker real y probar info/quote/history/fixed income analytics.
5. ON: seleccionar ticker real y probar info/quote/history/fixed income analytics.
6. Cauciones: mapear PPI rows vs IOL 1/2/3 días, ARS/USD y colocadora/tomadora.
7. Opciones: mantener solo investigación hasta contrato/riesgo completo.

## Stop conditions

Cualquier prueba debe detenerse si:

- requiere endpoint mutativo;
- requiere place/cancel/subscribe/redeem;
- expone token/credential/account data en log;
- intenta convertir precision decimal en tick/step;
- mezcla instrumentos solo por ticker sin provider identity;
- no puede demostrar mismo plazo/moneda/mercado entre proveedores.
