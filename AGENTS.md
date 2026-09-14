# Política obligatoria de repositorio y continuidad entre chats

Versión: `2026-09-14/3`

Aplica a toda persona, chat, agente o automatización que inspeccione o modifique este repositorio. Antes de trabajar, cada agente compatible debe cargar este archivo automáticamente; si no lo hace, debe leerlo manualmente como primer paso. También debe leer cualquier `AGENTS.md` más específico en el subdirectorio que vaya a tocar.

## Puerta obligatoria de continuidad

Antes de dar el primer estatus, proponer cambios o ejecutar una acción, lee íntegramente este archivo, `/POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md`, `/POROTA_TRADING_CHAT_START_HERE.md` y el checkpoint canónico completo `/POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md`. Si el usuario aporta o nombra un checkpoint con ID posterior, léelo también como delta y verifica sus runs/evidencias en GitHub. Después comprueba los cambios en GitHub posteriores al checkpoint. No reconstruyas desde memoria ni desde un extracto. Si no puedes leerlo, no afirmes continuidad completa: limita el trabajo a lo verificable y declara qué falta.

En cada respuesta separa código, CI, runtime, evidencia/importación y READY end-to-end. Preserva errores, parciales, decisiones, restricciones, owners y tareas abiertas. Marca lo desconocido como `NOT_VERIFIED`; no borres evidencia única ni llames READY a una métrica parcial.

## Preflight obligatorio

1. Lee esta política completa y el checkpoint canónico completo indicado arriba. Verifica en él el tag y SHA exactos del baseline operativo RC6. No deduzcas el baseline desde `main`, una rama de integración ni una memoria de chat.
2. Comprueba el estado de la rama objetivo, su SHA base, PRs abiertos y si otro chat/persona es responsable del mismo alcance. Anota el chat/alcance, rama, base y rutas previstas en el PR o checkpoint.
3. Si la rama, ruta, workflow o recurso tiene dueño activo, o no puedes determinarlo, no escribas ni ejecutes nada allí. Mantén el trabajo en solo lectura y coordina con el responsable.
4. La auditoría de solo lectura puede hacerse en paralelo. Las modificaciones paralelas deben usar ramas independientes y alcances/rutas sin conflicto. Nunca compartas una rama escribible entre chats.

## Aislamiento de la ingesta PPI

Cuando el workstream `pipeline ppi watch` tenga una ingesta activa, su responsable mantiene propiedad exclusiva. Otros chats no deben inspeccionar ni cambiar su host, DB, locks, timers, writer o configuración, y no deben usar su rama como base. Si el checkpoint no demuestra que la ejecución terminó, presume que sigue activa y no la inspecciones.

El checkpoint PPI vigente registra cerrados e inactivos los pases históricos API y Web que identifica. No los reinicies ni repitas masivamente. Un checkpoint explícito del usuario puede autorizar una consulta GitHub de solo lectura a runs, código o evidencias nombrados para un alcance distinto; esa autorización no permite acceder o cambiar el host o la DB, iniciar/reiniciar servicios, importar evidencia ni habilitar POST. Si hay riesgo de afectar a un owner o writer activo, detente y coordina.

## Ingesta directa en Droplet y progreso observable

- Toda ingesta histórica total/masiva corre directamente en el droplet mediante el runner/servicio operativo versionado. GitHub Actions no se usa para lanzarla, reanudarla, controlarla ni hacer de túnel/orquestador; CI de código puede seguir corriendo por Actions.
- Antes de iniciar cualquier trabajo largo, debe existir y probarse en runtime el estado de sólo lectura descrito en `/POROTA_TRADING_JOB_PROGRESS_CONTRACT.md`. La consulta estándar será `sudo porota-job status <run_id>`; el checkpoint debe registrar el comando y run_id concretos. No iniciar si el estado/progreso no puede consultarse directamente en el droplet.
- El estado muestra qué se ejecuta ahora, progreso con numerador/denominador reales, heartbeat UTC (máximo 60 s entre heartbeats), contadores de resultados, logs/resultados y gates de seguridad. Sin denominador válido, no se inventa un porcentaje.
- Si el entorno no expone una terminal directa y autorizada al droplet, detener el lanzamiento; no reemplazarla con Actions. Esto no autoriza a repetir ninguna ingesta PPI cerrada ni a importar evidencia.
- La especificación no prueba que el comando esté instalado: hasta que la implementación y el runtime se validen, registrar `NOT_IMPLEMENTED_OR_RUNTIME_NOT_VERIFIED` y no ejecutar ingestas largas.

## Ramas, PRs y operación

- No hagas push directo a `main`, a la referencia RC6 ni a una rama de otro chat. No hagas force-push ni borres ramas sin autorización explícita y verificación de dependencias.
- Crea una rama única por tarea, por ejemplo `docs/<alcance>-YYYYMMDD-<chat>`, `chore/<alcance>-YYYYMMDD-<chat>` o `fix/<alcance>-YYYYMMDD-<chat>`.
- Un PR debe resolver un solo objetivo y declarar base SHA, rutas, dueño, impacto, checkpoint y validaciones. Usa la plantilla del repositorio y deja constancia explícita de que leíste esta política y el checkpoint vigente.
- Un solo chat integrador coordina conflictos, revisa el diff final y es responsable de fusiones, releases y despliegues. No fusiones, despliegues ni ejecutes workflows manuales de producción sin autorización explícita del usuario.
- Antes de integrar, comprueba PRs/rutas concurrentes, CI aplicable y posibles efectos operativos. Si faltan permisos para verificar protecciones o checks, decláralo y no los des por configurados.
- Después de cada avance importante, actualiza el checkpoint con la rama, SHA, PR, archivos, checks y estado de integración. No sobrescribas el checkpoint que otro chat esté editando.

## Actualización obligatoria del checkpoint

Todo cambio material —decisión, commit, run, prueba, despliegue autorizado, hallazgo, bloqueo o estado— debe actualizar el checkpoint en el mismo flujo, aunque sea un delta breve. Conserva todo lo no resuelto y la evidencia única. Si no hay datos suficientes, registra `NOT_VERIFIED` y el paso exacto para resolverlo. Después del commit, vuelve a leer el archivo desde GitHub y verifica contenido y SHA; no declares un checkpoint actualizado antes de hacerlo. Antes de un cambio de chat o límite de contexto, deja un handoff autocontenido para el siguiente chat.

## Regla de parada

Si no puedes leer la política o el checkpoint, si hay discrepancia sobre RC6, si hay un dueño/alcance en conflicto o si no puedes demostrar que la acción queda aislada, limita el trabajo a análisis de solo lectura y deja constancia de qué falta.
