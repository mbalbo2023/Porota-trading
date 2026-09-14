# POROTA TRADING — TRACKER OBLIGATORIO DE SOLUCIÓN INTEGRAL POR WAVES — 2026-09-14

## Propósito

Transformar el blueprint integral en una secuencia controlada de implementación. Este archivo es obligatorio: ningún ítem desaparece sin `DONE`, `DEFERRED` o `WONT_DO` documentado.

Blueprint fuente:

`docs/research/POROTA_INTEGRAL_INSTRUMENT_SOLUTION_BLUEPRINT_2026-09-14.md`

## Invariantes

- PAPER solamente.
- `REAL_ORDERS_SENT=0` durante desarrollo/investigación.
- No habilitar una familia por inferencia.
- PPI contract y risk gates son obligatorios antes de `READY_PAPER_PPI`.
- IOL adapter inicial es read-only y no expone métodos mutativos.

## WAVE A — CORE MULTI-SOURCE REUSABLE

### WA-01 — Canonical Identity Resolver
Estado: `TODO`

Debe resolver family/subfamily, provider IDs, market, currency, settlement, ARS/D/C, underlying/emisor y evitar matching por ticker solo.

### WA-02 — Evidence/Provenance Store
Estado: `TODO`

Guardar por campo source/effective_at/observed_at/freshness/quality/conflict.

### WA-03 — IOLReadOnlyProvider
Estado: `TODO`

Sólo instrument info, quote/book, daily/intraday history, fixed income analytics, caucion rates, options chain, events y FCI catalog.

### WA-04 — PPIContractProvider
Estado: `IN_PROGRESS_EVIDENCE`

Ya existe evidencia contractual parcial. Falta encapsular discovery/identity/execution config/settlement/min-step-tick/fees/availability/window.

### WA-05 — Readiness Engine
Estado: `TODO`

Gates: IDENTITY, MARKET_DATA, ANALYTICS, CONTRACT_PPI, RISK.

### WA-06 — Provider Reconciliation / Divergence Gate
Estado: `TODO`

Detectar stale, identity conflict, settlement/currency mismatch y divergence de precios/históricos.

### WA-07 — Canonical DB schema/migrations
Estado: `TODO_DESIGN_FIRST`

No migrar runtime hasta aprobar schema y pruebas.

## WAVE B — SPOT

### WB-01 — ACCIONES / GGAL
Estado: `TODO_PATTERN`

Cerrar contrato PPI + dual-provider market data + risk standard.

### WB-02 — CEDEAR / AAPL BCBA
Estado: `TODO_PATTERN`

Agregar ratio, ARS/D/C, double calendar y underlying events.

### WB-03 — CEDEAR ETF / IVV
Estado: `TODO_PATTERN`

Agregar ETF classification, ratio, tracking/leveraged flags.

### WB-04 — ACCIONES USA / AAPL NASDAQ
Estado: `HOLD_UNTIL_PPI_CONTRACT_PROVED`

Separar de AAPL BCBA. Venue/fees/FX/calendar/settlement PPI obligatorios.

## WAVE C — RENTA FIJA

### WC-01 — BONO / AL30
Estado: `IN_PROGRESS_PRIORITY`

Cerrar PPI nominal/minimum/step/tick/settlement/availability. IOL analytics ya fuerte.

### WC-02 — LETRA / S30O6
Estado: `IN_PROGRESS_PATTERN`

Cerrar contract PPI y validar fixed-income analytics IOL + official terms.

### WC-03 — LETRA LINKED / D30O6
Estado: `IN_PROGRESS_ANALYTICS_FALLBACK`

IOL market data sí, fixed analytics no. Implementar PorotaFixedIncomeCalculator desde official terms.

### WC-04 — ON / YMCJO
Estado: `IN_PROGRESS_PATTERN`

Cerrar PPI contract + CNV/emisor terms + credit/liquidity gates.

### WC-05 — FixedIncomeAnalytics fallback
Estado: `TODO`

TIR/TEM/duration/cashflows/accrued/parity cuando provider externo no cubra.

## WAVE D — CAUCIONES

### WD-01 — Colocadora
Estado: `IN_PROGRESS_EVIDENCE`

PPI 120 rows + IOL rates/minimum. Falta full semantic contract.

### WD-02 — Tomadora
Estado: `IN_PROGRESS_EVIDENCE`

Separar state machine y añadir guarantee/aforo/collateral engine.

### WD-03 — Cash/Collateral engine
Estado: `TODO`

Effective return/cost, cash maturity, collateral utilization y concentration.

## WAVE E — DERIVADOS

### WE-01 — Options Contract Resolver
Estado: `RESEARCH_ONLY`

Exact PPI contract/multiplier/tick/step/exercise/assignment.

### WE-02 — OptionsRiskEngine
Estado: `TODO`

Delta/gamma/vega/theta budgets, stress, short margin, expiry risk.

### WE-03 — Futures Contract Resolver
Estado: `HOLD_PPI_CONTRACT`

BYMA terms disponibles; falta PPI listed contract/margin/fees.

### WE-04 — FuturesPosition/Margin Engine
Estado: `TODO`

MTM, initial/maintenance margin, collateral stress, rollover.

## WAVE F — NON-INTRADAY / SPECIAL FLOWS

### WF-01 — FCI
Estado: `DEFERRED_NON_INTRADAY`

State machine subscription/redemption, VCP/NAV, cut-offs, settlement.

### WF-02 — FCI EXTERIOR
Estado: `DEFERRED_NEEDS_CURRENT_PPI_EXAMPLE`

Primero elegir PPI AVAILABLE real.

### WF-03 — Primary Market / Licitaciones
Estado: `TODO_SEPARATE_FLOW`

Announcement -> terms -> PPI window -> analytics -> PAPER bid -> result.

### WF-04 — Indices Analytics
Estado: `TODO_ANALYTICS_ONLY`

Benchmarks/regime/risk factors; no direct READY_PPI.

### WF-05 — Legacy LEBAC/NOBAC
Estado: `INACTIVE_LEGACY`

Conservar histórico. Sólo reactivar con PPI AVAILABLE + official current evidence.

## Acceptance común

Cada patrón debe emitir un reporte con:

- canonical ID;
- field sources;
- five readiness gates;
- hold reasons;
- provider conflicts;
- stale fields;
- `ready_paper_ppi`;
- `real_orders_sent=0` durante validación.

## Orden de ejecución actual

1. WA-01 .. WA-06 diseño/implementación reusable.
2. WC-01 AL30 y WD-01/02 Cauciones como stoppers contractuales prioritarios en paralelo con core.
3. WB-01/02/03 y WC-02/03/04 como patrones de escalamiento.
4. Derivados sólo después del core y risk engines.
5. Non-intraday al final sin borrar alcance.
