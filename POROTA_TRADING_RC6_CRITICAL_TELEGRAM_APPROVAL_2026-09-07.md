# POROTA TRADING RC6 — CONTROL DE INCIDENTES CRÍTICOS POR TELEGRAM

Fecha: 2026-09-07  
Estado: **PREPARADO EN RAMA / NO DESPLEGADO**  
Base exacta: `5076b6dff8c644ed73160b4148eb7a1cf9ca7e43` (`hotfix/rc6-paper-t1-settlement-20260907`)  
Rama: `feature/rc6-critical-telegram-approval-20260907`

## Objetivo

Agregar un circuito auditable y accesible desde tablet para que un incidente 🔴 detectado por `Porota Health Watch` solicite autorización inmediata por Telegram antes de preparar una corrección.

El botón **NO ejecuta trading, NO habilita órdenes reales, NO hace merge y NO hace deploy**. Sólo registra en GitHub la decisión humana de permitir o rechazar la preparación de un HOTFIX PAPER.

## Flujo canónico

1. `Porota Health Watch` detecta un 🔴 y realiza diagnóstico read-only.
2. Crea/reutiliza un Issue cuyo título comienza por `[POROTA][RED]`, con evidencia, causa, impacto, plan, base SHA/ref y `HOTFIX_AUTHORIZATION=AWAITING`.
3. El gateway RC6 consulta únicamente Issues de GitHub y detecta el incidente pendiente.
4. Envía a Telegram dos botones: `✅ AUTORIZAR HOTFIX PAPER` y `❌ NO AUTORIZAR`.
5. Sólo acepta el toque del chat autorizado y vuelve a verificar que el Issue siga abierto, crítico, pendiente y sin decisión terminal.
6. Si se aprueba agrega `HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM`; si se rechaza agrega `HOTFIX_AUTHORIZATION=REJECTED_TELEGRAM`.
7. En la siguiente revisión, Health Watch puede preparar exclusivamente rama hotfix + corrección PAPER + tests + checkpoint + CI + PR.
8. **Merge y deploy permanecen bloqueados y requieren autorización separada.**

## Separación de seguridad

`fg_critical_approval_gateway_rc6.py` es un plano de control separado:

- no importa PPI ni el paper broker;
- no contiene rutas de órdenes;
- no usa Docker socket ni subprocess de despliegue;
- rechaza el arranque si recibe credenciales PPI;
- exige `POROTA_RUNTIME_MODE=PRODUCTION_PAPER`;
- exige `POROTA_CRITICAL_APPROVAL_ENABLED=true`;
- conserva SQLite de auditoría separado del ledger PAPER.

### Bot Telegram DEDICADO — requisito obligatorio

POROTA ya posee un consumidor canónico de `getUpdates`. Dos procesos usando el mismo bot/token competirían por offsets y podrían perder callbacks. Por eso el gateway **NO reutiliza** el bot operacional del observer.

Debe recibir exclusivamente:

- `POROTA_CRITICAL_TELEGRAM_BOT_TOKEN`
- `POROTA_CRITICAL_TELEGRAM_CHAT_ID`

Y falla cerrado si en su entorno aparecen `TELEGRAM_BOT_TOKEN` o `TELEGRAM_CHAT_ID` del observer. Esto evita por construcción un segundo consumidor del mismo bot.

## GitHub con privilegio mínimo

El gateway usa un fine-grained token limitado a `mbalbo2023/Porota-trading` con:

- Metadata: Read
- Issues: Read/Write

No necesita Contents, Actions, Pull Requests, Administration, Secrets, Packages ni otros repositorios. El secreto se monta read-only como `/run/secrets/github_issue_control.token` (ruta configurable con `POROTA_GITHUB_ISSUE_TOKEN_FILE`) y nunca se guarda en Git, logs, checkpoint o Telegram.

## Persistencia e idempotencia

SQLite: `data/paper_v17/critical_approval_rc6.db`.

Registra número de Issue, primer avistamiento, aviso, decisión, hash corto no reversible del actor, comentario GitHub y offset del bot dedicado. GitHub es la autoridad canónica; una decisión terminal no puede sobrescribirse.

## Semántica de autorización

`✅ AUTORIZAR HOTFIX PAPER` autoriza sólo análisis/corrección PAPER, rama hotfix, tests, CI, checkpoint y PR.

No autoriza merge, deploy, cambio de modo, reinicio, credenciales, órdenes reales ni ninguna operación PPI.

`❌ NO AUTORIZAR` registra el rechazo y el monitor no prepara hotfix para ese incidente.

## Integración con Health Watch

La tarea automática quedó configurada para crear Issues `[POROTA][RED]` idempotentes, registrar `HOTFIX_AUTHORIZATION=AWAITING`, leer la decisión, preparar hotfix/CI/PR sólo tras autorización y nunca hacer merge/deploy. `real_orders_sent=0` y capacidad real bloqueada siguen siendo invariantes.

## Tests y CI

`tests/test_critical_approval_gateway_rc6.py` cubre aviso único, autorización, rechazo, sender incorrecto, Issue cerrado/no autorizable, idempotencia, offset, rechazo de credenciales PPI, modo obligatorio y DB sin tablas de trading.

`.github/workflows/rc6-critical-telegram-approval.yml` compila, ejecuta pytest y prueba estáticamente la separación del control plane, ausencia de capacidad operativa y alcance limitado de la autorización.

## Pendientes antes de activar runtime

1. CI GREEN y revisión del PR.
2. Crear bot Telegram dedicado para aprobaciones y registrar únicamente su token/chat en el control plane.
3. Provisionar token GitHub Issues-only como secreto de host.
4. Crear/validar servicio separado sin PPI credentials y sin Docker socket.
5. Prueba E2E con Issue crítico ficticio controlado.
6. Verificar que el toque sólo crea el comentario GitHub y no modifica runtime.
7. Pedir autorización explícita del usuario para desplegar este control plane.

## Estado actual

Este trabajo **no altera el runtime RC6 vigente**. El observer PAPER continúa sobre la línea T+1 validada. `real_orders_sent=0` debe permanecer invariante.

**Veredicto:** preparado para CI/PR; **NO autorizado para despliegue**.
