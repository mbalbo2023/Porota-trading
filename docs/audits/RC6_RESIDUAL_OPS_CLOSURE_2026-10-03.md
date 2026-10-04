# RC6 — Residual Ops Closure — 2026-10-03

## Estado

- Workstream: `WS-RC6-RESIDUAL-OPS-CLOSURE-20261003`.
- Base productiva revalidada: `da697c6e6c2274579f9e4a112fabc4327475dd35`.
- Modo: WRITE_OWNER aislado + auditorías runtime READ_ONLY.
- DEPLOY_OWNER: NOT_ACQUIRED.
- PAPER/SHADOW ONLY.
- No se hizo deploy, restart, escritura de DB, limpieza Docker ni mutación de systemd.
- PPI Watch: sin mutación.

## Scope excluido por ownership paralelo

No se toca:
- #453 / WS-MOTOR-16 FUTUROS PAPER;
- #454 / #452 performance de 20 ruedas;
- #450, cuyo head y Predeploy se revalidan pero no se modifica;
- motor financiero, DailyRisk, observer, dashboard y PPI Watch.

## Runtime read-only

Action `37141540864` leyó mediante `systemctl`, `docker inspect`, SQLite
`mode=ro` + `PRAGMA query_only=ON` y el snapshot IOL existente.

Estado observado:
- observer `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- Scalping `WAITING_MARKET`, heartbeat fresco, `failed=0`;
- cash-sweep caución `WAITING_CALENDAR`, `real_orders_sent=0`, `routes_json=[]`;
- exit supervisor `RUNNING`.

Estos estados son saludables fuera de rueda según sus contratos actuales. No constituyen
validación en rueda. La operación simulada Scalping/caución durante una ventana de
mercado válida continúa `NO_VERIFICADO` porque 2026-10-03 es sábado.

## Auditoría residual

La primera corrida `37141540864` falló correctamente porque encontró host units fuera
del mapa canónico. No hubo mutación.

La segunda corrida `37141978168` terminó **SUCCESS** después de que el auditor pasó a
distinguir unidad canónica, externa, residual clasificada y desconocida:
- 69 host units clasificadas;
- 0 desconocidas;
- Scalping `WAITING_MARKET`;
- caución `WAITING_CALENDAR`;
- IOL `LIVE_FRESH`;
- `VIOLATION_COUNT=0`;
- `POROTA_RC6_RESIDUAL_OPS_AUDIT=GREEN`.

La corrida fue diagnóstica: no retiró units.

## IOL — verdad por source-path

Snapshot observado:
- cache global: `LIVE_FRESH`;
- `last_known_good_at=2026-10-02T19:58:57.187272+00:00`;
- `refreshed_at=2026-10-02T19:58:57.187272+00:00`;
- continuación: `CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL`;
- fallback: IOL bounded retry -> IOL LKG -> PPI primary -> BYMA complementary.

Secciones:
- caucion:ARS = LIVE_FRESH;
- caucion:USD = LIVE_FRESH;
- fci = LIVE_FRESH;
- fixed_income = SOURCE_UNAVAILABLE_NO_LKG;
- options:DOME/ECOG/ECOGC/ECOGD = SOURCE_UNAVAILABLE_NO_LKG.

Los faltantes son scoped por sección. No autorizan convertir IOL a cero ni invalidar
las secciones frescas.

## Provenance exacta de units residuales

Probe read-only `37142364953`: **SUCCESS**.
Artifact `11280643653`, digest
`sha256:3ecfdcb79da3608b8780e4a374b5ee74acd1257e885e14097829f53ca5aa34cb`.

El probe capturó estado, descripción, FragmentPath, SHA256, mtime, relación timer→service
y paths de ExecStart sanitizados, sin variables de entorno ni contenido de secrets.

### Externas vigentes — PRESERVE

Cinco unidades tienen autoridad externa al mapa lifecycle y deben preservarse:

1. `porota-critical-approval-rc6.service`
2. `porota-critical-github-proxy-rc6.service`

Autoridad: `ops/rc6_deploy_critical_control_plane.sh`.

3. `porota-live-decision-cockpit-rc6.service`
4. `porota-live-decision-cockpit-rc6.timer`
5. `porota-private-snapshot.service`

Autoridad: PR #107. Su comentario de deploy aislado `5738065413` registra run
`35411100398` GREEN, `PRODUCTION_PAPER`, `real_orders_sent=0`, cockpit
localhost-only y esas units activas/habilitadas. El PR continúa abierto/draft y exige
reconciliación separada antes de integración.

Estas cinco quedan explícitamente fuera de cualquier retiro automático.

### Legacy-orphan demostrado — RETIRE NEXT DEPLOY

La evidencia del tracker #441 / WS-OPS-RESIDUALS-01 ya había clasificado como residuos
legacy-orphan dos servicios failed de septiembre, sin trigger activo:

1. `porota-contract-evidence-weekend-backfill-rc6.service`
   - descripción: one-off Sep 12-13 weekend backfill;
   - estado observado: static / failed;
   - timer asociado: disabled / inactive;
   - evidencia #441: failed state stale desde 2026-09-12.

2. `porota-scheduler-export-hf6.service`
   - estado observado: disabled / failed;
   - timer asociado: disabled / inactive;
   - evidencia #441: failed state stale desde 2026-09-12, sin trigger.

Sólo estos dos nombres quedan en `legacy_retire_units`. El retiro no se ejecutó en
esta misión. Queda preparado para el próximo Deploy V2 autorizado.

### Residuales clasificados — PRESERVE / REVIEW_REQUIRED

Las demás 22 units fuera del mapa lifecycle quedan versionadas como
`NO_VERIFICADO_PRESERVE_REVIEW_REQUIRED`. La ausencia del mapa canónico no es
autoridad suficiente para borrarlas.

Incluyen antiguos HF4/HF6, collectors/one-shots RC6, preopen legacy, unidades de
históricos PPI y timers deshabilitados. El probe de provenance conserva suficiente
información para una clasificación futura sin volver a inferir por nombre.

No se elimina ninguna de estas 22 automáticamente.

## FIX / defensa permanente

La policy `ops/policy/host-control-plane-reconciliation-v2.json` incorpora:

- `external_host_units`: cinco units externas que deben preservarse;
- `legacy_retire_units`: únicamente los dos legacy-orphan demostrados;
- `residual_host_units`: 22 units clasificadas, preserve/review-required;
- `legacy_retire_contract`: unknown fail-closed, PPI Watch forbidden, external
  preserve y residual-review preserve.

`scripts/porota_apply_host_control_plane_v2.py`:
- no descubre ni elimina units por patrón;
- sólo puede retirar nombres exactos versionados;
- rechaza path/nombre inseguro, duplicados y PPI Watch;
- rechaza cualquier unit declarada externa;
- rechaza cualquier unit declarada residual-review;
- en el próximo Deploy V2 autorizado, los dos nombres permitidos se procesan con
  disable/stop, unlink exacto, reset-failed, daemon-reload y verificación
  inactive + file absent;
- preserva el comportamiento canónico existente ACTIVE/RETIRED/QUARANTINED.

Regresiones:
- allowlist exacto = dos nombres;
- external = cinco;
- residual-review = 22;
- las tres categorías no se solapan;
- PPI Watch no puede entrar;
- prueba con filesystem/systemctl simulados demuestra que sólo el nombre exacto se
  retira y que critical approval + PPI Watch permanecen intactos.

## Protecciones administrativas GitHub

La auditoría obtuvo:
- repo rulesets: HTTP 200, lista vacía;
- classic branch protection para productiva: HTTP 403 con el token disponible.

Estados:
- rulesets legibles: 0;
- classic branch protection / required checks: **NO_VERIFICADO**.

No se infiere ausencia de protección desde un HTTP 403.

## #450

`fix/rc6-preopen-not-due-20261003`:
- head `0311dcc10139777f8be988f4fe8c3efabc86d1a9`;
- base productiva `da697c6e6c2274579f9e4a112fabc4327475dd35`;
- compare: 2 ahead / 0 behind;
- Predeploy V2 `37131980746`: SUCCESS.

Queda listo para reconciliación posterior en candidato único. No requiere ni autoriza
un deploy individual.

## Criterio de cierre de este PR

1. auditorías read-only GREEN;
2. provenance artifact preservado;
3. tests/Predeploy V2 exactos GREEN;
4. no solapamiento con #453/#454/#450;
5. sin merge ni deploy;
6. WRITE_OWNER liberado.

El único cambio de runtime futuro preparado por este PR es retirar los dos servicios
legacy-orphan exactos durante un Deploy V2 consolidado. Hasta que ese deploy ocurra,
su estado runtime sigue siendo `PENDING_NEXT_AUTHORIZED_DEPLOY`.
