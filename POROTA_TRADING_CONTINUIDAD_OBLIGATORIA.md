# Protocolo obligatorio de continuidad POROTA TRADING

Versión: `2026-09-14/2`

Aplica a toda persona, chat, agente o automatización que trabaje sobre POROTA TRADING. La memoria conversacional, un mensaje aislado o un checkpoint viejo nunca sustituyen la verificación del repositorio y del estado más reciente.

## Puerta obligatoria al iniciar cada conversación

Antes de dar estatus, sugerir cambios o ejecutar una acción:

1. Lee completo `/AGENTS.md`, este protocolo y `/POROTA_TRADING_CHAT_START_HERE.md`.
2. Lee de principio a fin el checkpoint canónico `/POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md`. Lee también el ID/checkpoint, prompt o handoff más nuevo que el usuario adjunte o nombre. Un checkpoint posterior se trata como delta que debe verificarse e integrarse; no reemplaza silenciosamente el checkpoint completo.
3. En GitHub verifica repositorio, rama y SHA exactos, PRs abiertos que toquen el alcance, y los commits/runs/evidencias posteriores al checkpoint. Una rama llamada ready o un mensaje de éxito no bastan para probar el estado operativo.
4. Separa cada afirmación como código presente, CI, ejecución runtime, evidencia persistida/importada, estado de servicio/DB y resultado end-to-end. Usa `NOT_VERIFIED`, `UNKNOWN` o `STALE` cuando corresponda. No inventes ni completes vacíos por inferencia.
5. Sólo después entrega el estatus y continúa dentro del alcance autorizado. Si GitHub, el checkpoint o una referencia clave no están accesibles, dilo claramente y limita el trabajo a lo verificable. Nunca afirmes haber recuperado un contexto que no leíste.

Al reanudar una tarea, primero busca si código, run, captura, importación, prueba o decisión posterior ya la cerró. Usa la evidencia directa más reciente compatible con el alcance. Si fuentes discrepan, conserva ambas, explica qué evidencia posterior prevalece y qué queda sin verificar.

## Estándar obligatorio de checkpoint

El checkpoint canónico debe permitir que alguien sin acceso al chat anterior continúe sin reconstruir la historia. Conserva todas las decisiones, hechos, pendientes y evidencia única; no omitas errores, resultados parciales, bloqueos, fuentes descartadas ni instrucciones de seguridad.

Cada checkpoint debe registrar, según aplique:

- ID único, fecha/hora y zona horaria; repositorio, rama/ref, SHA base y baseline operativo exacto.
- Objetivo, alcance, exclusiones expresas, responsable/workstream y restricciones del usuario.
- Invariantes y gates de seguridad vigentes.
- Hecho, en curso, pendiente y bloqueado, con porcentajes sólo si existe una medida reproducible.
- Para cada cierre: archivo/ruta, commit SHA, workflow/run/job, hora, resultado y evidencia de runtime/importación cuando corresponda.
- Estado separado de código, CI, host/servicio, DB, evidencia y READY end-to-end.
- Decisiones vigentes, causas y evidencia; hechos supersedidos con motivo y fecha, sin borrar el historial.
- Riesgos, datos desconocidos/no verificados, criterio de no-go y siguiente acción exacta con sus precondiciones.

No reduzcas cobertura de tareas a datos válidos, código a desplegado, captura a importada, ni `READY_PAPER_SPOT` a READY end-to-end. Registra explícitamente los conteos y su denominador.

## Actualización y handoff entre chats

- Todo cambio material —decisión, commit, run, prueba, despliegue autorizado, hallazgo, bloqueo o cambio de estado— requiere actualizar el checkpoint en el mismo flujo, aunque la actualización sea breve.
- Añade el delta sin borrar información anterior que siga siendo relevante. Si el checkpoint tiene otro dueño activo, no lo sobrescribas: crea un delta en una rama aislada, anota que está pendiente de integración y coordina con el responsable.
- Si no hay información suficiente, registra qué falta como `NOT_VERIFIED` y deja el paso exacto para resolverlo; no inventes una conclusión para que el checkpoint parezca completo.
- Después del commit, vuelve a leer el archivo desde GitHub y verifica rama, contenido y SHA. No digas checkpoint actualizado antes de esa comprobación.
- Antes de cambiar de chat o acercarse al límite de contexto, deja un handoff autocontenido: checkpoint ID, estado, restricciones, evidencia, pendientes exactos y primer paso. El chat siguiente debe empezar por la puerta obligatoria anterior.

## Regla de conservación del histórico PPI

El checkpoint vigente al 2026-09-14 registra cerradas las corridas masivas `PPI-HIST-20260912-001` y `PPI-WEB-RESIDUAL-20260913-001`. No se repiten ni se arranca otro writer sin autorización expresa y causa verificable nueva. El estado final de cada corrida no certifica que todos sus históricos sean válidos ni que los instrumentos estén READY.

El aislamiento del workstream `pipeline ppi watch` sigue vigente cuando su responsable indique una ejecución activa. Un checkpoint explícito del usuario puede autorizar una inspección GitHub de solo lectura de runs, código o artefactos nombrados para una tarea distinta; eso no autoriza leer o cambiar la DB o el host, reiniciar servicios, importar evidencia ni habilitar POST.

## Límites de lo que GitHub puede exigir

`AGENTS.md` es el punto de entrada para agentes que cargan instrucciones del repositorio; el README, este protocolo y el archivo de inicio lo hacen visible para las demás herramientas. Ningún archivo dentro de un repositorio puede obligar técnicamente a un chat externo que no esté conectado a leerlo. Si el entorno no carga automáticamente estas reglas, usa el prompt de `POROTA_TRADING_CHAT_START_HERE.md` y verifica manualmente la lectura antes de continuar.

## Ingesta directa y seguimiento de progreso

Para una ingesta histórica total/masiva, el runner se lanza directamente en el droplet; Actions queda para CI de código y no se usa como lanzador, reanudador, orquestador ni túnel. Antes de iniciar cualquier tarea larga se requiere el estado runtime de sólo lectura definido en `/POROTA_TRADING_JOB_PROGRESS_CONTRACT.md`, consultable con `sudo porota-job status <run_id>`. Si la terminal directa o el estado consultable no están disponibles, no se inicia el trabajo ni se sustituye por Actions. El estado debe indicar etapa, progreso con numerador/denominador reales, último heartbeat UTC (máximo 60 s), resultados, logs, y safety. El porcentaje se omite si el total es desconocido. La especificación no equivale a implementación; se verifica el comando en el droplet antes de declarar el gate cumplido. Ninguna regla de observabilidad autoriza a repetir las corridas PPI cerradas.
