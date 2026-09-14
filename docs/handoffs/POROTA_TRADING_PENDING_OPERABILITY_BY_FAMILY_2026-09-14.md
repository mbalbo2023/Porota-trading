# POROTA TRADING — PENDIENTES OBLIGATORIOS DE OPERABILIDAD POR FAMILIA — 2026-09-14

## Carácter obligatorio

Este archivo es un backlog hijo del backlog multi-fuente y **no puede desaparecer silenciosamente**. Cada ítem debe terminar como `DONE`, `DEFERRED` o `WONT_DO` con evidencia y causa.

Documento técnico fuente:

`docs/research/POROTA_OPERABILITY_REQUIREMENTS_AND_SOURCE_MAP_BY_FAMILY_2026-09-14.md`

Invariantes:

- `REAL_ORDERS_SENT=0`.
- Sin mutaciones de broker durante investigación.
- PPI = autoridad de contrato `READY_PPI`.
- IOL = market data/analytics/cross-check cuando corresponda.
- Official sources = términos estables/regulatorios.
- No inferir tick/step desde decimales.

## P0 — contrato de ejecución PPI

### OP-P0-01 — ACCIONES / GGAL
Estado: `TODO_CONTRACT_HARDENING`

Cerrar PPI: provider ID, availability, settlement, lot/minimum, quantity step, price tick, fees, horario.

### OP-P0-02 — CEDEAR / AAPL
Estado: `TODO_CONTRACT_HARDENING`

Cerrar PPI y además ratio CEDEAR, ARS/D/C, calendario dual Argentina/USA y corporate actions.

### OP-P0-03 — ETF / IVV CEDEAR ETF
Estado: `TODO_CONTRACT`

Cerrar identidad semántica CEDEAR_ETF, ratio, settlement, step/tick, availability y reglas específicas de ETF/leveraged flag.

### OP-P0-04 — BONOS / AL30
Estado: `IN_PROGRESS`

Ya: PPI identity/currency/fees/auction/decimals + IOL quote/history/TIR/duration/cashflow.

Falta: settlement PPI, nominal/minimum, quantity step, price tick, horario/availability y veredicto end-to-end.

### OP-P0-05 — LETRAS / S30O6
Estado: `IN_PROGRESS`

Ya: IOL metadata, quote/order book, OHLCV, fixed-income analytics; términos oficiales Tesoro.

Falta PPI: identity exacta, settlement, nominal/minimum, quantity step, price tick, availability/fees/horario.

### OP-P0-06 — LETRA LINKED / D30O6
Estado: `IN_PROGRESS_PARTIAL_ANALYTICS`

Ya: IOL metadata + quote/order book; Tesoro oficial.

Hallazgo: IOL fixed-income analytics devuelve `not_found`; definir analytics Porota/official fallback y contrato PPI completo.

### OP-P0-07 — ON / YMCJO
Estado: `IN_PROGRESS`

Ya: IOL metadata, quote/order book, OHLCV, TIR/duration/cashflow.

Falta: contract PPI + terms CNV/emisor cross-check + liquidity/credit gates.

### OP-P0-08 — CAUCIONES
Estado: `IN_PROGRESS`

Ya: PPI 120 `CaucionesOperables`; IOL ARS/USD colocadora/tomadora rate/minimum/due-date.

Falta PPI: mapping moneda/tipo/plazo, rate/price semantics, minimum/maximum, amount step, settlement, guarantee/aforo, availability, fees y horarios.

## P1 — familias de riesgo/flujo especial

### OP-P1-01 — OPCIONES / GGAL
Estado: `RESEARCH_ONLY`

IOL chain + IV + Greeks confirmado. Antes de PAPER: PPI exact contract, multiplier, tick, step, exercise/assignment, margin, liquidity/spread, expiry/auto-close, Greeks limits y stress.

### OP-P1-02 — FUTUROS / Futuro GGAL
Estado: `HOLD_CONTRACT_RISK`

BYMA official contract specs disponibles. Falta PPI: listed symbol, margin/collateral, availability, fees, settlement, expiry handling. IOL reader de futuros no confirmado.

### OP-P1-03 — ACCIONES USA / AAPL NASDAQ
Estado: `HOLD_CONTRACT`

IOL USD/T1/quote confirmado. Falta identidad PPI específica, venue, settlement, fees, FX/cash rules, calendar, tick/step/minimum.

### OP-P1-04 — FCI / ejemplo IOLDOLD en IOL
Estado: `DEFERRED_NON_INTRADAY`

IOL catalogue capability confirmada. Para PPI: fund ID, minimum, currency, VCP/NAV, subscription/redemption cut-off, settlement, fees, reglamento.

### OP-P1-05 — FCI_EXTERIOR
Estado: `DEFERRED_NEEDS_CURRENT_PPI_EXAMPLE`

Seleccionar primer PPI AVAILABLE actual. No inventar especie. Capturar fund ID, currency, minimum, NAV, cut-offs, settlement, fees y reglamento.

### OP-P1-06 — LICITACIONES / D30O6
Estado: `SEPARATE_PRIMARY_FLOW_TODO`

Modelar como primary-market workflow, no secondary order. Capturar issuer terms + PPI subscription window/cut-off/minimum/settlement/fees/allocation.

### OP-P1-07 — INDICES / S&P Merval
Estado: `ANALYTICS_ONLY_TODO_MODEL`

No generar READY_PPI directo. Modelar provider/timestamp/methodology/constituents y uso como benchmark/risk factor.

### OP-P1-08 — LEBAC / NOBAC legacy
Estado: `NOT_CURRENT_UNLESS_PROVEN`

Conservar históricos. No READY sin PPI AVAILABLE actual + evidencia oficial current. BCRA LEBAC history observado hasta diciembre 2018.

## P1 — plataforma multi-source

### OP-P1-09 — `IOLReadOnlyProvider`
Estado: `TODO`

Implementar sólo lectura; no exponer métodos de trading/mutación.

### OP-P1-10 — `PPIContractProvider`
Estado: `TODO/IN_PROGRESS_EVIDENCE`

Normalizar operability/availability/settlement/minimum/step/tick/fees por familia.

### OP-P1-11 — provider identity map
Estado: `TODO`

Nunca unir sólo por ticker. Incluir family, market, currency, settlement, provider ID, ARS/D/C, underlying/emisor.

### OP-P1-12 — provenance + freshness
Estado: `TODO`

Guardar fuente/timestamp/calidad por campo y por decisión.

### OP-P1-13 — provider divergence gate
Estado: `TODO`

Definir tolerancias por quote/history/settlement/identity y bloquear en conflicto.

### OP-P1-14 — historical reconciliation
Estado: `IN_PROGRESS`

Comparar mismas fechas PPI vs IOL para GGAL, AAPL, AL30, S30O6 y YMCJO. Medir gaps, volumen, calendarios, adjustment y retention intraday.

## Criterio de cierre

Una familia sólo puede declararse completamente resuelta cuando un ejemplo representativo pasa:

`IDENTITY_READY -> MARKET_DATA_READY -> ANALYTICS_READY -> CONTRACT_READY_PPI -> RISK_READY -> READY_PAPER_PPI`

Si una capa no aplica (por ejemplo INDICES), debe quedar explícitamente como `N/A` o estado especializado, nunca asumirse.
