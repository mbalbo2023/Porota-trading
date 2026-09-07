# POROTA TRADING RC6 — CRITICAL CONTROL PLANE DEPLOYED

Fecha: 2026-09-07

## Veredicto

`CONTROL_PLANE_PERMANENT_DEPLOY=GREEN`

Workflow: `RC6 Critical control-plane permanent deploy`
Run: `34149414437`
Run conclusion: `success`

Source SHA desplegada:

`cf2ebf2ca6afe80dfa618437befd6734db708efe`

## Evidencia de postflight

- `DEPLOY_AUTHORIZATION=GREEN`
- `FOCUSED_CI=GREEN`
- `SECRETS_DIRECTORY=0711_ROOT_TRAVERSE_ONLY`
- `BROKER_LOCAL_CAPABILITY=GENERATED_VALUE_NOT_LOGGED`
- `CONTROL_PLANE_CONTAINER=true|0|true|porota-trading-bot:17.0.0-rc6|botuser`
- `FORBIDDEN_MOUNT=NONE`
- `CONTAINER_SECRET_ENV_GUARD=GREEN`
- `TELEGRAM_SINGLE_CONSUMER=GREEN`
- `ISSUES_CAPABILITY_BROKER=GREEN`
- `OBSERVER_POST=true|0|true|porota-trading-bot:17.0.0-rc6|["bv_paper_runtime.py"]`
- `DASHBOARD_POST=true|0|porota-trading-dashboard:17.0.0-rc6-operator-ux`
- `OBSERVER_DB_POST=ok|PRODUCTION_PAPER|0`
- `REAL_ORDERS_SENT=0`
- `TRADING_RUNTIME_UNCHANGED=GREEN`
- `CONTROL_PLANE_SOURCE_SHA=cf2ebf2ca6afe80dfa618437befd6734db708efe`
- `CONTROL_PLANE_PERMANENT_DEPLOY=GREEN`

## Arquitectura activa

- mismo bot Telegram canónico de POROTA;
- gateway crítico como único consumidor autorizado de `getUpdates` / callbacks;
- broker GitHub local limitado a operaciones de Issues;
- credencial `gh` permanece en el host y no se monta en el contenedor;
- capability key local aleatoria entre gateway y broker;
- gateway sin credenciales PPI;
- gateway sin Docker socket;
- gateway sin capacidad de enviar órdenes;
- secretos individuales `0400`, con directorio `0711 root:root` sólo para traversal.

## Invariantes preservadas

- observer no fue reemplazado ni reiniciado;
- dashboard no fue reemplazado ni reiniciado;
- `PRODUCTION_PAPER` continúa activo;
- `real_orders_sent=0`;
- no se habilitaron órdenes reales;
- este deploy no implica merge del PR #39 ni despliegue de hotfix de trading.

## Incidente del primer intento

El primer intento falló cerrado por `PermissionError` al intentar el broker leer `broker_capability.host` dentro de un directorio `0700 root:root`. El rollback quedó completo. La RCA se corrigió cambiando sólo el directorio de secretos a `0711 root:root`, manteniendo cada secreto individual en `0400` con su propietario específico. El segundo intento completó preflight, instalación y postflight en GREEN.
