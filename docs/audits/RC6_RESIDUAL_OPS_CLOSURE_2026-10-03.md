# RC6 — Residual Ops Closure — 2026-10-03

## Estado

- Workstream: `WS-RC6-RESIDUAL-OPS-CLOSURE-20261003`
- Base productiva revalidada: `da697c6e6c2274579f9e4a112fabc4327475dd35`
- Modo de esta misión: WRITE_OWNER aislado + auditoría runtime READ_ONLY.
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

## Evidencia read-only inicial

Action `37141540864` leyó el runtime mediante `systemctl`, `docker inspect`,
SQLite `mode=ro` + `PRAGMA query_only=ON` y el snapshot IOL existente.

El runtime observado mantuvo:
- observer `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- Scalping `WAITING_MARKET`, heartbeat fresco, `failed=0`;
- cash-sweep caución `WAITING_CALENDAR`, `real_orders_sent=0`, `routes_json=[]`;
- exit supervisor `RUNNING`.

Estos estados son saludables fuera de rueda según sus contratos actuales, pero **no
constituyen validación en rueda**. La operación simulada de Scalping/caución durante
una ventana de mercado válida continúa `NO_VERIFICADO` al ser sábado 2026-10-03.

## IOL: verdad por source-path

Snapshot observado:
- cache global: `LIVE_FRESH`;
- `last_known_good_at=2026-10-02T19:58:57.187272+00:00`;
- `refreshed_at=2026-10-02T19:58:57.187272+00:00`;
- continuación: `CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL`;
- fallback: IOL bounded retry -> IOL LKG -> PPI primary -> BYMA complementary.

Estados de sección observados:
- caucion:ARS = LIVE_FRESH;
- caucion:USD = LIVE_FRESH;
- fci = LIVE_FRESH;
- fixed_income = SOURCE_UNAVAILABLE_NO_LKG;
- options:DOME/ECOG/ECOGC/ECOGD = SOURCE_UNAVAILABLE_NO_LKG.

Conclusión: los faltantes son **scoped por sección** y no autorizan convertir IOL a
cero ni invalidar las secciones frescas.

## RCA — deriva de systemd fuera del control-plane canónico

La policy `ops/policy/host-control-plane-reconciliation-v2.json` declara autoridad
de lifecycle para el control-plane RC6, pero el censo host encontró unidades residuales
no declaradas en esa policy.

Dos unidades fuera del mapa canónico sí tienen autoridad actual demostrable y deben
preservarse:
- `porota-critical-approval-rc6.service`;
- `porota-critical-github-proxy-rc6.service`.

Su autoridad es `ops/rc6_deploy_critical_control_plane.sh`, que las instala, habilita
y valida como control-plane crítico independiente.

Las siguientes 27 unidades son host residue y quedan declaradas para retiro **sólo en
el próximo Deploy V2 autorizado** mediante allowlist exacto:

1. porota-1016-github-verify.service
2. porota-1016-github-verify.timer
3. porota-contract-evidence-dashboard-hf6.service
4. porota-contract-evidence-hf6.service
5. porota-contract-evidence-hf6.timer
6. porota-contract-evidence-rc6.service
7. porota-contract-evidence-rc6.timer
8. porota-contract-evidence-weekend-backfill-rc6.service
9. porota-contract-evidence-weekend-backfill-rc6.timer
10. porota-introspeccion-hf4.service
11. porota-introspeccion-hf4.timer
12. porota-live-decision-cockpit-rc6.service
13. porota-live-decision-cockpit-rc6.timer
14. porota-log-export-hf6.service
15. porota-log-export-hf6.timer
16. porota-ppi-argentina-complete-now-rc6.service
17. porota-ppi-argentina-nightly-rc6.service
18. porota-ppi-argentina-nightly-rc6.timer
19. porota-ppi-fullfamily-history-rc6.service
20. porota-ppi-web-residual-rc6.service
21. porota-preopen.service
22. porota-preopen.timer
23. porota-private-snapshot.service
24. porota-scheduler-export-hf6.service
25. porota-scheduler-export-hf6.timer
26. porota-weekend-ingestion-audit-rc6.service
27. porota-weekend-ingestion-audit-rc6.timer

En la primera captura estaban todavía activos/habilitados:
- `porota-1016-github-verify.timer`;
- `porota-live-decision-cockpit-rc6.timer`;
- `porota-private-snapshot.service`.

También existían residuos en estado failed:
- `porota-contract-evidence-weekend-backfill-rc6.service`;
- `porota-scheduler-export-hf6.service`.

No se deshabilitó ni eliminó ninguno durante esta misión.

## FIX / defensa permanente

La policy ahora contiene:
- `external_host_units`: preservación explícita de los dos servicios críticos;
- `legacy_retire_units`: allowlist exacto de 27 residuos;
- `legacy_retire_contract`: unknown units fail-closed, PPI Watch forbidden,
  external host units preserved.

`scripts/porota_apply_host_control_plane_v2.py`:
- no descubre ni elimina unidades por patrón;
- sólo puede retirar nombres exactos versionados;
- rechaza path/nombre inseguro, duplicados, PPI Watch y cualquier unidad externa;
- en Deploy V2: disable/stop, unlink exacto, reset-failed, daemon-reload y verificación
  de inactive + file absent;
- preserva el comportamiento canónico existente para ACTIVE/RETIRED/QUARANTINED.

Regresiones:
- el allowlist actual debe contener exactamente 27 nombres únicos;
- PPI Watch y critical control-plane no pueden entrar en ese allowlist;
- una prueba con filesystem/systemctl simulados demuestra que sólo la unidad exacta se
  retira y que critical approval + PPI Watch permanecen intactos.

## Protecciones administrativas GitHub

La Action obtuvo:
- endpoint repo rulesets: HTTP 200, lista vacía;
- classic branch protection para la rama productiva: HTTP 403 con el token disponible.

Por contrato de evidencia:
- rulesets de repositorio legibles: **0**;
- classic branch protection / required checks administrativos: **NO_VERIFICADO**.

No se infiere ausencia de protección a partir de un HTTP 403.

## #450

`fix/rc6-preopen-not-due-20261003` permanece separado:
- head `0311dcc10139777f8be988f4fe8c3efabc86d1a9`;
- base productiva `da697c6e6c2274579f9e4a112fabc4327475dd35`;
- compare: 2 ahead / 0 behind;
- Predeploy V2 `37131980746`: SUCCESS.

Debe reconciliarse con el próximo candidato único. No requiere ni autoriza un deploy
individual.

## Criterio de cierre de este PR

Antes del handoff:
1. auditoría residual read-only GREEN con todas las unidades host clasificadas;
2. tests/Predeploy V2 exactos GREEN;
3. no solapamiento con #453/#454/#450;
4. sin merge ni deploy;
5. WRITE_OWNER liberado.

El retiro runtime de las 27 units queda pendiente del próximo Deploy V2 consolidado y
no debe presentarse como ya ejecutado.
