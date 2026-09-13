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
- Bridge PAPER creado: `rc6_caucion_paper_bridge.py`.
- Producer fail-closed de obligaciones PAPER creado: `rc6_paper_obligation_snapshot.py`.
- Gate agregado de frescura creado: `rc6_caucion_fresh_data_agent.py`.
- Workflow `34778094293`: SUCCESS; 36 tests PASS sobre adapter + contrato y pruebas de fail-closed.
- Workflow `34778116633`: SUCCESS; dejó documentado el gap de integración previo.
- Workflow `34778690161`: SUCCESS; suite focalizada de bridge/freshness/obligaciones/sweep, sin red ni rutas reales.
- Workflow `34778816227`: SUCCESS; **67 tests PASS**, incluyendo prueba E2E determinística `fresh GREEN -> PLACED_SIMULATED`, stale -> HOLD, obligaciones incompletas -> HOLD; rutas reales siguen bloqueadas.

## PENDIENTES PARA READY_PAPER DE CAUCIONES

1. Integrar el gate contractual/dinámico y el freshness gate con el runtime vivo, sin relajar TTL.
2. Trazar dashboard/READY para impedir que muestre READY cuando el freshness gate esté rojo.
3. Durante rueda activa, obtener evidencia fresca real de CAUCIONES y validar semántica lado/profundidad/costos. Si sigue ambigua: HOLD.
4. Recalcular las 10 identidades; objetivo 10/10 sólo si cada una cumple contrato + dinámica + integración + tests.
5. Verificar en el runtime desplegado heartbeat/timestamps de `current/book/intraday` para CAUCIONES y `real_orders_sent=0`.
6. Demostrar en vivo o con evidencia runtime equivalente el ciclo fresh -> stale/missing -> HOLD -> fresh, sin afectar producción real.
7. Verificar que dashboard y clasificador READY consumen la misma verdad agregada `CAUCION_FRESH_DATA_AGENT_GREEN` y nunca un criterio más permisivo.
8. Prueba final de rueda activa y cierre de `READY_PAPER` sólo con todos los gates verdes.

### PENDIENTE NUEVO A — LÓGICA DE OPORTUNIDADES INTRADÍA

Documentar y validar cómo operará CAUCIONES durante toda la rueda, no sólo el cash-sweep EOD:
- cómo detecta una oportunidad de tasa excepcionalmente atractiva;
- qué universo/plazos/monedas evalúa en cada ciclo;
- cada cuánto evalúa y si la evaluación se dispara por refresh/evento o scheduler;
- qué compara: TNA neta de costos, profundidad disponible, plazo, calendario, liquidez futura, obligaciones/reserva, riesgo, concentración y costo de oportunidad frente a otras alternativas;
- cómo evita perseguir un pico transitorio, usar book stale o duplicar la misma profundidad;
- cuándo decide `HOLD`, `PAPER_CANDIDATE` o `PLACED_SIMULATED`;
- cómo trata oportunidades intradía versus el sweep de caja ociosa cercano al cierre;
- persistencia/auditoría de la razón exacta de cada aceptación/rechazo.

**Estado actual:** PENDIENTE DE DISEÑO/AUDITORÍA. El código existente probado cubre el cash-sweep PAPER y sus gates; no se declara todavía que exista un detector intradía completo de oportunidades de caución.

### PENDIENTE NUEVO B — HORARIOS DE CAUCIONES

Verificar y congelar contractualmente los horarios efectivos de negociación de CAUCIONES y cualquier excepción por moneda, plazo, segmento o broker/PPI.

**Evidencia preliminar verificada:**
- La tabla oficial vigente enlazada por BYMA (Comunicado 18782, vigencia desde 28/07/2025) indica para PPT `Negociación Regular – Pase y Caución: 10:30 a 17:00 hs` GMT-3.
- La página actual de horarios de BYMA sigue enlazando esa tabla.
- PPI Support aloja copia del mismo Comunicado 18782.
- No se debe asumir todavía que un cutoff operativo propio de PPI, una caución en USD o una condición excepcional de BYMA coincidan exactamente con el cierre general sin validación específica.

**Pendiente exacto:** confirmar si existen cutoffs PPI/operativos distintos para ARS vs USD, colocadora vs tomadora, algún plazo específico o eventos especiales; incorporar fuente/versionado al gate `calendar/cutoff` y tests.

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
- 🟢 Bridge evidencia -> PAPER sweep: creado y probado.
- 🟢 ObligationSnapshot PAPER: producer creado y probado fail-closed.
- 🟢 Fresh-data aggregate gate: creado y probado determinísticamente.
- 🟢 E2E sintético PAPER: fresh GREEN -> `PLACED_SIMULATED`; stale/obligaciones incompletas -> HOLD; 67 tests PASS.
- 🟡 Lógica completa de oportunidades intradía: pendiente de diseño/auditoría.
- 🟡 Horarios/cutoffs por modalidad/moneda/PPI: verificación en curso; BYMA general 10:30-17:00 ya evidenciado.
- 🟡 Fresh-data gate -> runtime/dashboard: pendiente de integración viva.
- 🔴 Dinámica fresca de rueda real: pendiente hasta mercado activo.
- 🔴 CAUCIONES `can_simulate`: no promover 10/10 hasta evidencia viva e integración READY/dashboard.
- 🔴 READY_PAPER operativo end-to-end: todavía no demostrado en runtime desplegado.

## ÚLTIMO PASO CONFIRMADO

Se cerró el circuito determinístico de prueba desde evidencia canónica + freshness GREEN + obligaciones PAPER reconciliadas hasta una colocación `PLACED_SIMULATED`, manteniendo `real_orders_sent=0` y rutas reales bloqueadas. También quedó probado que stale/missing e incompletitud de obligaciones producen HOLD. Esto prueba la lógica de integración en CI, no reemplaza la evidencia dinámica de rueda ni el deploy/runtime final.

## SIGUIENTE ACCIÓN EXACTA

Avanzar en paralelo, sin tocar scraper/ingesta masiva:
- auditar/diseñar detector intradía de oportunidades y su cadencia real;
- cerrar horarios/cutoffs oficiales BYMA + PPI por modalidad/moneda/plazo;
- integrar/persistir `CAUCION_FRESH_DATA_AGENT_GREEN` en runtime y dashboard/READY;
- obtener prueba read-only del runtime desplegado (workers, heartbeat, timestamps, safety);
- dejar lista la prueba de rueda activa real de las 10 identidades.
