# POROTA TRADING RC6 — DEPLOYMENT WAVES

Fecha: 2026-09-08
Rama canónica: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`

## Invariantes

- `real_orders_sent=0` absoluto durante RC6 PAPER/NO-TRADE.
- Producción real NO-GO.
- No mega-deploy.
- Cada ola requiere preflight, backup cuando corresponda, activación transaccional, postflight y rollback.
- No mezclar cleanup físico con cambios funcionales.
- No probar rutas de orden en producción.
- PPI browser read-only.

## WAVE 0 — BASELINE / PREFLIGHT

Objetivo: confirmar estado runtime inmediatamente antes de cualquier deploy.

Requisitos:
- resolver live SHA real;
- observer/dashboard running y restart count;
- DB quick_check;
- disk/free space;
- mode/session;
- PPI auth state;
- `real_orders_sent=0`;
- scheduler/outbox worker health;
- backup/rollback materializado cuando aplique.

No modifica runtime.

## WAVE 1 — SAFETY + OBSERVABILITY

Candidatos:
1. `fix/rc6-no-order-route-permission-probe-20260908`
   - elimina uso de Budget/Confirm/Cancel como permission probe;
   - CI GREEN.
2. `feature/rc6-browser-v3-versioned-safe-routes-20260908`
   - policy V3 versionada;
   - no `/Operar`;
   - unknown first-party mutation fail-closed;
   - CI GREEN.
3. `feature/rc6-canonical-data-sla-policy-20260908`
   - SLA/TTL/max staleness canónico compartido observer/dashboard;
   - CI GREEN.
4. `feature/rc6-operational-health-alert-outbox-20260908`
   - transitions GOOD↔BAD, dedupe, recovery, sanitización;
   - CI/code GREEN, runtime validation pending.
5. `feature/rc6-scraping-semaphore-model-20260908`
   - GREEN/YELLOW/GRAY/RED coverage+freshness model;
   - CI run 34262070238 SUCCESS;
   - deploy sólo como backend/presentation dependency, sin afirmar UI final completa.

Entrada:
- Wave 0 GREEN.
- Ningún cambio de trading logic/order execution.

Postflight obligatorio:
- observer/dashboard healthy;
- DB quick_check=ok;
- `real_orders_sent=0`;
- no Budget/Confirm/Cancel probe;
- no order routes visited;
- outbox worker sano;
- alerts de transición sin spam;
- browser V3 read-only E2E sólo cuando el collector esté DUE o mediante prueba segura explícita.

Rollback:
- revertir sólo Wave 1; no tocar DB/evidence.

## WAVE 2 — DASHBOARD / SCRAPING / INTROSPECTION

Candidatos:
- Dashboard Scraping/Contract Evidence;
- AUTH, LAST_SUCCESS, AGE, SLA/TTL, HASH/SCHEMA, COVERAGE, ROW_COUNT, EXPECTED_UNIVERSE, SOURCE, FALLBACK;
- readiness operativa separada del estado del scraper;
- `/vivo` introspection consolidada;
- API/source health y timestamps;
- accesibilidad responsive Samsung/Voice Access.

Entrada:
- Wave 1 estable.
- presentación no debe cambiar ejecución.

## WAVE 3 — CONTRACT EVIDENCE V1 SHADOW

Candidatos:
- DOM extractor → normalized evidence;
- manifests/provenance/hash/schema/extractor_version;
- expected vs observed coverage;
- missing critical fields;
- DOM/API/History reconciliation recurrente;
- `NO_AUTO_ACTIVATION`;
- canonical write denied until reconcile policy allows explicitly.

Entrada:
- Browser V3 estable;
- SLA/semaphore estable;
- expected-universe reconciler validado.

## WAVE 4 — STORAGE PHASE A

Candidato:
- `phase-a/rc6-empirical-evidence-write-architecture-20260908`.

Estado actual: code/CI/preflight GREEN, activation HOLD.

Entrada recomendada:
- observer estable;
- PPI auth no degradando el runtime o incidente explícitamente aislado;
- Wave 1 estable;
- backup/preflight/storage free-space GREEN.

Acción:
- contener nuevas raw writes en Evidence Store content-addressed;
- no borrar legacy;
- no VACUUM;
- no reclaim físico aún.

## WAVE 5 — PPI CLIENT / API OPERABILITY

Candidato:
- `upgrade/rc6-ppi-client-1.3.0-shadow-20260908`.
- CI run 34263271406 SUCCESS.

Entrada:
- credenciales API productivas válidas/reactivadas o nuevas;
- una prueba read-only Login + Configuration/MarketData satisfactoria;
- regresión completa.

No considerar 1.3.0 como solución asumida del incidente de credenciales.

Después:
- cerrar matriz 16 familias mediante evidencia read-only;
- no enviar órdenes para probar capability.

## WAVE 6 — FAMILY CONTRACT MODELS / CAUCIONES

Candidato inicial:
- `feature/rc6-cauciones-contract-model-20260908`.

Soporte PPI confirmado incorporado:
- ticker MONEDA+días;
- días corridos;
- inicio fecha de carga;
- Actual/365;
- price=TNA anual %;
- colocadora=Bids;
- quantity book=monto tomadora;
- mínimo PESOS=100000;
- mínimo DOLAR=100;
- integer only, step=1;
- maturity derivada, no campo API explícito.

Deploy de este modelo debe ser inicialmente no-binding/contract-validation only. `CAUCIONES_AUTO_PLACEMENT=false` permanece.

Pendiente antes de READY_PAPER:
- fees/taxes;
- timezone/cutoff;
- book depth/pagination;
- DOLAR balance semantics;
- sandbox Budget/Confirm/Cancel request/response semantics;
- API execution support demostrado;
- DOM/API/CE reconciliation.

## WAVE 7 — EVENT RISK / RISK ENGINE

Candidatos:
- GDELT SHADOW feed;
- multi-source dedupe/corroboration;
- evidence persistence;
- exposure graph;
- scoring/freshness/novelty;
- dashboard;
- preopen/open/close/postclose backtests;
- sector/correlation/family risk;
- sector concentration BINDING in PAPER only after campaign evidence.

No auto-promote to real money.

## WAVE 8 — UX / LEARNING / PAPER CAMPAIGN

Candidatos:
- drill-down trades;
- P&L semaphore + exact mark timestamp;
- mode/API/source visibility;
- tablet/Voice Access validation;
- Learning day→week→month;
- MFE/MAE provenance;
- Forward Lab v2 / walk-forward / robustness;
- cohorts/version separation;
- PAPER/SHADOW campaign.

## WAVE 9 — STORAGE PHASE B/C/D/E

B: legacy shadow reconciliation read-only.
C: physical reclaim only after equivalence + `EVIDENCE_LOST=0`.
D: learning evidence decision→evidence→outcome.
E: retention/COLD storage with traceability.

No Phase C deletion until explicit evidence-equivalence gate passes.

## Estado de deploy inmediato

### READY TO PREPARE FOR DEPLOY
- Wave 1 components individually GREEN in code/CI, subject to combined integration/preflight.

### READY IN SHADOW, NOT YET RUNTIME
- ppi-client 1.3.0;
- GDELT Event Risk;
- scraping semaphore;
- canonical SLA;
- Browser V3;
- alert outbox;
- disk Phase A;
- cauciones contract model once its CI completes GREEN.

### BLOCKED / WAITING EXTERNAL
- PPI Production API auth;
- empirical API operability 16/16;
- any sandbox execution-contract validation until sandbox credentials/path are available.

### NOT READY FOR DEPLOY AS BINDING TRADING LOGIC
- Cauciones auto placement;
- Event Risk binding;
- full Risk Engine promotion;
- real production execution.
