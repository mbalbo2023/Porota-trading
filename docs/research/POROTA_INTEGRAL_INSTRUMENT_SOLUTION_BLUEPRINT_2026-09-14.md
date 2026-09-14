# POROTA TRADING — BLUEPRINT DE SOLUCIÓN INTEGRAL POR FAMILIA DE INSTRUMENTOS — 2026-09-14

## Objetivo

Definir **cómo debe quedar implementada la solución completa** para que Porota pueda descubrir, estudiar, validar y operar en PAPER cada familia soportada, con trazabilidad de origen de datos y sin confundir analytics con contrato de ejecución.

Este blueprint transforma la investigación previa en una arquitectura de implementación.

## Principio central

Cada instrumento debe recorrer el mismo pipeline base:

`DISCOVERY -> IDENTITY -> MARKET DATA -> ANALYTICS -> CONTRACT PPI -> RISK -> READY_PAPER_PPI`

La familia modifica qué campos y qué validaciones son necesarias, pero no elimina ninguna capa aplicable.

## Arquitectura lógica común

### A. Provider adapters

1. `PPIContractProvider`
   - autoridad de identidad operable y contrato PPI;
   - read-only durante esta fase;
   - métodos previstos:
     - `discover_operable_instruments(family)`
     - `get_operable_identity(provider_id)`
     - `get_execution_config(provider_id)`
     - `get_settlement_terms(provider_id)`
     - `get_minimums_steps_ticks(provider_id)`
     - `get_fees(provider_id)`
     - `get_availability(provider_id)`
     - `get_trading_window(provider_id)`

2. `IOLReadOnlyProvider`
   - market data y analytics estructurada;
   - nunca contiene métodos mutativos;
   - métodos previstos:
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

3. `OfficialTermsProvider`
   - BYMA / Tesoro / BCRA / CNV / emisor / administradora;
   - sólo términos estables o regulatorios;
   - no reemplaza disponibilidad/contrato PPI.

### B. Canonical Identity Resolver

Entidad mínima:

```text
canonical_instrument_id
family
subfamily
symbol
provider_ids{PPI,IOL,...}
market
currency
settlement
ars_symbol
dollar_symbol
cable_symbol
underlying_id
issuer_id
maturity_date
contract_variant
```

Regla absoluta: nunca resolver identidad sólo por ticker textual.

### C. Canonical Evidence Store

Cada valor debe poder explicarse:

```text
canonical_field
value
source_provider
source_resource
source_instrument_id
effective_at
observed_at
freshness_seconds
quality_status
conflict_status
confidence
```

### D. Readiness Engine

Gates independientes:

- `IDENTITY_READY`
- `MARKET_DATA_READY`
- `ANALYTICS_READY`
- `CONTRACT_READY_PPI`
- `RISK_READY`

Sólo si los cinco aplicables pasan:

`READY_PAPER_PPI=true`

### E. Divergence Engine

Comparará PPI vs IOL vs official cuando corresponda:

- identidad;
- market/currency;
- settlement;
- última cotización;
- OHLCV;
- fechas de vencimiento;
- nominal/lote;
- tasas/cashflows.

Resultados:

- `MATCH`
- `WITHIN_TOLERANCE`
- `STALE_SOURCE`
- `PROVIDER_DIVERGENCE`
- `IDENTITY_CONFLICT`

`PROVIDER_DIVERGENCE` bloquea READY cuando afecta un campo crítico.

---

# Solución integral por familia

## 1. ACCIONES — patrón GGAL

### Ingesta

- PPI: identity, availability, settlement, fees, tick, quantity step, trading window.
- IOL: quote, order book, OHLCV, intraday.
- BYMA: calendario y reglas de mercado.
- CNV/emisor: corporate actions/hechos relevantes.

### Normalización

```text
family=ACCIONES
market=BCBA
currency=ARS
settlement=PPI_authoritative
lot_size=PPI_authoritative
price_tick=PPI_or_official_explicit
quantity_step=PPI_or_official_explicit
```

### Analytics

- retornos;
- volatilidad;
- ATR;
- momentum/trend;
- spread/liquidez;
- volumen relativo;
- gap risk;
- beta sector/índice;
- corporate-event gate.

### Risk gates

- spread máximo;
- volumen mínimo;
- quote freshness;
- max position;
- max sector concentration;
- stop/take-profit/trailing policy;
- market calendar.

### DONE

Cuando GGAL pueda reconstruirse end-to-end con evidencia PPI + IOL + risk y una orden PAPER válida pueda construirse sin inferencias.

---

## 2. CEDEAR — patrón AAPL BCBA

### Todo lo de Acción, más

- ratio CEDEAR/acción;
- underlying global;
- ARS/D/C aliases;
- calendario dual BYMA + mercado origen;
- dividend/corporate-action conversion;
- FX/CCL contextual risk.

### Modelo adicional

```text
underlying_market=NASDAQ
underlying_symbol=AAPL
cedear_ratio=<official>
origin_calendar_state
argentina_calendar_state
origin_market_open
local_market_open
```

### Risk gates específicos

- si mercado origen cerrado, política configurable:
  - `HOLD_NEW_ENTRIES` o
  - `ALLOW_WITH_WIDER_RISK_LIMITS`;
- divergence CEDEAR vs underlying ajustado por ratio/FX;
- corporate-action pending gate.

### DONE

AAPL BCBA correctamente separado de AAPL NASDAQ, ratio validado y calendario dual operativo.

---

## 3. ETF / CEDEAR ETF — patrón IVV

### Todo lo de CEDEAR, más

- clasificación ETF;
- index/fund underlying;
- leveraged/inverse flag;
- tracking error context;
- NAV/fund events cuando estén disponibles.

### Risk gates

- leveraged/inverse instrumentos requieren límites distintos;
- subyacente/índice stale;
- concentración temática/sectorial;
- tracking dislocation.

### DONE

IVV se modela como `CEDEAR_ETF`, no como acción genérica.

---

## 4. ACCIONES USA — patrón AAPL NASDAQ

### Fuentes

- PPI: broker contract USA, fees, cash/FX rules, venue, trading window.
- IOL: quote/order book/history.
- mercado origen/emisor: calendario/events.

### Campos extra

- venue;
- currency USD;
- local cash eligibility;
- tax/fee profile;
- US holiday calendar.

### Risk

- timezone/session state;
- FX/cash availability;
- foreign-market close/open gaps.

### DONE

No comparte canonical identity con el CEDEAR local aunque comparta ticker.

---

## 5. BONOS — patrón AL30

### Fuentes

- PPI: operability contract.
- IOL: quote/order book/history + fixed income analytics.
- Tesoro/emisor: legal/economic terms.

### Canonical bond terms

```text
nominal_value
price_basis
currency
settlement
issue_date
maturity_date
coupon_schedule
amortization_schedule
residual_balance
accrued_interest
clean_price
dirty_price
technical_value
parity
yield_tir
yield_tem
macaulay_duration
modified_duration
```

### Contract fields críticos

- PPI quantity semantic: nominales vs lotes;
- minimum nominal;
- quantity step;
- price tick;
- settlement;
- fees;
- availability.

### Analytics

- TIR/duration/DV01;
- cashflow ladder;
- carry/roll-down;
- parity;
- duration-adjusted return;
- liquidity/spread;
- issuer/sovereign risk.

### Risk gates

- maturity;
- cashflow events;
- wide spread;
- low depth;
- currency variant ARS/D/C;
- price basis mismatch.

### DONE

AL30 PAPER order builder recibe cantidad y precio correctos en la semántica PPI sin inferencias.

---

## 6. LETRAS — patrón S30O6

### Arquitectura

Misma base de renta fija, con subtipos:

- LECAP;
- BONCAP;
- dólar-linked;
- CER;
- zero coupon;
- discount instruments.

### Fuente de términos

- Tesoro oficial obligatorio para tipo y fórmula económica;
- IOL analytics cuando exista;
- Porota calcula analytics faltante desde términos oficiales;
- PPI sólo contract/execution.

### Caso D30O6

Como IOL no entrega fixed-income analytics:

`OfficialTermsProvider -> PorotaFixedIncomeCalculator -> analytics`

El sistema debe soportar `ANALYTICS_SOURCE=POROTA_CALCULATED` con inputs trazables.

### DONE

S30O6 y D30O6 pueden atravesar el mismo pipeline aunque uno tenga analytics IOL y el otro requiera cálculo propio.

---

## 7. ON — patrón YMCJO

### Fuentes

- PPI: contract.
- IOL: market/fixed income analytics.
- CNV + emisor: prospecto, terms, covenants, cashflows, events.
- rating agency si corresponde.

### Analytics extra

- spread vs soberano/curva;
- credit bucket;
- duration;
- yield;
- issuer concentration;
- coupon/call/amortization schedule;
- liquidity score.

### Risk gates

- issuer concentration;
- credit quality;
- event/default/restructuring flag;
- callability;
- low liquidity.

### DONE

YMCJO tiene identidad, contract PPI, terms CNV/emisor, analytics y límites crediticios.

---

## 8. CAUCIONES

### Dos instrumentos económicos separados

- `CAUCION_COLOCADORA`
- `CAUCION_TOMADORA`

No deben compartir el mismo state machine.

### Canonical contract

```text
currency
side=colocadora|tomadora
tenor_days
due_date
annual_rate
minimum_amount
maximum_amount
amount_step
settlement
fees
collateral_required
haircut_aforo
eligible_collateral
trading_window
```

### Fuentes

- PPI: ejecución, disponibilidad, min/max/step, guarantee/aforo, fees.
- IOL: rate/minimum/due date analytics/cross-check.
- market rules: BYMA/A3 cuando corresponda.

### Analytics

- effective return/cost net fees;
- annualized rate;
- cash utilization;
- opportunity cost;
- collateral utilization para tomadora.

### Risk

- maturity cash availability;
- collateral shortfall;
- concentration;
- rate divergence;
- settlement calendar.

### DONE

Una caución PAPER se construye por `currency + side + tenor`, sin ticker artificial.

---

## 9. OPCIONES — patrón GFGC7000OC

### Canonical option identity

```text
underlying
option_type=CALL|PUT
strike
expiration
exercise_style
contract_multiplier
currency
settlement
```

### Market/analytics

- bid/ask;
- volume/open interest si disponible;
- IV;
- theoretical price;
- delta/gamma/theta/vega/rho;
- underlying spot;
- term structure/skew.

### Contract PPI

- exact symbol/provider ID;
- multiplier;
- tick;
- quantity step;
- margin requirements;
- exercise/assignment;
- expiry handling.

### Risk engine dedicado

- max delta exposure;
- max gamma;
- max vega;
- theta budget;
- spread/liquidity threshold;
- short-option margin;
- assignment/exercise risk;
- expiry-day restrictions;
- stress scenarios.

### DONE

No pasa a PAPER general hasta existir `OptionsRiskEngine` específico.

---

## 10. FUTUROS — patrón GGAL future

### Canonical future identity

```text
underlying
contract_month
expiry_date
contract_size
currency
price_tick
quantity_step
margin_initial
margin_maintenance
settlement_type
```

### Fuentes

- BYMA/A3: contract specification.
- PPI: broker availability, margin/collateral, fees, order semantics.
- market data provider: quotes/history cuando se confirme.

### Risk

- daily MTM;
- margin utilization;
- variation margin;
- expiry rollover;
- limit-up/down si aplica;
- leverage cap;
- collateral stress.

### DONE

No reutilizar lógica de spot; requiere `FuturesPositionEngine` y `MarginEngine`.

---

## 11. FCI

### Flujo separado

No order book intradía. State machine:

`DISCOVERED -> NAV_READY -> SUBSCRIPTION_CONTRACT_READY -> PAPER_SUBSCRIPTION_READY`

y para rescate:

`POSITION -> REDEMPTION_CONTRACT_READY -> PAPER_REDEMPTION_READY`

### Campos

- fund ID;
- share class;
- currency;
- NAV/VCP;
- NAV timestamp;
- minimum subscription;
- minimum redemption;
- balance minimum;
- subscription cut-off;
- redemption cut-off;
- redemption settlement;
- management/custody fees;
- fund rules/prospectus.

### Fuentes

- PPI: availability/cut-off/minimum/settlement.
- CNV/administradora: legal/portfolio terms.
- IOL only as catalog/cross-check if same fund exists.

### DONE

FCI queda fuera del intradía pero integrado al portfolio/cash management.

---

## 12. FCI EXTERIOR

Mismo motor FCI con extras:

- foreign currency;
- foreign holiday calendar;
- FX rules;
- international settlement;
- jurisdiction/tax metadata.

Primera acción: elegir el primer FCI exterior `AVAILABLE` devuelto por PPI y construir su canonical record.

---

## 13. LICITACIONES

### State machine propio

`ANNOUNCED -> TERMS_READY -> PPI_WINDOW_OPEN -> ANALYTICS_READY -> PAPER_BID_READY -> CLOSED -> RESULT_KNOWN`

### Campos

- issuer;
- instrument;
- auction date;
- settlement date;
- bid variable (price/rate);
- minimum VNO;
- maximum amount;
- competitive/non-competitive tranche;
- PPI cut-off;
- fees;
- allocation result.

### Fuentes

- Tesoro/emisor: official terms/results.
- PPI: subscription window and broker constraints.

No reutilizar secondary-market order builder.

---

## 14. ÍNDICES

`ANALYTICS_ONLY`.

### Funciones

- market regime;
- benchmark;
- beta;
- correlation;
- sector breadth;
- volatility context.

No `READY_PPI` directo.

---

## 15. LEBAC / NOBAC / legacy

### Política

- conservar históricos;
- mantener resolver/canonical metadata;
- no incluir en candidate universe actual salvo PPI `AVAILABLE` + evidence oficial vigente.

Estado default:

`INACTIVE_LEGACY`

---

# Componentes de software a construir

## C1 — `providers/iol_readonly.py`

Sólo GET/read methods. Sin place/cancel/subscribe.

## C2 — `providers/ppi_contract.py`

Normalizador contractual read-only.

## C3 — `providers/official_terms.py`

Adapters/caches por fuente oficial.

## C4 — `core/instrument_identity.py`

Canonical identity resolver.

## C5 — `core/evidence_store.py`

Provenance por campo.

## C6 — `core/readiness_engine.py`

Evalúa los cinco gates.

## C7 — `core/provider_reconciliation.py`

Divergence/staleness/identity conflict.

## C8 — `analytics/fixed_income.py`

Fallback propio para analytics cuando IOL no tenga cobertura.

## C9 — `risk/options.py`

Greeks, margin/stress, expiry.

## C10 — `risk/futures.py`

Margin/MTM/rollover.

## C11 — `flows/fci.py`

Subscriptions/redemptions PAPER state machine.

## C12 — `flows/primary_market.py`

Licitaciones PAPER.

---

# Base de datos propuesta

## `instrument_master`

Una fila por canonical identity.

## `provider_instrument_map`

Map canonical <-> provider IDs/symbols.

## `instrument_contract_evidence`

PPI contract fields con timestamp/provenance.

## `market_snapshots`

Quotes/order book normalized.

## `historical_bars`

OHLCV multi-provider con source.

## `instrument_analytics`

Analytics IOL/Porota con method/version.

## `official_terms`

Términos regulatorios/estables.

## `readiness_state`

Estado por gate y razones de HOLD.

## `provider_divergence`

Conflictos y resolución.

---

# Orden de implementación recomendado

## WAVE A — core reusable

1. canonical identity resolver;
2. evidence/provenance store;
3. `IOLReadOnlyProvider`;
4. `PPIContractProvider`;
5. readiness engine;
6. provider divergence gate.

Resultado: infraestructura común.

## WAVE B — spot mature

7. GGAL Acción;
8. AAPL CEDEAR;
9. IVV CEDEAR ETF;
10. AAPL USA si PPI lo soporta.

Resultado: spot multi-source profesional.

## WAVE C — renta fija

11. AL30 Bono;
12. S30O6 Letra;
13. D30O6 linked + Porota analytics fallback;
14. YMCJO ON.

Resultado: reusable fixed-income engine.

## WAVE D — money market

15. Caución colocadora;
16. Caución tomadora.

Resultado: cash/collateral engine.

## WAVE E — derivatives

17. options risk engine;
18. futures margin/MTM engine.

## WAVE F — non-intraday

19. FCI/FCI exterior;
20. licitaciones;
21. indices analytics;
22. legacy inactive catalog.

---

# Acceptance test de cada familia

Cada ejemplo patrón debe producir un JSON/report equivalente a:

```text
canonical_id=<...>
family=<...>
identity_ready=true|false
market_data_ready=true|false
analytics_ready=true|false
contract_ready_ppi=true|false
risk_ready=true|false
ready_paper_ppi=true|false
hold_reasons=[...]
field_sources={...}
provider_conflicts=[...]
stale_fields=[...]
real_orders_sent=0
```

No se permite `ready_paper_ppi=true` si algún gate obligatorio está false.

---

# Resultado integral esperado

La solución final permitirá que Porota no dependa de una sola API y tampoco opere con campos inferidos:

- PPI decide si el instrumento es ejecutable y bajo qué contrato;
- IOL alimenta mercado y analytics estructurada;
- fuentes oficiales aportan términos estables;
- Porota resuelve identidad, calcula faltantes, reconcilia fuentes, mide riesgo y bloquea divergencias;
- cada familia tiene su propio risk/flow plugin sobre un núcleo común.

La implementación debe avanzar por ejemplos patrón y, una vez validado el patrón, escalar automáticamente al resto de especies de la misma familia mediante discovery + normalización + gates.
