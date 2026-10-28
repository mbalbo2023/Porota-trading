# WS-MOTOR-11 — auditoría de la toma de decisión

## Respuesta a la contradicción comercial

PPI e IOL no operan con “menos información”. Sus plataformas ensamblan varias
capas internas: maestro de especie, formulario, validador pre-trade, mercado,
clearing, backoffice y lifecycle. La API pública o el MCP muestran sólo una
parte. POROTA estaba intentando reconstruir esa unión, pero su gate tenía dos
errores opuestos:

1. descartaba payloads parciales antes de poder complementarlos;
2. exigía campos que su executor PAPER no consumía.

La decisión corregida conserva seguridad financiera y elimina rigidez
artificial: un campo bloquea sólo si tiene un consumidor PAPER concreto y no
puede obtenerse o derivarse de la jerarquía de fuentes.

## Regla de complementación efectiva

| Orden | Fuente | Autoridad | Qué puede completar |
|---:|---|---|---|
| 1 | PPI API/catálogo/XHR | identidad primaria y términos explícitos PPI | ticker, familia, mercado, moneda, settlement, lámina, múltiplo, base nominal |
| 2 | IOL MCP/API | complemento estructurado | analytics, maturity, simulación, chain, fondos, tasas |
| 3 | BYMA/A3/ROFEX | contrato oficial de mercado | lotes estandarizados, multiplier, tick, calendario |
| 4 | Clearing/gestora | riesgo y lifecycle | margen, NAV/fecha, cutoff, rescate |
| 5 | XHR/DOM PPI/IOL | residuo específico del broker | mínimo/step del ticket, premium basis, clase exacta |
| 6 | regla derivada | aritmética verificable | `cash_multiplier=1/quote_basis`; `tick_value=multiplier*tick` |

Ninguna capa borra otra. Los valores se unen por
`family+ticker+market+currency+settlement`. Una contradicción del mismo campo se
conserva y bloquea; una ausencia se completa. Metadatos de captura distintos no
se tratan como conflicto contractual.

## Requisitos OPEN según el consumidor real

| Familia | Requisitos OPEN | Consumidor real | No bloquea OPEN |
|---|---|---|---|
| Acciones/CEDEAR/ETF | identidad PPI completa | `catalog.contract_for`, `PaperBroker`, ledger por moneda | ISIN, ratio descriptivo, price tick, fee por instrumento |
| Bonos/Letras/ON | identidad + cash multiplier + mínimo + step | `InstrumentContract.notional/quantity`, caja, riesgo y P&L | ISIN, maturity/cupón/amortización (EVENT), TIR/duration, sesión duplicada |
| Opción long | identidad + underlying/right/strike/expiry + multiplier + mínimo/step | `InstrumentContract`, pérdida máxima=prima, expiry guard | ejercicio (EVENT), griegas/OI, tick informativo |
| Caución colocadora | identidad/lado/fechas/mínimo/step/base/fee timing + tasa/depth/quote frescos | `CaucionOffer.economics`, `CaucionBook`, caja | fee quote duplicado en ARS: usa `au_fee_schedule`; en USD sigue siendo obligatorio |
| FCI | identidad/clase + moneda + mínimo/step + estado de suscripción | lifecycle PAPER propuesto | NAV/cutoff/rescate pasan a EVENT; manager/custodian enrichment |
| Futuro | identidad + underlying/expiry/multiplier + mínimo/step + márgenes | `InstrumentContract.daily_variation/margin_deficit` y ledger futuro | tick value derivable, trading hours duplicado; settlement method EVENT |

`fee_schedule` no es un dato por instrumento para el motor actual: los costos
PAPER se obtienen de `au_fee_schedule`. `trading_session` tampoco: la admisión
usa `PaperSessionPolicy` y timestamps del `Quote`. Exigirlos de nuevo en
Evidence v2 bloqueaba sin aumentar seguridad.

## Hallazgos recuperados y conectados

- PPI `DatosTecnicos`: `laminaMinima`, `multiploMinimo` y
  `nominalesEnPrecio` ahora llegan a Evidence v2. Los decimales de display
  continúan explícitamente prohibidos como inferencia de step/tick.
- IOL fixed income: maturity/analytics y simulación quedan parciales si no hay
  contrato completo; ya no se descartan.
- IOL options chain: underlying/right/strike/expiry/lote oficial sobreviven aun
  cuando faltan términos PPI del ticket.
- FCI y cauciones estaban en secciones separadas del cache y el adaptador las
  ignoraba. Ahora se procesan, pero sólo se persisten si hay binding PPI exacto.
- El costo de caución ARS ya tiene un consumidor y autoridad central; exigir
  además una cotización de fees era duplicado. USD conserva fee explícito.

## Resultado before/after del gate corregido

- Baseline: 0/20 testigos READY.
- Integración inicial rígida: seguía en 0/20.
- Después de auditar consumidores, complementar campos y atravesar el gate
  real: 2/20: `GD30=READY_PAPER_SPOT` y
  `GFGC6000OC=READY_PAPER_OPTION_LONG`.
- Ningún resultado se extrapola: AL30, otra ON/letra/opción, series ajustadas y
  contratos vecinos conservan sus faltantes propios.
- FCI y futuros muestran simultáneamente `BLOCKED_DATA` y `BLOCKED_EXECUTOR`;
  tener datos no fingirá un lifecycle inexistente.

Esto no declara READY global para BONOS u OPCIONES. Sólo promueve los dos
instrumentos cuya unión de evidencia satisface exactamente la matemática del
executor PAPER existente. `READY_PAPER_CANDIDATE` queda limitado a la frontera
interna Evidence→contrato y no es un resultado final.
