# POROTA TRADING RC6 — ACTIVE BACKLOG / CONTINUIDAD VIVA

Fecha: 2026-09-08
Rama canónica: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`

## Regla

Este archivo NO reemplaza el checkpoint canónico. Lo complementa como tablero vivo de pendientes y avances de la conversación iniciada desde `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-08_CONTINUIDAD.md`.

Mantener todos los invariantes originales, especialmente:
- `real_orders_sent=0`;
- producción real NO-GO;
- browser PPI read-only;
- ninguna prueba de red sobre rutas de orden;
- no borrar evidencia/históricos sin equivalencia demostrada;
- no tratar `WEB_VISIBLE` como `API_EXECUTABLE`;
- no tratar 50 filas DOM como universo completo;
- no inferir tick/step de decimales;
- no mezclar cleanup físico con deploy funcional.

## Cambios de estado posteriores al checkpoint original

### PPI Production API auth
Estado actual: RED.

Evidencia nueva posterior al checkpoint original:
- credencial instalada estructuralmente completa;
- par oficial recuperado coincide con el instalado por fingerprint;
- SDK `ppi-client 1.2.4` devuelve `Credenciales invalidas`;
- prueba directa con headers REST oficiales `AuthorizedClient=API_CLI_REST` y `ClientKey=pp19CliApp12` también devuelve HTTP 400 `Credenciales invalidas`;
- no se modificó secret file ni `.env`;
- no se llamaron Budget/Confirm/Cancel ni rutas de orden.

Conclusión operativa: esperar validación/reactivación/nueva credencial por soporte PPI. No seguir repitiendo intentos con el mismo par.

### ppi-client 1.3.0
Estado: GREEN SHADOW / NO DEPLOY.

Rama:
`upgrade/rc6-ppi-client-1.3.0-shadow-20260908`

CI:
`RC6 PPI Client 1.3.0 Shadow Validate 2026-09-08`
run `34263271406` SUCCESS.

Pendiente: promoción sólo después de regresión completa y credenciales API válidas; no asumir que 1.3.0 resuelve auth.

### Scraping semaphore
Estado: GREEN código/CI.

Rama:
`feature/rc6-scraping-semaphore-model-20260908`

CI:
run `34262070238` SUCCESS.

Semántica:
- GREEN = coverage demostrada + freshness dentro de SLA;
- YELLOW = coverage parcial/truncada o freshness degradada aún usable;
- GRAY = expected universe no demostrado;
- RED = sin colección válida o stale más allá de max.

Pendiente: cableado real a Dashboard/Contract Evidence/alerting.

### Event Risk / GDELT
Estado: GREEN base SHADOW / integración pendiente.

Rama:
`feature/rc6-event-risk-gdelt-shadow-feed-20260908`

CI:
run `34262553865` SUCCESS.

Reglas:
- read-only GET;
- no broker;
- no DB operacional;
- no BUY/SELL;
- `SHADOW_ONLY`;
- provenance temporal sin look-ahead.

Pendiente: dedupe multi-source, persistence/evidence store, exposure graph, scoring, dashboard y backtests preopen/open/close/postclose.

## P0 original — estado vivo

### P0.1 Matriz API por familia
Estado: YELLOW / bloqueado parcialmente por PPI auth.

Hecho:
- matriz estática SDK/wrappers/enums/read-only;
- no-order-route permission probe corregido y CI GREEN.

Falta:
- evidencia empírica read-only con auth válido;
- cerrar marketdata/history/order/cancel/settlement por familia;
- 0/16 debe considerarse EXECUTABLE hasta prueba suficiente.

### P0.2 Universo/cobertura DOM >50
Estado: GREEN arquitectura / YELLOW datos.

Hecho:
- reconciler expected-universe con CI GREEN;
- no acepta `50` como complete sin fuente independiente.

Falta:
- expected universe completo para cada familia;
- Opciones: identidad/dedupe 50 renderizadas / 43 únicas;
- resolver familias con cap aparente.

### P0.3 Browser policy V3 versionada
Estado: GREEN código/CI / NO DEPLOY.

Rama:
`feature/rc6-browser-v3-versioned-safe-routes-20260908`

Falta:
- deploy transaccional;
- E2E read-only postdeploy con `real_orders_sent=0`.

### P0.4 DOM Contract Evidence V1 SHADOW
Estado: YELLOW.

Hecho:
- DOM source viability demostrada;
- reconciler, SLA y semaphore disponibles.

Falta:
- integrar extractor → normalized evidence → manifest/provenance → Contract Evidence V1;
- version/hash/schema/extractor version;
- row identity robusta;
- expected/observed coverage;
- missing critical fields;
- no auto-activation;
- no canonical write hasta reconcile.

### P0.5 DOM/API/History reconciliation
Estado: GREEN base / YELLOW operacionalización.

Falta:
- cablear pipeline recurrente;
- reconciliar discrepancias por identidad/familia;
- incorporar resultado a readiness.

### P0.6 SLA/TTL/max_staleness
Estado: GREEN código/CI / NO DEPLOY.

Rama:
`feature/rc6-canonical-data-sla-policy-20260908`

Falta:
- integración runtime observer/dashboard/alerting.

### P0.7 Dashboard > Scraping
Estado: YELLOW.

Falta:
- UI por fuente/familia con AUTH, LAST_SUCCESS, AGE, SLA/TTL, HASH/SCHEMA, COVERAGE, ROW_COUNT, EXPECTED_UNIVERSE, SOURCE, FALLBACK;
- readiness operativa separada del scraper.

### P0.8 Alerting/introspection/Telegram
Estado: GREEN código / YELLOW deploy.

Rama:
`feature/rc6-operational-health-alert-outbox-20260908`

Falta:
- deploy;
- validación real outbox worker/Telegram;
- panel introspection consolidado;
- incident transitions/dedupe/recovery en runtime.

### P0.9 Hard NO-TRADE date-scoped gate
Estado: NOT PROVEN / pendiente si se reutiliza la capacidad.

No confundir decisión operativa con gate implementado.

## P1 — data/evidence hardening

### History Store completeness / price basis
Estado: YELLOW pendiente de promoción.

Rama:
`fix/rc6-history-store-completeness-price-basis-20260907`

Invariantes:
- FULL_OHLCV no desplazado por CLOSE_ONLY sólo por prioridad;
- RAW y ADJUSTED identidades distintas;
- settlement/identity mismatch fail-closed;
- provenance completo.

### PPI Web History SHADOW
Estado: PENDIENTE.

Debe estudiar read-only:
- profundidad;
- OHLCV;
- settlement;
- RAW/ADJUSTED;
- paginación;
- familias;
- provenance.

Inicialmente `canonical_write=DENY`, `db_write=NO`.

### Cauciones
Estado: YELLOW / no READY_PAPER.

Pendientes:
- vencimiento exacto;
- day-count;
- rounding;
- quantity step;
- mínimos;
- depth/pagination;
- settlement;
- fees;
- ejecución API demostrada;
- DOM/API/CE reconcile.

Mantener `CAUCIONES_AUTO_PLACEMENT=false`.

### Opciones
Estado: YELLOW.

Pendiente identidad completa:
- subyacente;
- vencimiento;
- strike;
- call/put;
- moneda/mercado;
- dedupe robusto.

### Calendarios / CEDEAR exterior
Estado: base existente / regresión pendiente antes de READY_PAPER.

Regla: BYMA abierto no implica operar instrumento cuyo mercado/subyacente relevante esté cerrado.

### SRE latency RCA
Estado: YELLOW.

Quick-health fue separado del `PRAGMA quick_check` pesado. Falta RCA de latencia y panel integral.

## Disk / Empirical Evidence Architecture A→E

### Phase A
Estado: GREEN código/CI / ACTIVATION HOLD.

Rama:
`phase-a/rc6-empirical-evidence-write-architecture-20260908`

Incluye raw evidence store content-addressed, compresión determinística, manifests, dedupe físico por hash y preservación de intentos.

No activar mientras PPI auth/observer esté degradado.

### Phase B
Estado: PENDIENTE después de A estable.

Shadow/reconcile legacy read-only. Debe explicar PARTIAL y demostrar reconstrucción semántica.

### Phase C
Estado: BLOQUEADO hasta equivalencia + `EVIDENCE_LOST=0`.

Primera fase donde puede recuperarse espacio físico. No borrar antes.

### Phase D
Estado: PENDIENTE.

Learning evidence completa `decision -> evidence -> outcome`.

### Phase E
Estado: PENDIENTE.

Retention / COLD storage con trazabilidad.

## P2 — model/risk validation

Pendientes activos:
- MFE/MAE con provenance temporal;
- Forward Lab v2;
- walk-forward;
- robustness;
- Event Risk completo;
- correlations/sector/family normalization;
- cohorts/version separation;
- PAPER/SHADOW campaign;
- concentración sectorial BINDING en PAPER;
- revisión final lógica de negocio por familia.

## P3 — UX/operación/consolidación

Pendientes activos:
- Dashboard Scraping/Contract Evidence final;
- `/vivo` introspection final;
- drill-down abiertas/cerradas;
- P&L actual por operación + semáforo;
- timestamp exacto último mark;
- modo sandbox/simulación/producción visible;
- APIs/fuentes usadas + health;
- responsive tablet/Voice Access;
- validación real Samsung/Voice Access;
- Learning día→semana→mes;
- documentación operatoria;
- cleanup físico final;
- checkpoint Release Candidate.

## Política de integración

No mega-deploy.

Orden de olas:
1. safety/observability;
2. storage Phase A tras estabilización;
3. Scraping/CE V1 SHADOW + expected universe + dashboard/Telegram;
4. API operability/runtime readiness;
5. risk;
6. UX/Learning/PAPER campaign;
7. disk Phase B/C sólo con equivalencia demostrada.

## Estado global

- Producción real: RED NO-GO.
- `real_orders_sent=0`: mantener absoluto.
- PPI Web: GREEN último estado válido.
- PPI Production API: RED esperando soporte.
- ppi-client 1.3.0: GREEN SHADOW.
- Scraping semaphore: GREEN código/CI.
- Event Risk/GDELT base: GREEN SHADOW.
- Phase A disk: GREEN código/CI, activation HOLD.
- Dashboard/SRE/CE V1/History/P1/P2/P3: continuar en paralelo según dependencias.
