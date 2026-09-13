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

## AUDITORÍA RUNTIME / CADENCIA CAUCIONES — HALLAZGOS 2026-09-13

### current + book

`bf_production_paper_observer.py` ya incluye CAUCIONES en el universo observable cuando el catálogo la declara `AVAILABLE` y usa `settlement=INMEDIATA`. En rueda abierta, por identidad seleccionada, ejecuta `reader.current(...)` + `reader.book(...)`, persiste la quote y luego ejecuta el motor PAPER.

Cadencia configurada del observer: `PAPER_OBSERVER_INTERVAL_SECONDS`, default **60 s** (mínimo 15 s), más el tiempo real de las llamadas PPI. **Importante:** CAUCIONES no pertenece al foco prioritario actual; entra por rotación del universo, por lo que no existe hoy garantía de refresco de cada caución cada 60 s.

### intraday

`cf_intraday_scalping.py` admite `CAUCIONES` como familia para **recolección** de `MarketData/Intraday`. Su worker usa `PAPER_INTRADAY_SCAN_SECONDS`, default **180 s**, lote default 24 y rotación de universo.

Pero `evaluate_candidate()` exige `capability == READY_PAPER_SPOT`. El catálogo clasifica CAUCIONES como `NEEDS_CAUCION_TERMS`, no `READY_PAPER_SPOT`. Por lo tanto:
- CAUCIONES puede ser observada/ingerida por el collector intradiario;
- **NO puede hoy transformarse en candidato de scalping**;
- el scanner spot existente NO es un detector de oportunidad intradía de CAUCIONES.

### supervisor runtime

`bv_paper_runtime.py` supervisa scanner, exit-reader, notificaciones, candles e intraday-scalping. **No existe aún un child dedicado a CAUCIONES** para freshness/oportunidades/sweep. Éste es un gap real de integración, no de scraping.

### dashboard / READY

`bg_paper_dashboard.py::_family_ux_snapshot()` determina visualmente `READY_PAPER` usando `catalog_family_coverage.ready_paper_count`. Ese conteo proviene de `READY_PAPER_SPOT` y **no consume todavía `CAUCION_FRESH_DATA_AGENT_GREEN`**.

Hoy esto no promociona erróneamente CAUCIONES porque el catálogo la mantiene en `NEEDS_CAUCION_TERMS` y su ready count es 0. Sin embargo, antes de habilitar el ejecutor especializado es obligatorio cambiar la cadena de verdad: el dashboard/READY de CAUCIONES debe depender del gate especializado fresco, no de un contador spot.

### calendario centralizado

`co_market_sessions_hf6.py` centraliza BYMA spot `[10:30,17:00)` pero su set `BYMA_PAPER_SPOT_FAMILIES` **no incluye CAUCIONES**; por tanto `session_for('BYMA','CAUCIONES')` hoy devuelve `None`. A la vez, `rc6_cauciones_contract.py` conserva una ventana local 10:30–17:00. Esta duplicación debe resolverse después de cerrar evidencia oficial/PPI de horarios y cutoffs; no se agregará CAUCIONES al mapa central “por intuición”.

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

**Estado auditado:** el collector actual puede traer `intraday` de CAUCIONES cada ~180 s cuando entra en el lote, y el observer puede traer `current/book`; **no existe todavía un detector intradía especializado de oportunidades de caución**. No se reutilizará el scanner spot fingiendo compatibilidad.

### PENDIENTE NUEVO B — HORARIOS DE CAUCIONES

Verificar y congelar contractualmente los horarios efectivos de negociación de CAUCIONES y cualquier excepción por moneda, plazo, segmento o broker/PPI.

**Evidencia verificada:**
- BYMA tiene como comunicado vigente de horarios el **Comunicado 19016**, fechado 01/09/2026.
- La ventana pública general vigente para negociación BYMA continúa en torno a **10:30–17:00**; la modificación desde 28/07/2025 incluyó explícitamente Caución.
- PPI publica cauciones en pesos y dólares de 1 a 120 días; su página pública de cotizaciones se actualiza cada 15 minutos, pero eso NO define el cutoff de carga de órdenes ni sustituye el market data autenticado.
- La Circular BYMA 3567 publicada 09/09/2026 trata Caución en Dólares/asignación de volúmenes; no debe reinterpretarse automáticamente como horario.
- No se halló todavía en documentación pública indexada una regla PPI específica que demuestre un cutoff distinto por ARS/USD/plazo. Ausencia de evidencia NO equivale a “no existe”.

**Pendiente exacto:** obtener/confirmar el detalle del 19016 y cualquier cutoff operativo PPI para colocadora/tomadora, ARS/USD y excepciones; incorporar fuente/versionado al gate `calendar/cutoff` y tests. Hasta entonces se usa HOLD ante una ventana específica no demostrada.

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
- 🟢 Auditoría de cadencias/gaps: cerrada; current/book ~60 s + rotación, intraday ~180 s + rotación, sin detector caución especializado.
- 🟡 Lógica completa de oportunidades intradía: diseño/implementación especializada pendiente.
- 🟡 Horarios/cutoffs por modalidad/moneda/PPI: BYMA general 10:30–17:00 evidenciado; cutoff PPI específico pendiente.
- 🟡 Fresh-data gate -> runtime/dashboard: gap confirmado, pendiente de integración viva.
- 🔴 Dinámica fresca de rueda real: pendiente hasta mercado activo.
- 🔴 CAUCIONES `can_simulate`: no promover 10/10 hasta evidencia viva e integración READY/dashboard.
- 🔴 READY_PAPER operativo end-to-end: todavía no demostrado en runtime desplegado.

## ÚLTIMO PASO CONFIRMADO

Se cerró el circuito determinístico de prueba desde evidencia canónica + freshness GREEN + obligaciones PAPER reconciliadas hasta una colocación `PLACED_SIMULATED`, manteniendo `real_orders_sent=0` y rutas reales bloqueadas. También quedó probado que stale/missing e incompletitud de obligaciones producen HOLD. La auditoría posterior confirmó que el scanner intradía existente recolecta CAUCIONES pero no las evalúa como oportunidad, que no hay worker dedicado de cauciones y que el dashboard READY todavía no consume el gate agregado.

## SIGUIENTE ACCIÓN EXACTA

Avanzar en paralelo, sin tocar scraper/ingesta masiva:
- definir/implementar detector intradía especializado de oportunidades con política explícita y fail-closed;
- cerrar horarios/cutoffs oficiales BYMA + PPI por modalidad/moneda/plazo;
- persistir `CAUCION_FRESH_DATA_AGENT_GREEN` como estado runtime auditable;
- integrar esa verdad especializada al dashboard/READY;
- obtener prueba read-only del runtime desplegado (workers, heartbeat, timestamps, safety);
- dejar lista la prueba de rueda activa real de las 10 identidades.
