# POROTA TRADING — CHECKPOINT WAVES 9–18 DEPLOY PREP — 2026-09-09

## Regla canónica
Preparación y CI de olas independientes se paralelizan. Activación runtime se serializa por dependencia y riesgo. Ninguna ola es `READY_DEPLOY` por tener sólo un audit verde: requiere código real materializado sobre la base live vigente, tests, dependencias resueltas, gate de deploy/postflight y preservación de safety.

Invariantes: `PRODUCTION_PAPER`, `REAL_ORDER_CAPABILITY=BLOCKED`, `real_orders_sent=0`, ninguna prueba de orden real, PPI Web/API read-only salvo autenticación aprobada, no rollback automático.

## Problema estructural encontrado
Las ramas canónicas viejas W9–W18 no eran deployables desde el runtime actual:
- W9/W10/W11 tenían sólo workflows de readiness/audit; no mutaban runtime.
- W12/W13/W14/W15/W16/W17/W18 tenían 0 runs propios en sus ramas canónicas al relevamiento del 2026-09-09.
- varias ramas estaban decenas de commits divergidas del HEAD live, por lo que no deben mergearse completas.

## Nuevas ramas current-base creadas
Nacieron del HEAD live canónico `2c9f3df2d03974ab6fe464d119bdbb2d90158706` y deben actualizarse al siguiente HEAD live GREEN antes de portar componentes si el UX V2 cambia la base:
- `deploy-prep/rc6-wave9-currentbase-20260909`
- `deploy-prep/rc6-wave10-currentbase-20260909`
- `deploy-prep/rc6-wave11-currentbase-20260909`
- `deploy-prep/rc6-wave12-currentbase-20260909`
- `deploy-prep/rc6-wave13-currentbase-20260909`
- `deploy-prep/rc6-wave14-currentbase-20260909`
- `deploy-prep/rc6-wave15-currentbase-20260909`
- `deploy-prep/rc6-wave16-currentbase-20260909`
- `deploy-prep/rc6-wave17-currentbase-20260909`
- `deploy-prep/rc6-wave18-currentbase-20260909`

## Estado por ola
### W9 — P0 — API/FAMILIAS/SESSION SAFETY
Estado: `IN_PREP`, no READY_DEPLOY todavía.
Componentes reales identificados: fixed-income nominal contract, cauciones contract model/evaluator, Contract Evidence RC6, family scraping/readiness, BYMA fail-closed ya live, política de calendario USA CEDEAR pendiente generalización.
Blocker P0 live: todas las familias salvo Acciones/CEDEAR requieren completar contratos/readiness. Ver checkpoint `POROTA_TRADING_CHECKPOINT_P0_FAMILY_SCRAPING_TO_OPERATIONAL_2026-09-09.md`.

### W10 — P0 — RISK ENGINE BINDING
Estado: `IN_PREP`, no READY_DEPLOY.
Audit viejo GREEN pero sin runtime mutation. Debe demostrar Daily Risk, soft/hard stop, overnight/carry, RR neto/costos, liquidez/slippage, concentración/correlación, concentración sectorial BINDING, Patrimonial Gate, settlement/family-data risk, kill switch y alertas. La decisión canónica del operador es concentración sectorial BINDING, no observation-only.

### W11 — P0/P1 — EVENT RISK + GDELT
Estado: `IN_PREP`, no READY_DEPLOY.
Componentes fuente identificados: `feature/rc6-event-risk-shadow-20260907`, `feature/rc6-event-risk-gdelt-shadow-feed-20260908`, `feature/rc6-event-risk-dashboard-view-20260907`. Audit viejo GREEN pero sin runtime mutation. Gate: degradación fail-safe; una noticia aislada nunca habilita trading.

### W12 — P1 — DOM/EXPECTED UNIVERSE/CONTRACT EVIDENCE
Estado: `IN_PREP`, no READY_DEPLOY. Rama canónica vieja tenía 0 runs.
Componentes fuente: `feature/rc6-dom-expected-universe-reconciler-20260908` + Contract Evidence/browser RC6. Muy ligada al P0 scraping de todas las familias y a Opciones series-level contract.

### W13 — P1 — HISTORY/A3/BACKFILL
Estado: `IN_PREP`, no READY_DEPLOY. Rama canónica vieja tenía 0 runs.
Componentes fuente: `feature/rc6-a3-identity-mapper-20260907`, `feature/rc6-history-backfill-planner-20260907`, `feature/rc6-history-reconciler-v2-20260907`. A3 debe seguir fail-closed hasta alignment/payload proof.

### W14 — P1 — SRE/INTROSPECTION/EARLY WARNING
Estado: `PARTIAL_GREEN/IN_PREP`.
La migración de introspección/publisher RC6 y retiro de timers legacy ya está GREEN live. Restan health split, alert outbox, anti-spam y proof completo de coverage/rate-limit/schedulers.
Componentes: `feature/rc6-sre-health-split-20260907`, `feature/rc6-operational-health-alert-outbox-20260908`.

### W15 — P1 — FORWARD LAB/LEARNING/MFE-MAE
Estado: `IN_PREP`, no READY_DEPLOY. Rama canónica vieja tenía 0 runs.
Componentes: `feature/rc6-forward-lab-v2-20260907`, `feature/rc6-mfe-mae-provenance-20260907`. No promotion automática por métricas débiles.

### W16 — P1 — BACKUP/RETENTION/DISK
Estado: `PARTIAL_GREEN/IN_PREP`.
Subwave safe build-cache cleanup ya GREEN: 3.607 GB reclaim, disco 61%→55%, sin tocar images/containers/volumes/datasets. Falta política completa daily-current/daily-previous, quiescence/checkpoint, thresholds y retention transaccional.

### W17 — P1 — TABLET/VOICE ACCESS/UX FINAL
Estado: `ACTIVE_FIX/IN_PREP`.
Wave8 base estaba live, pero el PDF del operador del 2026-09-09 reveló títulos duplicados, headers ocultos en tablet, doble submenú y ruido en En Vivo. Rama activa `fix/rc6-dashboard-operator-ux-v2-20260909`; no declarar W17 READY hasta live proof del V2.

### W18 — P2 — CONTROL PLANE/RECOVERY HOUSEKEEPING
Estado: `IN_PREP`, no READY_DEPLOY. Rama canónica vieja tenía 0 runs.
Componentes: `closure/rc6-critical-control-plane-20260907`, `control-plane/rc6-critical-approval-canonical-20260907`, `hotfix/rc6-control-plane-broker-permissions-issue-41`. Recuperación sólo manual/autorizada; jamás rollback automático.

## Orden runtime
P0: W9 → W10 → W11. Después P1 según readiness/impact. W19 IOL read-only queda fuera del runtime RC6.

## Gate `READY_DEPLOY`
Una ola pendiente sólo se marca READY_DEPLOY cuando:
1. branch current-base contiene los módulos reales que se pretende activar;
2. tests/compile/contract tests pasan;
3. dependencies están resueltas o fail-closed explícitas;
4. workflow deploy conoce el SHA exacto y tiene preflight/postflight;
5. `PRODUCTION_PAPER`, `real_orders_sent=0`, DB quick_check y raw/provenance invariants pasan;
6. no order routes son llamadas;
7. no rollback automático;
8. existe live proof para marcar GREEN después de activación.

Última actualización: 2026-09-09 ~13:15 ART.
