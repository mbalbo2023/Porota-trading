# POROTA TRADING — RC6 Issue #41 Hotfix Checkpoint — 2026-09-07

## Estado

🟢 HOTFIX PAPER preparado y validado en CI.
🟡 NO desplegado en el Droplet.
🟡 NO mergeado.

## Incidente canónico

- Issue: #41 — `[POROTA][RED] RC6 control plane permanente no inicia broker local y hace rollback`
- Fingerprint: `RC6_CONTROL_PLANE_BROKER_SOCKET_NOT_CREATED_AFTER_CAPABILITY_HARDENING`
- Base/source SHA documentada: `fe36e16b48a189e820308d2f42b0810fc6bb292b`
- Base ref: `feature/rc6-critical-telegram-approval-20260907`
- Autorización recibida: `HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM`

## Corrección PAPER

Rama:
`hotfix/rc6-control-plane-broker-permissions-issue-41`

PR:
#42 — `[POROTA][PAPER HOTFIX] RC6 broker capability permissions — Issue #41`

Corrección aplicada únicamente al control plane:
- `/opt/porota-control-plane-rc6/secrets` conserva modo `0700`;
- ownership pasa explícitamente a `CONTROL_USER:CONTROL_GROUP` para permitir traversal al broker no-root;
- `broker_capability.host` conserva modo `0400`;
- se agregó validación fail-fast de lectura como usuario real del broker;
- se mantienen los guards de secretos y capability issues-only;
- no se modificó lógica de trading, observer, dashboard ni ejecución de órdenes.

## CI

Primer run:
- Run `34152771085`
- Resultado: FAILURE
- Causa: runner sin `pytest` (`No module named pytest`)
- Clasificación: fallo de infraestructura del workflow, no de la corrección.

Corrección del workflow:
- Commit `af696c03d33f758b620992f5f94bee3e37e2e5c0`
- se agregó setup de Python 3.11 e instalación explícita de `pytest`.

Segundo run:
- Run `34153633686`
- Job `focused-safety`: SUCCESS
- setup-python: SUCCESS
- instalación de pytest: SUCCESS
- shell syntax: SUCCESS
- focused regression tests: SUCCESS
- safety guards: SUCCESS

CI_STATUS=GREEN

## Invariantes de seguridad

- `PRODUCTION_PAPER` guard retenido.
- `real_orders_sent=0` guard retenido.
- No se habilitaron órdenes reales.
- No se modificó runtime de trading.
- No se expuso GitHub token al contenedor gateway.
- No hubo merge.
- No hubo deploy.

## Pendiente residual obligatorio

Este incidente NO debe marcarse como completamente resuelto en producción hasta completar, con autorización separada y explícita:

1. autorización de deploy;
2. merge/promoción según flujo aprobado;
3. deploy controlado del control plane;
4. postflight en Droplet comprobando socket broker, servicios activos, Telegram single-consumer, capability issues-only y ausencia de mounts/secretos prohibidos;
5. reconfirmación de `PRODUCTION_PAPER` y `real_orders_sent=0`;
6. sólo entonces cerrar Issue #41.

DEPLOY_AUTHORIZATION=AWAITING
MERGE_AUTHORIZATION=AWAITING
ISSUE_41_OPERATIONAL_RESOLUTION=PENDING_DEPLOY_VALIDATION
