# POROTA TRADING — CHECKPOINT P0 — SCRAPING → FAMILIAS OPERATIVAS — 2026-09-09

## Regla canónica
Este pendiente es P0 y forma parte del go-live PAPER de RC6. No se limita a Opciones, Cauciones o Bonos: aplica a TODAS las familias de instrumentos detectadas por PPI/BYMA.

Objetivo: que el pipeline de scraping / Contract Evidence / API read-only complete los campos contractuales, económicos y operativos faltantes de cada familia, normalice provenance/freshness y recalcule readiness (`can_simulate` / `READY_PAPER`) de forma automática y reproducible.

## Familias incluidas
Como mínimo:
- ACCIONES
- CEDEARS
- BONOS
- LETRAS
- ON / OBLIGACIONES NEGOCIABLES
- OPCIONES
- FUTUROS
- ETF / ETFS
- ACCIONES USA
- CAUCIONES
- FCI
- FCI EXTERIOR
- LICITACIONES / CANJES
- INDICES
- cualquier familia adicional que PPI exponga.

## Política de habilitación
Una familia/instrumento sólo puede pasar a `can_simulate=1` / `READY_PAPER` cuando exista evidencia verificable y fresca para los campos que su ciclo financiero exige. Nunca inferir silenciosamente datos contractuales desde ticker/DOM cuando faltan campos autoritativos.

El scraping debe ser un mecanismo de COMPLETADO de contrato, no sólo una captura visual. Debe:
1. descubrir y capturar XHR/GET read-only relevantes por familia;
2. normalizar contratos a nivel instrumento/serie;
3. persistir source/provenance/timestamp/TTL;
4. reconciliar PPI API ↔ PPI Web/DOM ↔ BYMA/MAE/ROFEX u otra fuente oficial cuando aplique;
5. recalcular capability/readiness;
6. hacer visible en dashboard el blocker exacto si sigue HOLD;
7. disparar históricos dirigidos cuando la estrategia de esa familia realmente los requiera.

## Campos por familia — mínimo esperado
### Acciones / CEDEAR / ETF
Ticker, mercado, moneda, settlement, price basis, nominal/cash multiplier si aplica, quantity step, mínimos, fees, calendario y market-data freshness.

### Bonos / Letras / ON
Ticker/ISIN, moneda, mercado, settlement, nominal units, price basis (%/100, clean/dirty si aplica), valor residual/par, mínimo/step, accrued/coupon/fecha relevante cuando aplique, fees, calendario y semántica de Current/Book.

### Opciones
Subyacente, strike, Call/Put, vencimiento+timezone, lote/multiplicador, quantity step, settlement/prima, estilo/ejercicio cuando aplique, market-data chain/book, fees y restricciones horarias.

### Futuros
Contrato/subyacente, vencimiento, multiplier, tick size/value, currency, settlement, margin/garantía read-only, calendario y fees.

### Cauciones
Moneda, plazo, TNA, mínimo/step, book/profundidad, cutoff/horario, base de cálculo, fees PPI + derechos de mercado completos, settlement y restricciones operativas.

### FCI / FCI Exterior
Fondo/clase, moneda, mínimo, horario de corte, plazo de rescate/suscripción, valor cuota/freshness, fees y restricciones. No ejecutar suscripciones/rescates reales.

### Licitaciones / Canjes
Instrumento/evento, moneda, ventana, fecha/cutoff, mínimo/step, condiciones, settlement y estado; sólo read-only hasta política PAPER específica.

### Índices
Identidad, fuente, currency/base, timestamp/freshness y uso sólo como contexto/señal, nunca como instrumento ejecutable salvo que exista contrato negociable separado.

## Estado confirmado al 2026-09-09 ~12:32 ART
- ACCIONES: 55 total / 55 AVAILABLE / 55 can_simulate.
- CEDEARS: 193 total / 191 AVAILABLE / 191 can_simulate.
- BONOS: 42 total / 42 AVAILABLE / 0 can_simulate.
- OPCIONES: 383 total / 382 AVAILABLE / 0 can_simulate.
- CAUCIONES: 10 total / 10 AVAILABLE / 0 can_simulate.
- El runtime ya genera decisiones HOLD para varias familias no simulables; esto NO equivale a READY_PAPER.
- Scraping/Contract Evidence RC6 continúa bloqueado por sesión web expirada después de corregir ownership del perfil; PPI API read-only sigue OK.

## Gate de cierre P0
No considerar cerrado este checkpoint hasta obtener:
- matriz completa por familia con TOTAL / AVAILABLE / CAN_SIMULATE / blocker;
- `ACTIVE_LEGACY_RUNTIME=0` preservado;
- Contract Evidence RC6 operativo y sin output legacy activo;
- scraping por familia materializando contratos a nivel instrumento/serie;
- readiness recalculado automáticamente;
- dashboard mostrando evaluados, HOLD/BLOCKED y causa exacta;
- históricos dirigidos activados sólo después de contrato válido cuando corresponda;
- `PRODUCTION_PAPER`, `real_orders_sent=0`, `REAL_ORDER_CAPABILITY=BLOCKED` preservados.

## Prioridad
P0 — MUY ALTA. Este pendiente prevalece sobre mejoras cosméticas y Waves P1/P2 cuando exista conflicto de recursos, porque determina qué familias pueden ser evaluadas de forma confiable durante la jornada.
