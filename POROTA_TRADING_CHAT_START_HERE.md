# POROTA TRADING — empezar una conversación nueva

## Puerta de entrada

No empieces sólo con el último mensaje, una captura o una memoria aislada. Primero:

1. Lee `/AGENTS.md` y `/POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md` completos.
2. Lee `/POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md` completo y todo prompt/checkpoint posterior que el usuario identifique.
3. Comprueba en GitHub el HEAD/SHA actual, cambios posteriores al checkpoint, PRs que toquen el alcance y runs/evidencias recientes. No supongas que el checkpoint sigue siendo el último estado.
4. Resume lo entendido con semáforo, separando código, CI, runtime, evidencia/importación y READY end-to-end. Declara todo dato no verificado.
5. Continúa sólo dentro del alcance y permisos documentados. Actualiza el checkpoint con cada hito material y verifica el commit en GitHub.

Si este chat no tiene acceso al repositorio, informa que no pudo pasar esta puerta y pide el checkpoint completo o el acceso faltante. No digas que recuperaste todo leyendo sólo un título, extracto o mensaje.


## Progreso consultable en Droplet

Antes de una tarea larga, comprueba que el comando de estado `sudo porota-job status <run_id>` existe y funciona directamente en el droplet; registra etapa, progreso real, heartbeat, resultados y gates. La especificación obligatoria está en `/POROTA_TRADING_JOB_PROGRESS_CONTRACT.md`. Toda ingesta histórica total/masiva debe lanzarse directamente en el droplet; no se ejecuta ni se controla con Actions. Si no hay terminal directa o status runtime, detén el lanzamiento y marca el bloqueo como `NOT_IMPLEMENTED_OR_RUNTIME_NOT_VERIFIED`.

## Prompt breve para copiar en chats sin carga automática del repositorio

> Continúa POROTA TRADING desde su checkpoint, sin reconstruir desde cero. Antes de dar estatus o ejecutar nada, lee íntegramente `/AGENTS.md`, `/POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md`, `/POROTA_TRADING_CHAT_START_HERE.md` y `/POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md`. Lee también cualquier checkpoint posterior que yo adjunte o nombre y verifica en GitHub la rama, SHA, PRs y runs posteriores. Lee `/POROTA_TRADING_JOB_PROGRESS_CONTRACT.md` antes de cualquier tarea larga. Conserva todas las decisiones, restricciones, pendientes y evidencia; distingue código, CI, runtime, evidencia/importación y READY end-to-end; marca lo no verificado y no inventes ni repitas trabajos cerrados. Informa primero lo que entendiste y continúa sólo dentro del alcance autorizado. Actualiza el checkpoint después de cada hito y verifica su commit SHA. Las ingestas masivas se ejecutan en el droplet y requieren estado/progreso runtime consultable antes de arrancar.

## Punto operativo actual incluido en el checkpoint

La copia cross-chat conserva íntegro `POROTA_TRADING_CHECKPOINT_ACTIVE_READY_2026-09-14.md` y agrega el delta de la captura contractual reciente. Si evidencia posterior cambia el estado, actualiza primero este checkpoint sin borrar el antecedente.
