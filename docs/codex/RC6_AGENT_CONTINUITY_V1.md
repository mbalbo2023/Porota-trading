# RC6 — contrato de continuidad del agente

Este procedimiento sirve para recuperar contexto desde GitHub y el Droplet.
No certifica el SHA instalado ni la rueda de hoy por sí mismo.
La rama main no es evidencia de lo instalado.

## Relectura obligatoria

1. Leer `AGENTS.md` y `ops/policy/porota-policy.yaml` del producto actual.
2. Resolver la rama y el SHA productivos desde GitHub; `main` aloja documentos
   y no es evidencia de lo instalado. Consultar PRs/issues y los ownership
   exclusivos `WRITE_OWNER` (#471/#473) y `DEPLOY_OWNER` (#624).
3. Leer la orden completa de la tarea, los handoffs con su fecha y los
   cambios posteriores. Una memoria, captura o respuesta previa no demuestra
   un HEAD ni una mutación de runtime.
4. Comparar código, test del SHA, artifact/digest, Deploy V2 y evidencia directa
   del Droplet. `VALIDATED_RUNTIME` no equivale a `GO` integral ni a edge OOS.
5. Delimitar scope y paths antes de escribir; usar rama aislada y PR. Si hay
   solapamiento, permanecer en `READ_ONLY` y resolver el ownership en GitHub.
6. Publicar estado con procedencia y timestamp: `PROPUESTO`, `EN_GITHUB`,
   `ARTEFACTO_VALIDADO`, `DESPLEGADO`, `VALIDADO_RUNTIME`, `NO_VERIFICADO` o
   `BLOQUEADO`. No cerrar un issue por antigüedad o por una prueba sintética.

## Invariantes de ejecución

Sólo PAPER/SHADOW; `PRODUCTION_PAPER`, fills simulados, `real_orders_sent=0`,
ninguna ruta de orden real. PPI Watch intocable. Backups deshabilitados por
política actual y FIX-FORWARD sin rollback. Toda excepción requiere la orden
explícita del operador y un recibo verificable; no inferirla de un README viejo.
No exigir comandos manuales al usuario. La prohibición actual de subagentes,
estrés del host y tareas programadas prevalece sobre sugerencias genéricas.

## Handoff por hito

Registrar workstream, SHA base/head, diff y paths, owner y vencimiento,
Predeploy/Deploy run, digest del artifact, producto instalado y última
observación runtime con reloj y procedencia. Describir bloqueos concretos y
el siguiente discriminante, sin presentarlos como GREEN. Liberar ownership
pareado al terminar o bloquear la misión.
