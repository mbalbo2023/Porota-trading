# POROTA TRADING RC6 — ARMADO ONE-SHOT DEL CONTROL PLANE CRÍTICO

Fecha: 2026-09-07

`CONTROL_PLANE_PROD_DEPLOY=AUTHORIZED_USER`
`CANONICAL_TELEGRAM_SINGLE_CONSUMER=AUTHORIZED_USER`

SOURCE_SHA=cf2ebf2ca6afe80dfa618437befd6734db708efe

## Alcance exacto

Este archivo arma una única ejecución del workflow `RC6 Critical control-plane permanent deploy` usando exactamente la SHA indicada arriba, cuyo CI enfocado quedó GREEN.

Se autoriza exclusivamente desplegar el plano de control de aprobación crítica para `PRODUCTION_PAPER`:

- mismo bot Telegram canónico de POROTA;
- gateway como único consumidor de `getUpdates` / callbacks;
- broker local GitHub limitado a operaciones de Issues;
- credencial amplia de `gh` permanece en el host y nunca se monta en el contenedor;
- broker protegido además por capability key local aleatoria;
- directorio de secretos `0711 root:root` sólo para traversal; archivos secretos `0400` con propietario específico;
- sin credenciales PPI;
- sin Docker socket dentro del gateway;
- sin capacidad de órdenes;
- rollback del control plane ante preflight/postflight rojo.

## Invariantes que deben permanecer intactas

- observer no reiniciado ni reemplazado;
- dashboard no reiniciado ni reemplazado;
- observer source identity sigue `5076b6dff8c644ed73160b4148eb7a1cf9ca7e43`;
- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- capacidad de órdenes reales bloqueada.

Reintento autorizado después de que el primer intento fallara cerrado y el journal confirmara `PermissionError` sobre `broker_capability.host`; el rollback anterior quedó completo.

Este armado NO autoriza merge del PR #39 ni despliegue de ningún hotfix de trading.
