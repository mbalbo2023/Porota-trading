# Contrato de evidencia por instrumento

Referencia técnica de identidad y procedencia. Las instrucciones de trabajo
están en [AGENTS.md](../AGENTS.md); este documento no acredita readiness ni
establece pasos de despliegue adicionales.

Every financial field used to make a PAPER instrument operable must retain provider, field, and timestamp provenance. Search results or ticker parsing do not constitute a financial contract. Unsupported or undocumented fields remain fail-closed.

Status ownership:
- POROTA: documented/provider data exists but the adapter/collector is missing.
- PPI_SUPPORT: PPI declares/supports a family but the documented API does not expose the required contract semantics or discovery route.
- PPI_SUPPORT_OR_VERIFIED_ROFEX_SOURCE: futures may be completed only by explicit provider-backed contract/margin data; hardcoded reference tables are never execution inputs.

Caucion cash sweep is a PAPER treasury policy only. It remains HOLD until a provider-backed placing offer supplies identity, currency, term, rate, side, available principal, minimum/step, day-count basis, maturity, quote time and explicit total costs. Only settled free cash after reserves may be allocated; no borrowing or taker cauciones.

## REST PPI y readiness por identidad — revisión 18-Nov-2026

Current, Book, Intraday, SearchInstrument y History son parte del circuito REST
existente; login OK no acredita GET ni frescura. SearchInstrument puede no devolver
settlement: `REQUEST_CANDIDATE` es un parámetro solicitado, **no una quinta
clave atestiguada por PPI**. Una sonda `OBSERVE_ONLY` puede medir identidad única,
pero no promueve `can_simulate`, `READY_PAPER` ni caución. La corrección de
consumidores está en [PR #1182 DRAFT](https://github.com/mbalbo2023/Porota-trading/pull/1182)
al corte; validar HEAD/CI/runtime antes de acreditarla. El snapshot 0/17.283 READY
no se corrige por documentación: requiere prueba física instrumento por instrumento.
