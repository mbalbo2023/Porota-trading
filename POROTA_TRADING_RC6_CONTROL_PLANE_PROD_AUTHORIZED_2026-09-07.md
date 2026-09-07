# POROTA TRADING RC6 — AUTORIZACIÓN CONTROL PLANE PERMANENTE

Fecha: 2026-09-07

El operador autorizó explícitamente en ChatGPT: **“Continúa hasta el deploy”**.

`CONTROL_PLANE_PROD_DEPLOY=AUTHORIZED_USER`

## Alcance autorizado

- completar validación y hardening del control plane crítico;
- ejecutar CI y preflight read-only;
- desplegar únicamente el gateway de autorización crítica para `PRODUCTION_PAPER`;
- realizar postflight y rollback fail-closed;
- mergear el PR #39 cuando el código y el despliegue queden probados GREEN.

## Fuera de alcance

- modificar o reiniciar `porota_production_observer`;
- modificar o reiniciar `porota_production_dashboard`;
- cambiar lógica de trading, riesgo, señales o ejecución;
- leer o modificar credenciales PPI;
- habilitar órdenes reales;
- enviar operaciones a PPI;
- considerar este deploy como autorización para desplegar futuros hotfixes PAPER.

## Invariantes

- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- bot Telegram dedicado para el control plane;
- token GitHub fine-grained limitado a este repositorio con Metadata read + Issues read/write; sin Contents/Actions/Admin;
- secretos fuera del repositorio y fuera de `docker inspect`;
- sin Docker socket;
- sin credenciales PPI;
- fail-closed ante cualquier preflight/postflight rojo.
