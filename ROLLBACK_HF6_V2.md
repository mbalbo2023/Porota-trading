# POROTA TRADING — HF6-v2 Rollback Plan

## Runtime conocido que debe preservarse

- Versión activa previa al candidato: `17.0.0-rc3-hf6`.
- Imagen activa esperada: `porota-trading-bot:17.0.0-rc3-hf6`.
- Imagen aceptada previamente: `sha256:4de66950561e73410d16e3cee3c2fa592ec6027dd076faaf7286af950f094d07`.
- Observer: `porota_production_observer`.
- Dashboard: `porota_production_dashboard`.
- Modo: `PRODUCTION_PAPER`.
- Ejecución: `SIMULATED`.
- Observer esperado: `read_only=true`.
- Invariante: `real_orders_sent=0`.

El deploy NO debe eliminar esta imagen hasta terminar smoke/observación y autorización de limpieza.

## Pre-deploy obligatorio

Antes de reemplazar cualquier contenedor se deben capturar localmente, sin imprimir secretos:

- image ID exacto de observer/dashboard;
- restart count y read-only flag;
- mounts, redes, puertos y restart policy;
- nombres/estado de timers Porota;
- `PRAGMA quick_check` de la DB;
- modo/session/process/ppi_auth/real_orders_sent;
- hashes del árbol candidato y manifest de imagen.

No se vuelcan variables de entorno ni credenciales al reporte.

## Rollback funcional

Si el candidato falla health, DB, invariantes de seguridad, dashboard, scheduler, riesgo o smoke PAPER:

1. detener únicamente los contenedores candidatos;
2. recrear observer/dashboard con la imagen HF6 aceptada y la misma configuración/mounts capturados antes del deploy;
3. NO restaurar DB por defecto: el esquema nuevo debe ser aditivo/reversible y las tablas v2 paralelas no reemplazan las legacy;
4. restaurar DB sólo si existe evidencia de corrupción o migración destructiva, lo que debe ser condición RED antes de continuar;
5. verificar `/health`, `PRAGMA quick_check`, modo `PRODUCTION_PAPER`, observer read-only, restart count esperado y `real_orders_sent=0`;
6. reactivar únicamente timers que existían antes del deploy y que fueron suspendidos por el procedimiento.

## Pre-open legacy

`porota-preopen.timer` no se borra sin evidencia. El retiro propuesto es reversible:

- guardar `systemctl cat`, estado y journal relevante;
- disable/stop del timer legacy;
- conservar backup de la unidad/script host;
- verificar que el observer no cambió;
- el nuevo readiness continuo/sesiones por familia no debe depender del viejo pre-open.

Rollback del pre-open: restaurar la unidad desde backup, `daemon-reload`, enable/start timer sólo si fuera necesario volver al baseline HF6.

## Timers nuevos

Los timers nuevos (logs, scheduler snapshot, históricos post-cierre, Contract Evidence/CEM cuando correspondan) se instalan como unidades independientes. Rollback: disable/stop y remover únicamente esas unidades; luego `daemon-reload`. No tocar timers no pertenecientes al candidato.

## Storage cleanup

La limpieza ocurre sólo DESPUÉS de candidato verde y smoke estable. Nunca usar `docker system prune -a`.

Conservar siempre:

- imagen HF6 rollback;
- imagen candidata activa mientras se evalúa;
- DB/WAL/SHM activos;
- históricos canónicos/versionados;
- aprendizaje;
- Contract Evidence;
- secretos/credenciales cifradas;
- backups canónicos;
- evidencia del release.

Se pueden considerar, tras inventario y autorización, únicamente imágenes/build-cache huérfanos, bundles staging antiguos, snapshots/logs vencidos según lifecycle y worktrees temporales.

## Condiciones RED para rollback inmediato

- `real_orders_sent != 0`;
- modo distinto de `PRODUCTION_PAPER`/ejecución no simulada;
- DB quick_check distinto de `ok`;
- observer no read-only cuando deba serlo;
- imagen equivocada o no identificable;
- health persistentemente fallido;
- pérdida de mounts/DB/credenciales o corrupción de datos;
- posibilidad de routing de órdenes reales;
- regresión crítica del motor de salida/supervisión.

PnL PAPER negativo, históricos incompletos, warnings de fuente o familias HOLD no son por sí solos motivo de rollback si los invariantes de seguridad permanecen verdes.
