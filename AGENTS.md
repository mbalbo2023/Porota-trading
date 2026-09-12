# Política obligatoria para trabajo paralelo

Versión: `2026-09-12/1`

Aplica a toda persona, chat, agente o automatización que inspeccione o modifique este repositorio. Antes de trabajar, cada agente compatible debe cargar este archivo automáticamente; si no lo hace, debe leerlo manualmente como primer paso. También debe leer cualquier `AGENTS.md` más específico en el subdirectorio que vaya a tocar.

## Preflight obligatorio

1. Lee esta política completa y el checkpoint canónico vigente indicado por el responsable de la tarea. Verifica en él el tag y SHA exactos del baseline operativo RC6. No deduzcas el baseline desde `main`, una rama de integración ni una memoria de chat.
2. Comprueba el estado de la rama objetivo, su SHA base, PRs abiertos y si otro chat/persona es responsable del mismo alcance. Anota el chat/alcance, rama, base y rutas previstas en el PR o checkpoint.
3. Si la rama, ruta, workflow o recurso tiene dueño activo, o no puedes determinarlo, no escribas ni ejecutes nada allí. Mantén el trabajo en solo lectura y coordina con el responsable.
4. La auditoría de solo lectura puede hacerse en paralelo. Las modificaciones paralelas deben usar ramas independientes y alcances/rutas sin conflicto. Nunca compartas una rama escribible entre chats.

## Aislamiento de la ingesta PPI

El chat y workstream `pipeline ppi watch` tiene propiedad exclusiva de la ingesta que está corriendo. Otros chats no deben consultar, leer, editar, ejecutar, detener, reiniciar, desplegar ni cambiar su rama, runs/logs, bloqueos, timers, host, base de datos, datos, código o configuración. Tampoco deben usar esa rama como base.

Solo el responsable explícito de ese workstream puede operar esos recursos dentro de la autorización vigente. Si otro cambio puede afectarlos, detente y escálalo al coordinador antes de continuar. No intentes verificar ese trabajo entrando en su rama o ejecución.

## Ramas, PRs y operación

- No hagas push directo a `main`, a la referencia RC6 ni a una rama de otro chat. No hagas force-push ni borres ramas sin autorización explícita y verificación de dependencias.
- Crea una rama única por tarea, por ejemplo `docs/<alcance>-YYYYMMDD-<chat>`, `chore/<alcance>-YYYYMMDD-<chat>` o `fix/<alcance>-YYYYMMDD-<chat>`.
- Un PR debe resolver un solo objetivo y declarar base SHA, rutas, dueño, impacto, checkpoint y validaciones. Usa la plantilla del repositorio y deja constancia explícita de que leíste esta política y el checkpoint vigente.
- Un solo chat integrador coordina conflictos, revisa el diff final y es responsable de fusiones, releases y despliegues. No fusiones, despliegues ni ejecutes workflows manuales de producción sin autorización explícita del usuario.
- Antes de integrar, comprueba PRs/rutas concurrentes, CI aplicable y posibles efectos operativos. Si faltan permisos para verificar protecciones o checks, decláralo y no los des por configurados.
- Después de cada avance importante, actualiza el checkpoint de auditoría con la rama, SHA, PR, archivos, checks y estado de integración. No sobrescribas el checkpoint que otro chat esté editando.

## Regla de parada

Si no puedes leer la política o el checkpoint, si hay discrepancia sobre RC6, si hay un dueño/alcance en conflicto o si no puedes demostrar que la acción queda aislada, limita el trabajo a análisis de solo lectura y deja constancia de qué falta.
