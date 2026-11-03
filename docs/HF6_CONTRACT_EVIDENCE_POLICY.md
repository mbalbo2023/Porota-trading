# HF6 contract evidence policy

Every financial field used to make a PAPER instrument operable must retain provider, field, and timestamp provenance. Search results or ticker parsing do not constitute a financial contract. Unsupported or undocumented fields remain fail-closed.

Status ownership:
- POROTA: documented/provider data exists but the adapter/collector is missing.
- PPI_SUPPORT: PPI declares/supports a family but the documented API does not expose the required contract semantics or discovery route.
- PPI_SUPPORT_OR_VERIFIED_ROFEX_SOURCE: futures may be completed only by explicit provider-backed contract/margin data; hardcoded reference tables are never execution inputs.

Caucion cash sweep is a PAPER treasury policy only. It remains HOLD until a provider-backed placing offer supplies identity, currency, term, rate, side, available principal, minimum/step, day-count basis, maturity, quote time and explicit total costs. Only settled free cash after reserves may be allocated; no borrowing or taker cauciones.
