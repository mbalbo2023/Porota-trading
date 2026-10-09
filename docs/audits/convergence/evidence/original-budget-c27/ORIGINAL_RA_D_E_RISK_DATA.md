# Reauditoría independiente RA-D / RA-E — riesgo, accounting, concurrencia y autoridad de datos

Fecha de corte: 2026-10-04 UTC  
Objeto exclusivo: PR #466, commit `c27dfd963c4fe83465c0f2105347e974fbbe6356`, tree `bf3cf193434641aa89e4c746b26c77aec5d1d2b2`  
Modo: `READ_ONLY`; probes locales sintéticos `PRODUCTION_PAPER`/`SIMULATED`; `real_orders_sent=0`  
Restricciones observadas: sin fixes, commits, merge, deploy, mutación de runtime ni acceso a PPI Watch.

## Dictamen

**FAIL / NO-GO para merge o deploy del candidato #466.** Encontré un bloqueo **P1** de liveness de salidas: el runtime conserva una identidad económica de cinco partes en la posición y en el índice de snapshots, pero dos lectores reales la reducen a tres partes. Una cotización posterior de otro mercado/moneda con el mismo ticker puede ocultar el libro exacto que ya cruzó el stop; spot queda `OPEN` y FUTUROS queda `ACTIVE`.

También verifiqué dos defectos **P2**: el presupuesto de riesgo de FUTUROS no es punto-en-tiempo aunque su API recibe `at`, y la idempotencia genérica acepta como retry un payload económicamente distinto. La jerarquía PPI > IOL > BYMA y el carácter complementario/no-operable de IOL/BYMA resistieron los ataques ejecutados, pero existe una contradicción **P3** de observabilidad: una fila exclusivamente BYMA se marca `shadow_promotion=true` aun cuando no es elegible ni tiene autoridad.

Que el P1 principal ya exista parcialmente en la base productiva no lo excluye: el objeto #466 conserva la ruta spot defectuosa y extiende su alcance a FUTUROS. Ningún GREEN, matrix o attestation refuta los contraejemplos sobre callers reales.

| ID | Severidad | Área | Resultado | Procedencia |
| --- | --- | --- | --- | --- |
| RA-DE-01 | **P1** | Identidad de quote / stops / exits | Libro exacto ejecutable queda oculto por otra moneda/mercado; spot y futuro no cierran | Raíz heredada en base; blast radius FUTUROS en #466 |
| RA-D-02 | **P2** | Riesgo concurrente punto-en-tiempo | El mismo `at` devuelve distinta capacidad después de un cierre futuro | Implementación FUTUROS presente en #466 |
| RA-D-03 | **P2, alcance hoy limitado** | Idempotencia financiera | Mismo `event_id` con monto/tiempo distintos se acepta como retry | Heredado; caller FCI existe, wiring runtime no demostrado |
| RA-E-04 | **P3** | Claims/telemetría de fuente | Complemento BYMA-only publica `shadow_promotion=true` | Heredado/continuado |

## Método y límites de evidencia

- Leí íntegramente #469 y las autoridades #458, #460, #462, #464 y #465, además de `AGENTS.md` y `ops/policy/porota-policy.yaml`. Sus conclusiones se trataron como hipótesis, no como evidencia.
- Inspeccioné callers de producción del tree congelado y ejecuté probes propios sobre una copia creada por `git archive` en `/tmp/porota-ra-de-c27dfd96-Ap68PX`.
- Todos los estados financieros usados por los probes viven en SQLite temporales. Los libros fueron sintéticos; no hubo red, SDK de broker ni credenciales.
- La base productiva observada para comparación fue `da697c6e6c2274579f9e4a112fabc4327475dd35`. La consulta triple de `latest_quote`, el caller `tick()` sin quotes y la lookup triple del catálogo ya están allí. #466 modifica varios de esos módulos, pero no corrige esa raíz y agrega FUTUROS al recorrido.
- No se certifica aquí el host, el proveedor en rueda, el artifact ni resultados económicos externos. Un import no ejecutable por faltar `requests` se informa como limitación de entorno, no como GREEN ni como falla de producto.

## RA-DE-01 — P1: colisión de identidad en la ruta real de salidas

### Código y caller alcanzable

| Paso | Path / símbolo / blob | Evidencia |
| --- | --- | --- |
| Persistencia | `be_paper_engine.py:403,436-450`, `PaperStore.add_quote`; blob `d186691be64ffd7d55a1e60638f1588dc8acdddb` | Se persisten `currency` y `market`; el índice es `(symbol, asset_class, settlement, currency, market, id)` |
| Lectura | `be_paper_engine.py:452-466`, `PaperStore.latest_quote`; mismo blob | SQL filtra sólo `(symbol, asset_class, settlement)` y ordena por `id DESC` |
| Supervisor spot | `bv_paper_runtime.py:149-163`, `run_clock`; blob `6eddabf60e455f72114835fabbc14862138f252b` | Invoca `supervisor.tick()` sin mapa de quotes |
| Resolución spot | `bm_exit_supervisor.py:242-254`, `PositionExitSupervisor.tick`; blob `a82bba596966365ad92181b5f13118950905343b` | Al no recibir quotes usa `self.store.latest_quote(p)` |
| Supervisor FUTUROS | `be_paper_engine.py:1671-1677`, `PaperBroker.supervise_futures` | Usa el mismo `latest_quote` truncado |
| Recolección de salida | `bv_paper_runtime.py:172-214`, `collect_exit_books` | Construye identidad de cinco partes sólo para budget scope, pero `reader.book` y catálogo reciben tres partes |
| Catálogo | `bu_instrument_catalog.py:594-635`, `lookup`; blob `1fc91ac2e9190478ed566fd640a78f1ffbfeaece` | Firma y consulta `(symbol, kind, settlement)`; ante dos mercados/monedas puede devolver `None` |
| Riesgo diario | `bw_daily_risk.py:166-177`, `DailyRisk.evaluate`; blob `ee30eb28c9c519e4cdf645054f336890914371e2` | Reutiliza `latest_quote`; el cruce induce `stale=True` aun existiendo libro exacto |

La validación posterior de identidad evita ejecutar con el instrumento equivocado, pero no recupera el libro exacto. El resultado es fail-closed para la cotización cruzada y simultáneamente **fail-open respecto de la exposición**: no se vende/cierra la posición que ya atravesó el stop.

### Probe spot exacto

Fixture:

```text
posición abierta vía PaperBroker._open:
  (DUPL, ACCIONES, INMEDIATA, ARS, BYMA)
stop_price = 98.019600

market_snapshots en orden de inserción:
  id=1 ARS/BYMA   bid=99
  id=2 ARS/BYMA   bid=90   <- libro exacto; cruza stop
  id=3 USD/NASDAQ bid=200  <- mismo ticker/clase/plazo, otra identidad
```

Output exacto:

```json
{
  "latest_quote_returned_identity": ["DUPL", "ACCIONES", "INMEDIATA", "USD", "NASDAQ"],
  "supervisor_tick_verdict": "WATCH_IDENTITY_MISMATCH",
  "position_status_after_tick": "OPEN"
}
```

El probe no insertó manualmente la posición: la abrió con `PaperBroker._open` y llamó `PositionExitSupervisor.tick()` sin quotes, igual que `bv_paper_runtime.run_clock`.

### Probe FUTUROS exacto

Fixture y output:

```json
{
  "position_identity": ["DLR/OCT26", "FUTUROS", "INMEDIATA", "ARS", "A3"],
  "configured_stop": "1490",
  "exact_bid": "1400",
  "latest_quote_returned_currency_market": ["USD", "NASDAQ"],
  "status_after_runtime_supervise_futures": "ACTIVE",
  "status_after_direct_exact_quote": "CLOSED"
}
```

El control positivo importa: con el mismo libro exacto pasado directamente a `_on_future_quote`, la posición cerró. Por tanto el defecto no está en la regla de stop sino en la selección del snapshot.

### Probe del lector de exits y catálogo ambiguo

Fixture:

```text
financial_instrument_catalog:
  (DUPL, ACCIONES, BYMA, ARS, INMEDIATA)
  (DUPL, ACCIONES, A3, USD, INMEDIATA)
lookup(store, DUPL, ACCIONES, INMEDIATA) -> None
Reader.book sintético -> bid=90, fresco y cruzando el stop
```

Output exacto:

```json
{
  "collector_failures": 1,
  "persisted_latest_currency_market": ["UNKNOWN", "UNKNOWN"],
  "exit_book_event": "EXIT_BOOK_ERROR: ValueError;stage=VALIDATE_QUOTE",
  "supervisor_tick_verdict": "WATCH_IDENTITY_MISMATCH",
  "position_status_after_tick": "OPEN"
}
```

Así, incluso sin la colisión de snapshots, el collector real puede degradar silenciosamente un libro válido a `UNKNOWN/UNKNOWN` porque el caller ya conoce `p.market`/`p.currency` pero no los pasa al catálogo ni liga la respuesta del broker a esa identidad durable.

### Impacto

- Incumplimiento de stop-loss, take-profit, max-hold y cierre EOD cuando otra identidad es la última fila del triple.
- FUTUROS mantiene margen/exposición y puede saltar el stop aun con el libro exacto disponible.
- `DailyRisk` y `mark_equity` pueden marcar stale/degraded o valorar con el quote equivocado antes de rechazarlo.
- El riesgo es mayor con tickers homónimos entre mercados, cambios de catálogo, instrumentos duales o respuestas parciales.
- La garantía “EXIT indelegable” de F-01 se limita al presupuesto/caché upstream: conservar cuota por identidad no sirve si el consumer downstream vuelve a colapsarla.
- F-04 puede auditar correctamente la procedencia en SHADOW y aun así la ruta factual de ejecución lee otra identidad.

### Gap de tests

`test_full_identity_native_book_cache_does_not_collapse_same_ticker` cubre la caché/budget upstream, no `market_snapshots`, `PaperStore.latest_quote`, `collect_exit_books`, `PositionExitSupervisor.tick` ni `supervise_futures`. No encontré un test end-to-end con mismo ticker/clase/plazo, dos monedas/mercados, libro exacto que cruza stop y quote ajeno insertado después.

### RCA / FIX / GUARD / TEST recomendado — no implementado

- **RCA:** identidad canónica de cinco partes se reduce al cruzar fronteras de storage/catalog/reader; el índice correcto no coincide con el predicado SQL.
- **FIX:** exigir `(symbol, asset_class, settlement, currency, market)` en `latest_quote` y en la API de catálogo/reader para exits; ligar la respuesta a la identidad durable de la posición.
- **GUARD:** persistir por identidad también una “última respuesta inválida/ausente”. Corregir sólo el `WHERE` no debe resucitar silenciosamente un quote exacto viejo después de una respuesta exacta más nueva pero vacía o inválida.
- **TEST:** callers reales spot y FUTUROS; stop/target/max-hold/EOD; quote exacto viejo + ajeno nuevo; quote exacto válido + exacto inválido nuevo; moneda/mercado/plazo conflictivos; restart y carreras multiproceso.

## RA-D-02 — P2: `portfolio_capacity(at)` no es punto-en-tiempo para FUTUROS

### Código y contrato observado

`dh_paper_dynamic_risk_gate_hf6.py:109-182`, `portfolio_capacity`; blob `515fb7743088ff9f4801880d3b4332fb6c2f1ca7`:

- normaliza el cutoff `at`;
- spot usa `positions_at(at)` y pérdidas realizadas con cutoff;
- FUTUROS itera el estado **actual** de `paper_future_positions`;
- si la fila actualmente dice `CLOSED`, omite el riesgo abierto histórico salvo que el cierre caiga dentro del corte.

Existe ya un patrón correcto en `rc6_paper_family_lifecycle.py:728-810`, `future_risk_snapshot`: reconstruye estado desde eventos `occurred_at <= at` en vez de confiar en `status` actual.

Callers económicos reales:

- `be_paper_engine.py:1262-1269`, admisión FUTUROS;
- `be_paper_engine.py:1373-1379`, sizing FUTUROS;
- `be_paper_engine.py:1788-1798`, sizing spot;
- llamadas posteriores en la apertura spot para verificación concurrente.

El parámetro `at`, el uso punto-en-tiempo de spot y la función paralela `future_risk_snapshot` hacen razonable tratar el corte como contrato de API. No elevo a P1 porque los guards finales observados detectan clock rollback/eventos posteriores y no demostré una mutación económica real aprovechable mediante este rewind.

### Probe exacto

```json
{
  "as_of": "2026-10-05T12:00:00-03:00",
  "later_close_at": "2026-10-05T12:01:00-03:00",
  "before_later_close": {
    "open_stop_risk": "1500100",
    "realized_loss_consumed": "0",
    "remaining_before_candidate": "0"
  },
  "after_later_close_same_asof": {
    "open_stop_risk": "0",
    "realized_loss_consumed": "0",
    "remaining_before_candidate": "1500000.0"
  },
  "same_asof_changed": true
}
```

El mismo cutoff cambió sólo porque después se cerró la posición. Esto rompe reproducibilidad de auditoría/backtest y puede falsear la explicación de por qué una decisión histórica fue admitida o rechazada.

### Gap y recomendación — no implementada

- Los tests históricos focales ejercitan `future_risk_snapshot`, no `portfolio_capacity` después de cerrar y volver a consultar el mismo corte.
- **FIX:** derivar apertura/cierre/variación/mark de FUTUROS al `at` desde eventos durables, idealmente reutilizando una única reconstrucción canónica.
- **GUARD:** propiedad de inmutabilidad: eventos estrictamente posteriores no pueden cambiar un snapshot de riesgo anterior.
- **TEST:** mismo cutoff antes/después de close, variation y mark posteriores; restart; frontera de día Buenos Aires; monedas ARS/USD; acceso concurrente.

## RA-D-03 — P2 limitado: retry no equivalente aceptado como idempotente

### Código y reachability

`rc6_paper_family_lifecycle.py:529-597`, `apply_paper_event`; blob `7f2827b602d4c03d5b17fa30871af91df2c57d97`. Si `event_id` ya existe, sólo compara `lifecycle_id` y retorna `idempotent=True`; no compara `family`, `instrument`, `currency`, `to_state`, `amount`, `occurred_at` ni `detail`.

`FamilyPaperExecutor.subscribe_fund` lo llama directamente en `rc6_paper_family_lifecycle.py:129-139`. No encontré un caller runtime no-test que active hoy esa suscripción FCI, por eso la severidad se mantiene P2 de alcance limitado y no P1. Los métodos especializados de FUTUROS agregan validaciones en otras capas, pero no reparan el contrato genérico.

### Probe exacto

```json
{
  "first_request": {
    "lifecycle_id": "FCI-1",
    "event_id": "REQ-1",
    "subscription_amount": "1000",
    "occurred_at": "2026-10-05T12:00:00-03:00",
    "result": {"idempotent": false, "state": "SUBSCRIBE_REQUESTED", "ledger_total": "-1000"}
  },
  "non_equivalent_replay": {
    "lifecycle_id": "FCI-1",
    "event_id": "REQ-1",
    "subscription_amount": "2000",
    "occurred_at": "2026-10-05T12:01:00-03:00",
    "result": {"idempotent": true, "state": "SUBSCRIBE_REQUESTED"}
  },
  "durable_event_count": 1,
  "durable_amount": "-1000",
  "collision_rejected": false
}
```

El caller recibe éxito idempotente para una intención económica distinta, mientras el ledger conserva el monto original.

### Gap y recomendación — no implementada

- Los tests reintentan payloads equivalentes; no atacan colisiones semánticas con igual `event_id`.
- **FIX:** guardar y comparar fingerprint canónico de toda la intención o comparar todos los campos normalizados contra el evento persistido.
- **GUARD:** cualquier diferencia semántica debe ser `PAPER_LIFECYCLE_EVENT_ID_COLLISION`, no éxito idempotente.
- **TEST:** variar individualmente monto, moneda, instrumento, estado, tiempo y detail; repetir tras restart y bajo dos procesos.

## RA-E-04 — P3: `shadow_promotion` contradice la autoridad efectiva

`rc6_source_consolidation.py:227-242`, blob `40a2a923b4e3e43a3a84ff8536411ad421565aa4`, fija `shadow_promotion = not review`, sin exigir fuente primaria PPI ni `selection_eligible`.

Probe BYMA-only:

```json
{
  "identity_primary_source": "COMPLEMENT_REFERENCE_ONLY",
  "selection_eligible": false,
  "entry_authority": false,
  "live_decision_authority": false,
  "shadow_promotion": true
}
```

No encontré un consumer de código de `shadow_promotion`; sus apariciones fuera del módulo son tests/docs. Por ello no afirmo un bypass de entry authority y califico P3 de claim/telemetría. Aun así puede inducir a un operador o evidencia automatizada a confundir “sin conflicto” con “promovible”.

- **FIX:** derivar el flag de `primary_authoritative && selection_eligible && !review`, o renombrarlo a una propiedad estrictamente descriptiva como `conflict_free_comparison`.
- **TEST:** IOL-only y BYMA-only deben permanecer no promovibles aunque sean frescos y no conflictivos.

## Matriz adversarial de autoridad de datos

| Ataque | Observado | Dictamen |
| --- | --- | --- |
| PPI stale + IOL fresh | Valor observacional IOL=101; PPI rechazado `STALE_QUOTES`; `entry_authority=false` | PASS observe-only |
| PPI missing + IOL fresh | IOL puede llenar observación bajo identidad explícita; entry/live false | PASS observe-only |
| PPI/IOL fresh en desacuerdo | PPI elegido; `SOURCE_FIELD_DISCREPANCY_REVIEW_REQUIRED`; entry false | PASS |
| Timestamp de proveedor ausente | valor `null`, `NO_VERIFICADO`, `PROVIDER_TIMESTAMP_MISSING_OR_AMBIGUOUS` | PASS |
| Evidencia duplicada conflictiva en mismo path | valor `null`, review, `DUPLICATE_SOURCE_EVIDENCE_CONFLICT` | PASS |
| Identidad exacta duplicada con last conflictivo | `validated_last=null`, selection false, review conflict | PASS |
| Conflicto moneda/mercado | `BLOCKED_CONFLICT`, selection false | PASS |
| Conflicto settlement | `BLOCKED_CONFLICT`, selection false | PASS |
| Mismo ticker, distinto mercado | Filas separadas; PPI puede ser shadow-eligible, complemento no | PASS en consolidación; FAIL downstream RA-DE-01 |
| IOL parcial (1 útil + 1 error) | `PARTIAL_SOURCE_ERRORS`, evidencia útil retenida, audit no vacío, live false | PASS |
| LKG fresco antiguo + dato nuevo stale | el nuevo stale invalida rescate silencioso; `STALE_QUOTES` | PASS |
| BYMA-only | selection/entry/live false, pero `shadow_promotion=true` | PASS autoridad; P3 telemetría |

La jerarquía efectiva verificada fue PPI > IOL > BYMA, siempre `entry_authority=false` en este pipeline. También confirmé wiring de F-04: los callers de fuente pasan `reports=source_reports` y `as_of=at` al audit y ligan digests; no se reprodujo un audit vacío en estas variantes. Eso no compensa la pérdida de identidad en la ruta factual de salidas.

## Caja, reservas, monedas, SQLite y restart

No hallé una segunda falla material en los escenarios independientes siguientes:

| Escenario | Output exacto resumido | Resultado |
| --- | --- | --- |
| Dos procesos, lifecycle distinto, mismo futuro | exit codes `[0,0]`; `OPENED` / `FUTURES_POSITION_ALREADY_OPEN`; 1 activa; cash ARS `-1500100` | PASS |
| Dos procesos, mismo lifecycle/event | un `idempotent=false`, otro `true`; 1 activa; cash ARS `-1500100` | PASS |
| SQLite write lock 0,5 s y release | segundo proceso completó en 0,433 s; apertura única; cash `-1500100` | PASS |
| Restart + retry de settlement variation | cash antes/después ARS `-1490100`; USD `0`; variation `10000`; retry idempotente; snapshot igual | PASS |
| DailyRisk multimoneda | ARS baseline `100000000`, PnL `-10100`, `READY`; USD baseline `1000`, PnL `0`, `READY` | PASS |

Estos PASS son acotados a fixtures locales; no certifican p99 bajo carga, filesystem del host, crash en cada instrucción ni comportamiento del proveedor. Sí reducen la hipótesis de doble reserva/cash por las carreras concretas probadas.

## Ejecución de tests focales

- Fuente: `tests/test_issue465_source_retention_audit.py`, `tests/test_rc6_source_consolidation.py`, `tests/test_rc6_ppi_iol_reconciliation_rc6.py`, `tests/test_rc6_final_family_source_policy.py`. Todos los casos ejecutables en el entorno RA-D/E pasaron; un caso inicialmente no importó por faltar `requests`. Root luego ejecutó ese caso exacto en `/tmp/ra-b-venv` y reportó PASS; JUnit: `audit_evidence/root_targeted.xml`.
- Riesgo/FUTUROS: `tests/test_rc6_final_shared_risk.py`, `tests/test_rc6_future_programming_complete.py`, `tests/test_rc6_future_paper_lifecycle.py`, `tests/test_dynamic_risk_and_caucion_sweep_hf6.py`. Todos los casos ejecutables en el entorno RA-D/E pasaron; un caso de collector inicialmente no importó por la misma dependencia. Root luego ejecutó ese caso exacto en `/tmp/ra-b-venv` y reportó PASS en el mismo JUnit.
- Otras suites exit/cash que importan `test_production_paper_v1634` tampoco se contaron como ejecutadas por esa limitación.

Los GREEN existentes no cubren los tres contraejemplos económicos descritos.

Los probes independientes se ejecutaron como invocaciones Python efímeras; no quedó persistido un driver standalone. Los JSON y este informe preservan fixtures, secuencia, símbolos y outputs exactos, pero no deben presentarse como un harness ejecutable archivado.

## Evidencia reproducible

- `audit_evidence/risk_data/identity_exit_collision.json`: fixtures y outputs spot, FUTUROS y collector.
- `audit_evidence/risk_data/risk_asof_idempotency.json`: inmutabilidad del cutoff e idempotencia no equivalente.
- `audit_evidence/risk_data/source_authority_matrix.json`: ataques de fuente, identidad, clocks, LKG y complementariedad.
- `audit_evidence/risk_data/concurrency_restart_matrix.json`: procesos, lock, restart, variation y monedas.
- `audit_evidence/risk_data/test_execution.md`: entorno, suites y limitaciones.

Los cuatro JSON fueron validados con `python -m json.tool`. El checkout compartido permaneció limpio y su tree final siguió siendo `bf3cf193434641aa89e4c746b26c77aec5d1d2b2`.

## Condiciones mínimas para reauditoría

1. Identidad de cinco partes end-to-end en collector, catálogo, persistencia, selección y supervisors; tombstone/estado de última respuesta por identidad.
2. Prueba real-call-chain de stop/target/EOD para spot y FUTUROS con ticker homónimo, conflicto de moneda/mercado y última respuesta inválida.
3. `portfolio_capacity(at)` reconstruido desde eventos al cutoff y propiedad no-lookahead bajo eventos posteriores/restart.
4. Idempotency key ligada a un fingerprint semántico durable; rechazo explícito de replays no equivalentes.
5. Semántica inequívoca de promoción de fuente complementaria y test de claims.
6. Repetir concurrencia, restart, multimoneda y suite completa en el entorno reproducible del artifact candidato.

Hasta cumplir y reauditar estas condiciones sobre un nuevo SHA/tree/artifact, **#466 no es apto para merge ni deploy** desde los frentes RA-D/RA-E.
