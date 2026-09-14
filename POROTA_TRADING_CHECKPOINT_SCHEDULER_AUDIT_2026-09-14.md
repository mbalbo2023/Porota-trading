# POROTA TRADING — CHECKPOINT SCHEDULER AUDIT — 2026-09-14

## Estado

`IN_PROGRESS`

## Alcance autorizado

Auditar exhaustivamente los trabajos programados RC6 en modo sólo lectura: timers, servicios, scripts, dependencias, logs, locks y posibles interferencias con captura contractual, históricos, runtime PAPER y Telegram.

## Entregables requeridos

- Inventario real de todos los jobs.
- Propósito, fuente, cadencia, escritura, lock y dependencias por job.
- Clasificación `KEEP`, `DISABLE`, `MODIFY`, `CREATE` o `HOLD`.
- Riesgo de interferencia con contratos/PPI y con rueda.
- Estado comprobable de scalping PAPER para el preopen.

## Invariantes

- `PRODUCTION_PAPER`, `SIMULATED`, `real_orders_sent=0`.
- Sin órdenes, POSTs, DB writes, reinicios, habilitación de timers ni segundo writer.
- No reactivar masivamente los timers pausados.
- Fail-closed ante evidencia incompleta.

## Estado previo

Los timers Porota detectados permanecen deshabilitados; el servicio full-family histórico fue deshabilitado para impedir arranque tras reboot. Captura contractual: autenticación recuperada, pero último resultado `RED_CAPTURE_NOT_IMPORTABLE`.
