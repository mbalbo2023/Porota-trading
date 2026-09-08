# POROTA TRADING RC6 — DEPLOYMENT WAVES

Fecha: 2026-09-08
Rama canónica de contexto: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`

## Órdenes operativas persistentes

- Paralelizar por defecto todo trabajo independiente y seguro.
- No ejecutar rollback automático.
- Ante falla de deploy: RCA, corrección y redeploy cuando corresponda.
- Rollback únicamente con autorización explícita del usuario; si se considera necesario, pedir autorización antes.
- Ningún workflow nuevo de deploy debe contener rollback automático.

## Wave 0 — Preflight / baseline
Estado: PREPARADA.

Objetivo:
- confirmar SHA live;
- observer/dashboard running;
- restarts;
- DB quick_check;
- `real_orders_sent=0`;
- disco;
- modo;
- distinguir incidente PPI auth conocido de regresiones nuevas.

## Wave 1A — Safety / observability core
Estado: INTEGRACIÓN EN CURSO.

Rama:
`integration/rc6-wave1-safety-observability-20260908`

Contenido inicial:
1. `m_instrument_universe.py` sin Budget/Confirm/Cancel como permission probe;
2. Browser policy V3 versionada / rutas seguras;
3. operational health alert outbox + wiring del observer;
4. tests específicos.

Postflight requerido:
- observer y dashboard running;
- restarts sin regresión;
- DB quick_check ok;
- `real_orders_sent=0`;
- cero pruebas de rutas de orden;
- PPI auth puede continuar RED por incidente externo conocido sin convertir por sí solo el deploy en rollback;
- outbox no debe habilitar trading ni bloquear observer por fallo propio.

Política de fallo:
- NO rollback automático;
- conservar estado/evidencia;
- diagnosticar;
- corregir;
- redeploy;
- rollback sólo con autorización explícita del usuario.

## Wave 1B — SLA / scraping semaphore runtime
Estado: GREEN por componentes / integración pendiente.

Contenido:
- SLA/TTL canónico;
- scraping semaphore backend;
- wiring observer/dashboard evitando pisar cambios de Wave 1A.

Se mantiene separada de 1A para resolver limpiamente los cambios concurrentes sobre `bf_production_paper_observer.py` y `bg_paper_dashboard.py`.

## Wave 2 — Dashboard / Scraping / Introspection
Estado: YELLOW.

- semáforo por fuente/familia;
- captura y readiness separados;
- `/vivo` introspection;
- health APIs/fuentes;
- accesibilidad tablet.

## Wave 3 — Contract Evidence V1 SHADOW
Estado: YELLOW.

- DOM normalized evidence;
- manifests/provenance/hash/schema;
- expected universe;
- reconcile;
- no canonical activation.

## Wave 4 — Disk Phase A
Estado: GREEN código/CI / ACTIVATION HOLD.

No activar mientras observer/PPI auth permanezca degradado sin una ventana segura separada.

## Wave 5 — ppi-client 1.3.0 / API operability
Estado: GREEN SHADOW / runtime bloqueado por PPI auth.

No promover sólo porque CI sea GREEN. Requiere credencial válida + Login/Configuration/MarketData read-only.

## Wave 6 — Family contracts / Cauciones
Estado: YELLOW avanzado.

Cauciones:
- contrato puro en rama `feature/rc6-cauciones-contract-model-20260908`;
- soporte PPI cerró naming, plazo, Actual/365, mínimos, step, TNA/book;
- faltan fees/timezone/depth/saldo DOLAR + sandbox execution semantics;
- `CAUCIONES_AUTO_PLACEMENT=false`.

## Wave 7 — Event Risk / Risk Engine
Estado: YELLOW avanzado SHADOW.

## Wave 8 — UX / Learning / PAPER campaign
Estado: YELLOW.

## Wave 9 — Disk B/C/D/E
Estado: B/D/E pendientes; C bloqueada hasta equivalencia y `EVIDENCE_LOST=0`.

## Invariantes globales

- producción real NO-GO;
- `real_orders_sent=0`;
- browser PPI read-only;
- no order-route tests;
- no cleanup destructivo;
- no rollback sin autorización explícita.
