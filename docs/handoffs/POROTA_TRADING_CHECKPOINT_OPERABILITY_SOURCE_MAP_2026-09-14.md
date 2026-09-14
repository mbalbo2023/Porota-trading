# POROTA TRADING — CHECKPOINT OPERABILIDAD Y FUENTES POR FAMILIA — 2026-09-14

## Estado canónico de esta investigación

Branch:

`ops/rc6-contract-open-session-immediate-20260914`

Objetivo de esta ronda:

> Determinar, con un ejemplo representativo por familia, qué información necesita Porota para estudiar y operar profesionalmente cada instrumento, de qué fuente debe venir cada campo y qué falta para `READY_PAPER_PPI`.

## Seguridad

- Investigación read-only.
- No se ejecutó ninguna orden.
- No se llamó a place/cancel/subscribe/redeem/place_caucion.
- `REAL_ORDERS_SENT=0` según invariantes vigentes.
- No se importó automáticamente esta investigación a las DB canónicas.

## Documentos nuevos obligatorios

### Informe exhaustivo

`docs/research/POROTA_OPERABILITY_REQUIREMENTS_AND_SOURCE_MAP_BY_FAMILY_2026-09-14.md`

Commit de creación:

`7e1e8b6be865c62ca57566151bf9770eda65c187`

### Backlog obligatorio por familia

`docs/handoffs/POROTA_TRADING_PENDING_OPERABILITY_BY_FAMILY_2026-09-14.md`

Commit de creación:

`6ac14197e97f528ea91b085806709fecf8492d5e`

### Blueprint de solución integral

`docs/research/POROTA_INTEGRAL_INSTRUMENT_SOLUTION_BLUEPRINT_2026-09-14.md`

Commit de creación:

`28521663e9c73d79bae30562daec778a9e783a23`

Los documentos deben leerse junto con:

- `docs/handoffs/POROTA_TRADING_CHECKPOINT_CONTRACT_EVIDENCE_2026-09-14.md`
- `docs/handoffs/POROTA_TRADING_PENDING_MULTI_SOURCE_PPI_IOL_2026-09-14.md`
- `docs/handoffs/POROTA_TRADING_PENDING_OPERABILITY_BY_FAMILY_2026-09-14.md`
- `docs/research/POROTA_MULTI_SOURCE_MARKET_DATA_AND_CONTRACT_ARCHITECTURE_2026-09-14.md`
- `docs/research/POROTA_PROVIDER_COVERAGE_MATRIX_INITIAL_2026-09-14.md`
- `docs/research/POROTA_OPERABILITY_REQUIREMENTS_AND_SOURCE_MAP_BY_FAMILY_2026-09-14.md`
- `docs/research/POROTA_INTEGRAL_INSTRUMENT_SOLUTION_BLUEPRINT_2026-09-14.md`

## Decisión arquitectónica confirmada

No abandonar PPI. No reducir permanentemente a Acciones + CEDEAR. No iniciar scraping IOL masivo.

Modelo:

`PPI Contract Authority + IOL Structured Market/Analytics + Official Terms + Porota Reconciliation/Risk`

PPI sigue siendo fuente obligatoria para `READY_PPI`; IOL puede proporcionar o enriquecer análisis y market data; fuentes oficiales validan términos estables.

## Evidencia representativa nueva confirmada en esta ronda

### LETRAS — S30O6

IOL confirmó:

- tipo `Letras`;
- ARS;
- lote 100;
- T1;
- quote/order book durante rueda;
- histórico diario 2026-09-01..2026-09-14;
- fixed-income analytics.

Snapshot de analytics:

- maturity 2026-10-30;
- remaining days 45;
- TIR ~25.6518%;
- TEM ~1.9211%;
- rate 2.55%;
- nominal analítico 100.

Tesoro oficial confirma el instrumento y sus términos estables.

### LETRA DÓLAR-LINKED — D30O6

IOL confirmó metadata y quote/order book, pero fixed-income analytics respondió `not_found`.

Conclusión: market data IOL no implica cobertura analítica universal; usar términos oficiales/Porota analytics y mantener PPI contract separado.

### ON — YMCJO

IOL confirmó:

- YPF Clase XVIII, vencimiento 2033-09-30;
- tipo `Obligaciones Negociables`;
- ARS;
- lote 100;
- T1;
- `YMCJO/YMCJD/YMCJC`;
- quote/order book;
- histórico diario 2026-09-01..2026-09-14;
- TIR/duration/clean-dirty/accrued/parity/technical value/cashflow.

Snapshot aproximado:

- TIR ~6.6459%;
- Macaulay duration ~4.6845;
- modified duration ~4.3926.

### ETF / CEDEAR ETF — IVV

IOL confirmó:

- `CEDEAR ISHARES CORE S&P 500 ETF`;
- ARS;
- lote 1;
- T1;
- `IVV/IVVD/IVVC`;
- quote/order book;
- histórico diario 2026-09-01..2026-09-14.

BYMA oficial confirma IVV como CEDEAR de ETF habilitado en 2026.

### ACCIONES USA — AAPL NASDAQ

IOL confirmó identidad distinta del CEDEAR local:

- venue NASDAQ;
- tipo `ACCIONES`;
- USD;
- lote 1;
- T1;
- quote/order book.

Regla: nunca mezclar `AAPL NASDAQ` con `AAPL BCBA` por ticker textual.

### CAUCIONES

IOL confirmó capacidad read-only para:

- ARS/USD;
- colocadora/tomadora;
- plazo 1/2/3 días;
- rate;
- due date;
- minimum.

Snapshot representativo:

- colocadora ARS 1d: rate 21.5, mínimo 100000;
- colocadora USD 1d: rate 0.992, mínimo 100;
- tomadora ARS 1d: rate 21.6, mínimo 1000;
- tomadora USD 1d: rate 1.0, mínimo 100.

Estos números son time-sensitive; se registran como evidencia de capability, no como parámetros fijos.

PPI sigue aportando 120 filas `CaucionesOperables`, pero falta mapping semántico completo para READY.

### OPCIONES — GGAL / GFGC7000OC

IOL chain venc. 2026-10-16 confirmó ejemplo:

- call;
- strike 7000;
- bid/ask;
- IV;
- theoretical price;
- delta/gamma/theta/vega/rho;
- volumen;
- stale flag.

Opciones continúan `RESEARCH_ONLY` hasta contract PPI + risk module.

### FCI

IOL listing read-only confirmó al menos:

- `IOLDOLD` / IOL Dólar Ahorro Plus, USD, operable=true en IOL;
- `ADCGLOA` / Adcap Renta Dólar.

Esto no autoriza FCI PPI; PPI debe aportar su propio fund contract/cut-off/settlement.

### FUTUROS

BYMA oficial publica futuros de renta variable sobre GGAL, MELI y SPY, con especificaciones de contrato, mínimo, vencimientos y mark-to-market.

El connector IOL actual no demuestra reader de futuros. PPI contract + margin/guarantee siguen pendientes.

### LEBAC/NOBAC

BCRA mantiene LEBAC como historia de subastas hasta diciembre 2018. No se encontró evidencia current 2026 suficiente para declararlas operables hoy.

Regla: conservar historia, pero `NOT_CURRENTLY_OPERABLE` salvo nueva evidencia PPI + oficial.

## Fuente de verdad por capa

1. PPI: operability + execution contract.
2. IOL: market data + analytics + cross-check.
3. BYMA/Tesoro/BCRA/CNV/emisor: términos oficiales estables.
4. Porota: canonical identity, provenance, reconciliation, risk, state machine y decision.

## Stopper principal actual

El problema ya no es “no tenemos información para analizar renta fija/ON/Letras”. IOL demostró cobertura analítica fuerte en varios ejemplos.

El stopper crítico para ampliar `READY_PAPER_PPI` es completar por familia:

- PPI exact identity;
- availability;
- settlement;
- nominal/minimum;
- quantity step;
- price tick;
- fees;
- horarios;
- requisitos específicos de riesgo/margen/cut-off.

## Solución integral trazada — NUEVO CHECKPOINT

La solución ya no se define como parches por instrumento aislado. Se adopta un pipeline común:

`DISCOVERY -> IDENTITY -> MARKET DATA -> ANALYTICS -> CONTRACT PPI -> RISK -> READY_PAPER_PPI`

Componentes obligatorios:

1. `PPIContractProvider` — contrato PPI read-only.
2. `IOLReadOnlyProvider` — datos/analytics sin métodos mutativos.
3. `OfficialTermsProvider` — BYMA/Tesoro/BCRA/CNV/emisor.
4. `CanonicalIdentityResolver` — evita matching por ticker solo.
5. `EvidenceStore` — provenance por campo.
6. `ReadinessEngine` — cinco gates independientes.
7. `ProviderReconciliation` — staleness/divergence/identity conflicts.
8. `FixedIncomeAnalytics` — fallback Porota cuando IOL no cubre, como D30O6.
9. risk plugins específicos para Opciones y Futuros.
10. state machines separados para FCI y Licitaciones.

Blueprint detallado:

`docs/research/POROTA_INTEGRAL_INSTRUMENT_SOLUTION_BLUEPRINT_2026-09-14.md`

### Orden de implementación adoptado

- `WAVE A`: núcleo reusable — identity, provenance, IOL read-only, PPI contract, readiness, divergence.
- `WAVE B`: spot — GGAL, AAPL CEDEAR, IVV, AAPL USA si PPI soporta.
- `WAVE C`: renta fija — AL30, S30O6, D30O6, YMCJO.
- `WAVE D`: cauciones colocadora/tomadora.
- `WAVE E`: opciones/futuros con motores de riesgo dedicados.
- `WAVE F`: FCI/FCI exterior, licitaciones, índices, legacy.

### Regla de escala

No resolver cientos de especies manualmente. Primero validar un **instrumento patrón por familia**. Una vez que pasa los gates, escalar mediante discovery + normalización + readiness automática al resto de las especies de esa familia.

### Acceptance mínimo por ejemplo patrón

Debe producir:

```text
canonical_id
family
identity_ready
market_data_ready
analytics_ready
contract_ready_ppi
risk_ready
ready_paper_ppi
hold_reasons
field_sources
provider_conflicts
stale_fields
real_orders_sent=0
```

No se permite `ready_paper_ppi=true` si algún gate obligatorio está en false.

## Prioridad de continuación

1. Construir WAVE A sin tocar ejecución real.
2. AL30 PPI contract end-to-end.
3. Cauciones PPI semantics end-to-end.
4. Contract sample PPI de GGAL, AAPL CEDEAR, IVV, S30O6 y YMCJO.
5. identity map + provenance + divergence gate.
6. PPI-vs-IOL historical reconciliation.
7. Futuros/options dedicated risk work.
8. FCI/FCI exterior como flujo no intradía/deferred.

## Regla para próximos chats

Ningún chat debe volver a concluir que PPI “fracasó” o que Porota debe quedar sólo en Acciones + CEDEAR sin revisar este checkpoint y el informe exhaustivo asociado. Tampoco debe declarar nuevas familias READY basándose sólo en IOL analytics.

Todo chat que continúe esta línea debe leer además `POROTA_INTEGRAL_INSTRUMENT_SOLUTION_BLUEPRINT_2026-09-14.md` y mantener el avance por WAVE y por ejemplo patrón.
