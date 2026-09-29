# WS Motor 14 — políticas PAPER separadas de términos del broker

Estas reglas sólo gobiernan `PRODUCTION_PAPER / SIMULATION`. No describen
condiciones comerciales de PPI, IOL ni A3 y no habilitan rutas reales.

| Familia | Dato del proveedor | Policy PAPER | Guard |
|---|---|---|---|
| Renta fija | `nominalInPrice` / `units_per_lot` | `paper_cash_multiplier=1/base`; cantidad mínima y step de simulación = 1 nominal | identidad PPI exacta, familia nominal, base positiva, sin evidencia contraria |
| FCI | identidad PPI AVAILABLE, moneda | `INTERNAL_RISK_BUDGET_BY_AMOUNT`; unidad contable 0,01 | monto positivo dentro del risk engine; NAV sólo al evento |
| Opción long estándar | chain IOL exacta + regla BYMA de acción | 1 contrato; multiplicador 100 | acción local, T0, serie no ajustada; CEDEAR/especial queda pausada |
| Caución colocadora | tasa, plazo, vencimiento y mínimo publicados | `CONSERVATIVE_NOTIONAL_CAP`; sin suponer depth | cap igual al mínimo publicado hasta que el risk engine imponga uno menor |
| Futuro | identidad y contrato A3/ROFEX | `CONSERVATIVE_NOTIONAL_RATE=1`; reserva 100% del nocional | multiplicador/vencimiento/subyacente verificados; margen broker puede quedar `NO_VERIFICADO` |

Campos `broker_*` ausentes permanecen `NO_VERIFICADO`. Los campos `paper_*`
conservan provenance propio y nunca se presentan como términos del broker.

FCI usa el lifecycle:

`SUBSCRIBE_REQUESTED -> PENDING -> NAV_APPLIED -> SETTLED`.

Una caída o respuesta vacía de IOL conserva el último snapshot válido. Los
estados explícitos son `LIVE_FRESH`, `CACHE_FRESH`, `CACHE_STALE`,
`SOURCE_UNAVAILABLE` y `EMPTY_UNEXPECTED`; la metadata estática no vence con
el TTL de una cotización dinámica.
