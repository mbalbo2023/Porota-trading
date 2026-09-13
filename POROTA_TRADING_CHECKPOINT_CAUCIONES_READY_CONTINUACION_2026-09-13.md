# POROTA TRADING — CHECKPOINT CAUCIONES READY / CONTINUACIÓN 2026-09-13

**Estado:** ACTIVO / CONTINUIDAD CAUCIONES  
**Rama de trabajo válida:** `ops/rc6-ppi-web-residual-ready-20260913`  
**Modo obligatorio:** `PRODUCTION_PAPER`  
**Órdenes reales:** `0`  

## REGLA DE CONTINUIDAD — SCRAPER / INGESTA

**NO REHACER EL SCRAPER. NO RECREARLO. NO INVENTAR OTRO SCRAPER. NO RELANZAR LA INGESTA MASIVA.**

El scraper/pipeline PPI/PPI Web ya fue ejecutado y funcionó para la ingesta realizada. Existe evidencia concreta de información obtenida previamente por PPI API, PPI Web/XHR autenticado y por los collectors existentes. El trabajo pendiente de CAUCIONES no consiste en volver a construir scraping histórico, sino en reutilizar la infraestructura y la evidencia existentes y completar únicamente evidencia contractual/dinámica residual cuando sea necesaria.

La ingesta histórica masiva se considera **cerrada**. Sólo están permitidas consultas/capturas residuales, dirigidas, read-only y justificadas por un campo faltante concreto. Ninguna captura residual habilita automáticamente `can_simulate` ni `READY_PAPER`.

## FAMILIAS FUERA DEL TARGET INMEDIATO

Confirmado por el checkpoint canónico previo: quedan fuera del target inmediato, de forma **reversible**, las familias:
- `FCI`
- `FCI_EXTERIOR`
- `OPCIONES`

No borrar código, datos ni históricos de esas familias. No asumir ninguna otra exclusión sin evidencia/decisión explícita.

## EVIDENCIA YA OBTENIDA — NO REPETIR

- PPI API full-family histórico: corrida masiva cerrada sobre 1960 identidades.
- PPI Web residual: 642/642 terminales; no repetir corrida completa.
- CAUCIONES PPI API read-only: 10 identidades detectadas (`PESOS1/2/7/30/120`, `DOLAR1/2/7/30/120`).
- PPI config confirma `CAUCIONES`, BYMA, settlements y operación `COLOCAR-CAUCION`.
- Shapes `current`, `book` e `intraday` ya observados.
- Cash-sweep PAPER existente ya localizado: `df_caucion_end_of_day_sweep_hf6.py`, `di_caucion_cash_sweep_runtime_hf6.py`, `bt_caucion_paper.py`, `ca_caucion_allocator.py`.
- `CASH_SWEEP_ORDER_ROUTING_ALLOWED=False`.
- Adaptador fail-closed creado: `rc6_caucion_offer_adapter.py`.
- Contrato fail-closed corregido: ya no supone `bids=colocadora`, `price=TNA` ni `quantity=principal` sin prueba semántica explícita.
- Workflow `34778094293`: SUCCESS; 36 tests PASS sobre adapter + contrato y pruebas de fail-closed.
- Workflow `34778116633`: SUCCESS; confirmó que aún no había call-sites productivos de `offer_from_canonical_snapshot`, `run_paper_sweep` ni `ObligationSnapshot`.

## PENDIENTES PARA READY_PAPER DE CAUCIONES

1. Crear el bridge PAPER mínimo entre evidencia canónica validada y `run_paper_sweep` existente, sin red y sin capacidad de órdenes reales.
2. Probar bridge positivo y negativos fail-closed.
3. Crear/validar producer de `ObligationSnapshot` desde obligaciones reales del ledger PAPER, sin porcentaje fijo y sin inventar obligaciones.
4. Integrar el gate contractual/dinámico de `cq_family_contract_rules_hf6.py` con el bridge.
5. Demostrar stale/missing -> HOLD y fresh/valid -> PAPER candidate/PLACED_SIMULATED en test determinístico.
6. Trazar dashboard/READY para impedir que muestre READY cuando el freshness gate esté rojo.
7. Durante rueda activa, obtener evidencia fresca de CAUCIONES y validar semántica lado/profundidad/costos. Si sigue ambigua: HOLD.
8. Recalcular las 10 identidades; objetivo 10/10 sólo si cada una cumple contrato + dinámica + integración + tests.

## TTL CANÓNICOS CAUCIONES

De `cq_family_contract_rules_hf6.py`:
- `tna`: 5 min
- `available_principal`: 5 min
- `market_session_state`: 5 min
- `operable`: 15 min
- `expiry_at`: 15 min

El bridge no puede relajar estos TTL.

## SEMÁFORO

- 🟢 Scraper/ingesta histórica: CERRADO; no rehacer.
- 🟢 Infraestructura collector existente: reutilizar; no crear paralela.
- 🟢 Universo CAUCIONES: 10/10 observado.
- 🟢 Planner/orquestador/allocator PAPER: existen.
- 🟢 Orden real: bloqueada.
- 🟢 Adapter canónico fail-closed: creado y probado.
- 🟢 Semántica cruda peligrosa: bloqueada por código.
- 🟡 Bridge evidencia -> PAPER sweep: en implementación.
- 🟡 ObligationSnapshot productivo: pendiente.
- 🟡 Fresh-data gate -> bridge/dashboard: pendiente.
- 🔴 Dinámica fresca de rueda: pendiente hasta mercado activo.
- 🔴 CAUCIONES `can_simulate`: 0/10 hasta completar gates.
- 🔴 READY_PAPER end-to-end: todavía no demostrado.

## ÚLTIMO PASO CONFIRMADO

Se cerró la corrección de seguridad que impide convertir datos crudos PPI en una caución ejecutable por suposiciones semánticas. Adapter + contract tests están verdes y la arquitectura PAPER existente fue auditada. Se confirmó que faltan call-sites productivos que conecten el adapter y el cash-sweep y que `ObligationSnapshot` todavía no tiene producer productivo.

## SIGUIENTE ACCIÓN EXACTA

Avanzar en paralelo, sin tocar scraper/ingesta masiva:
- bridge PAPER canónico + tests;
- producer fail-closed de `ObligationSnapshot` + tests;
- integración de gate contractual/dinámico y freshness con bridge;
- traza de READY/dashboard;
- workflow CI focalizado end-to-end;
- dejar preparada la única prueba que requiere rueda activa: evidencia fresca real de CAUCIONES.
