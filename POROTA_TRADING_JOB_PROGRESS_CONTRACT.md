# Contrato de estado y progreso para trabajos POROTA

**Versión:** 2026-09-14/1  
**Estado:** requisito de operación; la interfaz runtime en el droplet aún no está implementada/verificada.

Este contrato hace que cada ejecución larga pueda consultarse sin depender de una conversación o de los logs de GitHub. Es obligatorio para ingestas, backfills y otras tareas de droplet que duren más de un paso operativo.

## Canal de ejecución

- La ingesta histórica total o masiva se ejecuta directamente en el droplet por su runner/servicio operativo versionado. GitHub Actions puede validar código, pero no debe lanzar, reanudar ni controlar esa ingesta.
- Antes de iniciar un trabajo largo, el droplet debe ofrecer una consulta de estado de sólo lectura por `run_id`. El comando estándar que se debe implementar y probar es `sudo porota-job status <run_id>`.
- Si la sesión no tiene una superficie directa y autorizada para el droplet, no sustituirla por un workflow de Actions. Detener el inicio de la ingesta y registrar el acceso directo faltante.
- Esto no autoriza a repetir las ingestas PPI ya cerradas, a iniciar writers, ni a importar evidencia contractual.

## Registro mínimo por ejecución

La consulta debe mostrar:

- `run_id`, nombre de la tarea, versión/commit del runner, estado y etapa actual en lenguaje claro;
- hora UTC de inicio, último heartbeat y fin, además de si el heartbeat está vigente o atrasado;
- progreso reproducible (`completed`, `total`, unidad y contadores terminales por resultado); mostrar porcentaje sólo cuando exista un denominador real;
- último evento seguro, código de error resumido y ubicación de logs/resultados;
- invariantes de seguridad relevantes: modo, órdenes reales enviadas, importación DB y reinicio de servicios.

Los estados de ejecución son `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED` e `INTERRUPTED`. `STALE` se calcula desde un heartbeat vencido; no se presenta como éxito. El runner debe actualizar el estado en cada cambio de etapa y emitir heartbeat como máximo cada 60 segundos mientras esté trabajando. Para tareas por lotes, debe actualizar los contadores al terminar cada lote.

## Privacidad e integridad

El estado es una vista de progreso, no una copia de evidencia: no incluir credenciales, headers, cuerpos HTTP, valores financieros, listas de tickers ni identificadores de cuentas. Escribir actualizaciones atómicas y mantener un único writer por `run_id`. La consulta no debe modificar la base de datos ni el trabajo monitoreado.

## Criterio de habilitación

No iniciar una ingesta larga hasta que el runner conectado al droplet tenga la escritura del estado, `porota-job status <run_id>` funcione en runtime, los heartbeats/progreso se hayan probado y el checkpoint documente la consulta exacta y el `run_id`. La especificación sola no significa que la interfaz esté instalada.

## Estado verificado al 2026-09-14

La sonda limitada `34801210987`/`103844101204` pudo consultarse por estado y etapas de GitHub Actions. No expuso un estado/heartbeat/progreso consultable desde el droplet. Esta interfaz queda **NOT_IMPLEMENTED_OR_RUNTIME_NOT_VERIFIED**; por eso no se permite lanzar el próximo trabajo largo hasta implementarla y verificarla.
