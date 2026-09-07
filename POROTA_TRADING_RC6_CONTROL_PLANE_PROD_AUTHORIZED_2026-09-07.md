# POROTA TRADING RC6 — AUTORIZACIÓN CONTROL PLANE PERMANENTE

Fecha: 2026-09-07

El operador autorizó explícitamente en ChatGPT: **“Continúa hasta el deploy”** y posteriormente aprobó reutilizar el mismo bot Telegram de POROTA con enforcement de consumidor único.

`CONTROL_PLANE_PROD_DEPLOY=AUTHORIZED_USER`
`CANONICAL_TELEGRAM_SINGLE_CONSUMER=AUTHORIZED_USER`

## Alcance autorizado

- completar validación y hardening del control plane crítico;
- ejecutar CI y preflight read-only;
- reutilizar el bot Telegram canónico de POROTA sólo si el gateway queda como único consumidor de `getUpdates`/callbacks;
- desplegar únicamente el gateway de autorización crítica para `PRODUCTION_PAPER`;
- realizar postflight y rollback fail-closed.

## Fuera de alcance

- modificar o reiniciar `porota_production_observer`;
- modificar o reiniciar `porota_production_dashboard`;
- cambiar lógica de trading, riesgo, señales o ejecución;
- leer o modificar credenciales PPI;
- habilitar órdenes reales;
- enviar operaciones a PPI;
- considerar este deploy como autorización para desplegar futuros hotfixes PAPER;
- introducir un segundo consumidor Telegram concurrente.

## Invariantes

- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- un solo consumidor de `getUpdates` para el bot canónico;
- otros componentes pueden seguir enviando mensajes con el mismo bot, pero no consumir updates/callbacks;
- credencial GitHub limitada a la capacidad necesaria para leer Issues y registrar aprobación/rechazo; no debe otorgar capacidad de trading;
- secretos fuera del repositorio y fuera de `docker inspect`;
- sin Docker socket;
- sin credenciales PPI;
- fail-closed ante cualquier preflight/postflight rojo.

## Política de source identity

El deploy del control plane puede realizarse desde la rama del PR #39 sin avanzar la rama live del observer. No se debe mover la identidad canónica del observer `5076b6dff8c644ed73160b4148eb7a1cf9ca7e43` sólo para incorporar este plano de control separado.
