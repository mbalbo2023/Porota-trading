# POROTA TRADING RC6 — CONTROL DE INCIDENTES CRÍTICOS POR TELEGRAM

Fecha: 2026-09-07  
Estado: **E2E PROBADO / PREPARANDO DEPLOY PERMANENTE**  
Base exacta observer: `5076b6dff8c644ed73160b4148eb7a1cf9ca7e43` (`hotfix/rc6-paper-t1-settlement-20260907`)  
Rama control plane: `feature/rc6-critical-telegram-approval-20260907`

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
8. **Merge y deploy del hotfix permanecen bloqueados y requieren autorización separada.**

## Separación de seguridad

`fg_critical_approval_gateway_rc6.py` es un plano de control separado:

- no importa PPI ni el paper broker;
- no contiene rutas de órdenes;
- no usa Docker socket ni subprocess de despliegue;
- rechaza el arranque si recibe credenciales PPI;
- exige `POROTA_RUNTIME_MODE=PRODUCTION_PAPER`;
- exige `POROTA_CRITICAL_APPROVAL_ENABLED=true`;
- conserva SQLite de auditoría separado del ledger PAPER.

## Telegram canónico con consumidor único

El diseño permanente reutiliza **el mismo bot Telegram operativo de POROTA** para mantener un solo chat y simplificar la accesibilidad. El riesgo no está en que varios componentes envíen mensajes con el mismo bot; el riesgo aparece si dos procesos distintos consumen `getUpdates`/callbacks y compiten por el mismo offset.

Política aprobada:

- observer, dashboard y otros componentes pueden **enviar** mensajes con el bot canónico;
- el gateway crítico es el **único consumidor de `getUpdates`/callback_query`**;
- el preflight debe abortar si detecta un consumidor legacy/concurrente;
- el token/chat existentes se copian a archivos secretos separados para el control plane y nunca se inyectan como `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` dentro del contenedor;
- el contenedor no recibe Docker socket ni credenciales PPI.

Los nombres `POROTA_CRITICAL_TELEGRAM_*` se mantienen como namespace interno del control plane, aunque sus valores provengan del bot canónico autorizado.

## GitHub

El gateway necesita leer Issues críticos y registrar únicamente el comentario de aprobación/rechazo. La preferencia es una credencial mínima limitada al repositorio con Issues read/write. Si se reutiliza una sesión existente de `gh`, sus capacidades deben auditarse antes del deploy y no deben exponerse valores de token.

## Persistencia e idempotencia

SQLite: `data/paper_v17/critical_approval_rc6.db`.

Registra número de Issue, primer avistamiento, aviso, decisión, hash corto no reversible del actor, comentario GitHub y offset Telegram. GitHub es la autoridad canónica; una decisión terminal no puede sobrescribirse.

## Semántica de autorización

`✅ AUTORIZAR HOTFIX PAPER` autoriza sólo análisis/corrección PAPER, rama hotfix, tests, CI, checkpoint y PR.

No autoriza merge, deploy, cambio de modo, reinicio, credenciales, órdenes reales ni ninguna operación PPI.

`❌ NO AUTORIZAR` registra el rechazo y el monitor no prepara hotfix para ese incidente.

## E2E probado 2026-09-07

Issue sintético #40 `[POROTA][RED][E2E-TEST]`:

- gateway temporal aislado levantado;
- botón Telegram recibido y pulsado;
- decisión registrada como `HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM`;
- observer siguió running/restart 0/readonly;
- dashboard siguió running/restart 0;
- DB `quick_check=ok`;
- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- runtime de trading sin cambios;
- contenedor E2E eliminado al finalizar.

## Integración con Health Watch

La tarea automática quedó configurada para crear Issues `[POROTA][RED]` idempotentes, registrar `HOTFIX_AUTHORIZATION=AWAITING`, leer la decisión, preparar hotfix/CI/PR sólo tras autorización y nunca hacer merge/deploy. Los Issues `[E2E-TEST]` están excluidos de cualquier hotfix automático.

## Pendientes para activar runtime permanente

1. Mantener CI GREEN sobre la rama de control plane.
2. Auditar la capacidad GitHub disponible sin exponer tokens.
3. Instalar servicio/contenedor separado con bot canónico y single-consumer enforcement.
4. Ejecutar postflight: observer/dashboard inalterados, DB OK, `PRODUCTION_PAPER`, `real_orders_sent=0`.
5. Rollback automático del control plane ante cualquier rojo.

## Source identity

El plano de control es independiente del observer. No se debe avanzar la rama live del observer ni cambiar su image/source identity sólo para desplegar este servicio. El observer permanece sobre `5076b6dff8c644ed73160b4148eb7a1cf9ca7e43` hasta una decisión de release separada.

**Veredicto:** E2E probado; deploy permanente autorizado por el operador, condicionado a preflight y postflight GREEN.
