# POROTA TRADING — RC6 CONTROL PLANE FINAL CHECKPOINT — 2026-09-07

## Veredicto

`CONTROL_PLANE_STATUS=FINAL_GREEN`
`CHAT_CAN_BE_CLOSED=YES`
`CANONICAL_CONTROL_PLANE_PR=39`
`PR_39_MERGED=YES`
`PR_42_SUPERSEDED=YES`
`ISSUE_41_CLOSED=YES`
`PRODUCTION_PAPER=REQUIRED`
`REAL_ORDERS_SENT=0_REQUIRED`

## Integración canónica

PR #39 — `RC6: autorización de hotfix crítico por Telegram, fail-closed`

- Head: `feature/rc6-critical-telegram-approval-20260907`
- Base: `hotfix/rc6-paper-t1-settlement-20260907`
- Head SHA validada: `80f51c207cfbe7850ae3540cff77931736ac2c85`
- Merge commit: `dba6ae423231701f622660e08cf7d73e42c6b91d`
- Estado: MERGED / CLOSED

La rama base quedó apuntando al merge commit canónico `dba6ae423231701f622660e08cf7d73e42c6b91d`.

## CI final previo al merge

Workflow: `RC6 Critical Telegram approval safety`
Run: `34154435864`
Resultado: SUCCESS

Cobertura confirmada:
- compile control plane;
- sintaxis de scripts de deploy/healthcheck;
- focused tests;
- separación del motor de trading;
- contenedor sin token GitHub expuesto;
- broker local autenticado y capability Issues-only;
- dedicated gateway healthcheck;
- authorization scope.

## Deploy permanente ya validado

Workflow: `RC6 Critical control-plane permanent deploy`
Run: `34149414437`
Resultado: SUCCESS

Source SHA desplegada documentada: `cf2ebf2ca6afe80dfa618437befd6734db708efe`.

Postflight canónico:
- `CONTROL_PLANE_PERMANENT_DEPLOY=GREEN`
- `TELEGRAM_SINGLE_CONSUMER=GREEN`
- `ISSUES_CAPABILITY_BROKER=GREEN`
- `FORBIDDEN_MOUNT=NONE`
- `CONTAINER_SECRET_ENV_GUARD=GREEN`
- observer sin reemplazo/restart;
- dashboard sin reemplazo/restart;
- `OBSERVER_DB_POST=ok|PRODUCTION_PAPER|0`
- `REAL_ORDERS_SENT=0`
- `TRADING_RUNTIME_UNCHANGED=GREEN`

No se repitió el deploy después del merge porque el mismo control-plane ya estaba desplegado y validado GREEN; repetirlo habría agregado riesgo sin beneficio.

## RCA Issue #41

Issue #41 — `[POROTA][RED] RC6 control plane permanente no inicia broker local y hace rollback`

Primer intento:
- broker no-root no podía atravesar `secrets` con `0700 root:root`;
- no se creó el socket Unix;
- rollback fail-closed completo;
- trading runtime no afectado.

Corrección canónica:
- directorio `secrets` en `0711 root:root` para traversal sin listado;
- secretos individuales mantienen `0400` y propietario específico;
- broker capability host aislada del contenedor;
- gateway sin credenciales PPI;
- gateway sin Docker socket;
- gateway sin capacidad de enviar órdenes.

Issue #41 quedó CLOSED / COMPLETED después de evidencia de postflight GREEN.

## Eliminación de duplicación

PR #42 — `[POROTA][PAPER HOTFIX] RC6 broker capability permissions — Issue #41`

- CLOSED
- NOT MERGED
- SUPERSEDED BY PR #39
- NO MERGE
- NO DEPLOY

No existe segundo control-plane autorizado.
No existe segundo gateway Telegram autorizado.
No existe segundo broker GitHub autorizado.
No existe segundo camino de deploy autorizado para #42.

Ramas históricas/audit/closure/fix pueden permanecer como evidencia Git, pero no implican procesos activos ni implementaciones runtime paralelas.

## Estado RED al cierre

No quedan incidentes `[POROTA][RED]` reales abiertos asociados a este control-plane.

Issue #40 permanece abierto únicamente como:
`[POROTA][RED][E2E-TEST] Prueba controlada del circuito de autorización Telegram RC6`

Por protocolo:
- `E2E_TEST=true`
- no es incidente real;
- no autoriza hotfix;
- no autoriza merge;
- no autoriza deploy;
- debe ser ignorado por automatización de hotfix real.

Otros issues abiertos detectados pertenecen a backlog/general RC6 o NEXT VERSION y no bloquean el cierre de este hilo de control-plane.

## Arquitectura final única

`Porota Health Watch -> gateway crítico Telegram -> broker local GitHub Issues-only -> GitHub Issue`

Invariantes:
- un solo consumidor Telegram canónico;
- un solo gateway crítico;
- un solo broker Issues-only;
- fail-closed;
- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- órdenes reales bloqueadas;
- trading runtime sin cambios por este control-plane.

## Pendientes fuera del alcance de este checkpoint

Este checkpoint cierra exclusivamente la implementación, RCA, deploy e integración del control-plane crítico RC6. No declara cerrados otros pendientes funcionales/operativos de POROTA RC6 (históricos, calendarios, T+1, PPI, Contract Evidence, introspección, SRE, UX, IOL next version, etc.), que deben continuar desde sus checkpoints/issues correspondientes.

## Continuidad

Para cualquier nuevo chat sobre este tema, usar este archivo como checkpoint final del control-plane RC6 y NO reconstruir una segunda implementación desde PR #42 o ramas históricas.
