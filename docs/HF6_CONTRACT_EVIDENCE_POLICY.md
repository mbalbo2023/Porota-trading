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

Si la rueda está cerrada, el deploy **técnico** seguro puede hacerse sin quotes
frescas; el QA de Current y Book debe registrar por cada identidad PPI: endpoint,
HTTP/sanitized error, `asof` de fuente, hora de observación, edad contra umbral
válido, asociación unívoca de contrato y `READY_PAPER_*`. Hasta la siguiente
rueda válida, `NO_VERIFICADO` se acompaña de owner, Acción y vencimiento, y
`READY_PAPER` permanece **fail-closed**. Reusar el PR/parche parcial de origen
antes de duplicar la lógica de identidad o frescura.

## P0 desplegado y fuentes complementarias — evidencia 18-Nov-2026

El [único Promote V2 #44442769638](https://github.com/mbalbo2023/Porota-trading/actions/runs/44442769638)
acreditó el **runtime** PAPER (recibo #14492700787); el
[PR #1197](https://github.com/mbalbo2023/Porota-trading/pull/1197) integró el
emisor A670 al `main` de control para solicitar Current/Book a la **sesión PPI
ya autenticada del scanner**. No es una sustitución de la API PPI: los métodos
SDK previos consumían endpoints REST del mismo proveedor. El antiguo camino de
`sample` que abría otro login quedó inaccesible. Ninguno de estos hechos prueba
un GET exitoso ni un `READY_PAPER` por instrumento.

La cadena de un contrato es **PPI identidad primaria exacta de cinco claves →
Current/Book/History con proveedor y reloj → complemento externo para campos
que PPI no acredita → conciliación sin conflicto → readiness PAPER individual**.
Los complementos son rutas **condicionales**, no hechos de disponibilidad ni
permisos para sustituir identidad:

| Familia PAPER | Complemento candidato sólo con evidencia | Nunca inferir |
|---|---|---|
| ACCIONES, CEDEARS, ETFS | IOL, BYMA | Moneda, mercado, settlement o precio a partir del ticker |
| BONOS, LETRAS, OBLIGACIONES | IOL, BYMA | Término, flujo, especie o cotización por semejanza de nombre |
| OPCIONES, FUTUROS | BYMA, A3/ROFEX según contrato verificado | Subyacente, vencimiento, multiplicador, margen o rueda |
| CAUCIONES | BYMA y oferta comprobada del colocador | Tasa ejecutable, plazo, monto libre o costo sin oferta exacta |
| FCI | Fuente primaria/documentación oficial comprobada | Cuotaparte, rescate o fecha de valuación sin marca temporal |

Para cada cinco-clave registrar `provider`, `endpoint`, `HTTP/status`, reloj
de fuente y recepción, antigüedad, campo faltante, complemento elegido o
rechazado con motivo, `can_simulate` y capacidad PAPER. Mercado cerrado permite
`VALIDADO_RUNTIME` sin quote fresco, pero **no promueve** un dato histórico
como Current ni inventa readiness. El scan físico por diez familias se mantiene
pendiente de ventana real; usar estados con owner/RCA y próximo run en #1093.

La cobertura histórica también es independiente de la frescura intradía.
Existe un HOLD explícito en `rc6_source_cycle_dispatch.py::_paper_zero` por
autoridad de DB/WAL, y History v2 exige `rc6_history_cutoff_repair_runs` para
cutoff `2026-09-21` COMPLETE; el resultado registrado fue PARTIAL. No escribir
datos retroactivos, ejecutar reingestas o emitir segundas sesiones PPI para
disfrazarlo. La corrección requiere custodia física del mismo DB, avance
idempotente de series exactas, regresión y recibo en Actions.
