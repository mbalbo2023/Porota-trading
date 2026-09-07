# POROTA TRADING — RC6 HOTFIX PAPER — Issue #41

Fecha: 2026-09-07
Issue canónico: #41 `[POROTA][RED] RC6 control plane permanente no inicia broker local y hace rollback`
Fingerprint: `RC6_CONTROL_PLANE_BROKER_SOCKET_NOT_CREATED_AFTER_CAPABILITY_HARDENING`

## Autorización

`HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM`

Autorizado únicamente: preparación de corrección PAPER, tests, CI, checkpoint y PR.
No autorizado: merge, deploy, órdenes reales ni cambios al runtime de trading.

## Base

Base SHA documentada: `fe36e16b48a189e820308d2f42b0810fc6bb292b`
Rama: `hotfix/rc6-control-plane-broker-permissions-issue-41`

## Diagnóstico

El deploy original creaba `/opt/porota-control-plane-rc6/secrets` como `root:root 0700`, mientras `porota-critical-github-proxy-rc6.service` ejecuta como el usuario no-root del control plane. El archivo `broker_capability.host` pertenecía a ese usuario, pero el proceso no podía atravesar el directorio padre para leerlo; el broker no llegaba a crear `github.sock` y el deploy hacía rollback fail-closed.

## Corrección PAPER preparada

- El directorio `secrets` sigue en modo `0700`, pero ahora pertenece a `CONTROL_USER:CONTROL_GROUP`; no se amplían bits de acceso.
- `broker_capability.host` permanece `0400` y propiedad del usuario del broker.
- Antes de instalar/activar unidades systemd se ejecuta explícitamente `sudo -u CONTROL_USER test -r broker_capability.host`.
- Se valida además ownership/modo exacto del directorio.
- Se mantienen los guards postflight `PRODUCTION_PAPER` y `real_orders_sent=0`.
- Se agregan tests de regresión específicos.

## Seguridad

La corrección no toca observer, dashboard, lógica de trading, PPI ni ejecución de órdenes. No introduce token GitHub dentro del contenedor y conserva el broker Issues-only, socket Unix y rollback fail-closed.

`REAL_ORDERS_SENT_EXPECTED=0`
`TRADING_RUNTIME_CHANGE=NO`
`MERGE_AUTHORIZATION=NO`
`DEPLOY_AUTHORIZATION=NO`
