# POROTA TRADING RC6 — CONTROL DE INCIDENTES CRÍTICOS POR TELEGRAM

Fecha: 2026-09-07  
Estado de este cambio: **PREPARADO EN RAMA / NO DESPLEGADO**  
Base exacta: `5076b6dff8c644ed73160b4148eb7a1cf9ca7e43` (`hotfix/rc6-paper-t1-settlement-20260907`)  
Rama: `feature/rc6-critical-telegram-approval-20260907`

## 1. Objetivo

Agregar un circuito auditable y accesible desde tablet para que un incidente 🔴 detectado por `Porota Health Watch` pueda pedir autorización inmediata por Telegram antes de preparar una corrección de código.

El botón **NO ejecuta trading, NO habilita órdenes reales, NO hace merge y NO hace deploy**. Sólo registra en GitHub la decisión humana de permitir o rechazar la preparación de un HOTFIX PAPER.

## 2. Flujo canónico

1. `Porota Health Watch` detecta un nuevo estado 🔴 y realiza diagnóstico read-only.
2. Si no existe ya el mismo incidente, crea un Issue GitHub con título que comienza por `[POROTA][RED]`.
3. El cuerpo del Issue incluye evidencia, causa probable, impacto, plan propuesto, base SHA/ref y la marca exacta:

   `HOTFIX_AUTHORIZATION=AWAITING`

4. El gateway RC6 consulta únicamente Issues de GitHub y detecta ese incidente pendiente.
5. Envía a Telegram:
   - `✅ AUTORIZAR HOTFIX PAPER`
   - `❌ NO AUTORIZAR`
6. Sólo se acepta el toque si el `sender_id` coincide exactamente con `TELEGRAM_CHAT_ID`.
7. Antes de registrar la decisión, el gateway vuelve a consultar GitHub y exige que:
   - el Issue siga abierto;
   - el título siga siendo `[POROTA][RED]...`;
   - el cuerpo siga conteniendo `HOTFIX_AUTHORIZATION=AWAITING`;
   - no exista ya una decisión terminal.
8. Si se aprueba, el gateway agrega un comentario al Issue con:

   `HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM`

   Si se rechaza:

   `HOTFIX_AUTHORIZATION=REJECTED_TELEGRAM`

9. En la siguiente revisión automática, `Porota Health Watch` puede preparar exclusivamente:
   - rama `hotfix/...` desde la SHA registrada;
   - corrección;
   - tests;
   - checkpoint;
   - CI;
   - Pull Request.
10. Merge y deploy permanecen bloqueados y requieren autorización separada.

## 3. Separación de seguridad

El nuevo módulo es `fg_critical_approval_gateway_rc6.py`.

Diseño deliberado:
- no importa PPI;
- no importa el paper broker;
- no importa el motor de órdenes;
- no usa Docker socket;
- no llama subprocess para ejecutar comandos;
- rechaza el arranque si recibe credenciales PPI en su entorno;
- sólo debe correr en `POROTA_RUNTIME_MODE=PRODUCTION_PAPER`;
- sólo se habilita con `POROTA_CRITICAL_APPROVAL_ENABLED=true`;
- conserva su propio SQLite de auditoría, separado del ledger PAPER.

El proceso necesita acceso de red únicamente a:
- Telegram Bot API;
- GitHub Issues API.

## 4. Permiso GitHub mínimo

No reutilizar un PAT administrativo ni un token con permisos de Contents/Actions si puede evitarse.

Crear un **fine-grained token dedicado** al repositorio `mbalbo2023/Porota-trading` con:
- Metadata: Read;
- Issues: Read and Write.

No requiere:
- Contents Write;
- Actions Write;
- Pull Requests Write;
- Administration;
- Secrets;
- Packages;
- acceso a otros repositorios.

El valor NO debe guardarse en Git, `.env`, logs, checkpoint ni Telegram. El contrato del módulo espera un archivo read-only montado como:

`/run/secrets/github_issue_control.token`

La ruta puede cambiarse con `POROTA_GITHUB_ISSUE_TOKEN_FILE`.

## 5. Telegram y lector único

El runtime split `PRODUCTION_PAPER` vigente tiene Telegram en modo de notificaciones y no ejecuta el monolito legacy. El gateway deberá ser el único consumidor interactivo de `getUpdates` en ese modo.

No desplegar este gateway simultáneamente con otro proceso que consuma el mismo bot/offset. Si en el futuro se reactiva otro lector Telegram, se debe consolidar el routing antes de activar este servicio.

Cada tap vuelve a validarse por `sender_id == TELEGRAM_CHAT_ID`.

## 6. Persistencia e idempotencia

SQLite local del gateway:

`data/paper_v17/critical_approval_rc6.db`

Tablas:
- `critical_approval_state`;
- `critical_approval_meta`.

Se guarda:
- número de Issue;
- primer avistamiento;
- fecha de aviso;
- decisión;
- hash corto no reversible del actor Telegram;
- id del comentario GitHub;
- offset Telegram.

La autoridad final de la decisión es GitHub. Antes de un tap se consultan los comentarios existentes y una decisión previa no puede sobrescribirse.

## 7. Semántica de los botones

### ✅ AUTORIZAR HOTFIX PAPER

Autoriza únicamente:
- análisis/corrección PAPER;
- creación de rama hotfix;
- tests;
- CI;
- checkpoint;
- PR.

No autoriza:
- merge;
- deploy;
- cambio de modo;
- reinicio del observer;
- modificación de credenciales;
- habilitar órdenes reales;
- ninguna operación en PPI.

### ❌ NO AUTORIZAR

Registra rechazo y el monitor no debe preparar hotfix para ese incidente.

## 8. Integración con Porota Health Watch

La tarea automática fue actualizada para:
- crear Issues `[POROTA][RED]` idempotentes por incidente/fingerprint;
- incluir `HOTFIX_AUTHORIZATION=AWAITING`;
- revisar decisiones registradas en comentarios;
- ante `AUTHORIZED_TELEGRAM`, preparar rama/corrección/tests/checkpoint/CI/PR;
- ante `REJECTED_TELEGRAM`, no modificar código;
- nunca hacer merge ni deploy;
- mantener `real_orders_sent=0` y capacidad real bloqueada como invariantes.

## 9. Pruebas incluidas

`tests/test_critical_approval_gateway_rc6.py` cubre como mínimo:
- un incidente pendiente se notifica una sola vez;
- autorización válida se registra en GitHub y SQLite;
- rechazo válido se registra;
- sender Telegram incorrecto falla cerrado;
- Issue cerrado no puede aprobarse;
- Issue sin marker canónico no puede aprobarse;
- decisión terminal es idempotente;
- offset Telegram se persiste;
- el proceso rechaza credenciales PPI;
- el proceso rechaza modos distintos de `PRODUCTION_PAPER`;
- el SQLite del gateway no contiene tablas de órdenes/trading.

Workflow enfocado:

`.github/workflows/rc6-critical-telegram-approval.yml`

Además de pytest, verifica estáticamente que el gateway no importe componentes PPI/order y que preserve el alcance de autorización.

## 10. Pendiente antes de activar en runtime

Este commit/PR **no debe desplegarse automáticamente**.

Antes de activar el gateway se requiere:
1. CI GREEN.
2. revisión de seguridad del PR.
3. provisionar el fine-grained GitHub Issues token como secreto de host, sin pegarlo en ChatGPT ni GitHub code.
4. confirmar que no existe otro `getUpdates` activo para el mismo bot en `PRODUCTION_PAPER`.
5. agregar/validar un contenedor o servicio de control separado, sin PPI credentials y sin Docker socket.
6. prueba E2E con un Issue ficticio de severidad controlada.
7. comprobar que el toque sólo crea el comentario de autorización y no produce cambios runtime.
8. autorización explícita del usuario para el deploy del control plane.

## 11. Estado del runtime actual

Este trabajo no altera el runtime RC6 vigente. El observer PAPER continúa sobre la línea T+1 ya validada y `real_orders_sent=0` debe seguir siendo invariante.

**VEREDICTO DEL CAMBIO EN ESTA ETAPA:** preparado para CI/PR; no autorizado para despliegue.
