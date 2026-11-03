# POROTA HF6 v2 — retiro del pre-open legacy

Estado: WIP. **No ejecutado en el host.** Requiere aprobación explícita del deploy.

## Decisión

El `porota-preopen.timer/service` legado deja de ser un gate operativo. El motivo es que evalúa supuestos históricos HF2/SANDBOX/SHADOW que ya no describen el runtime HF6 `PRODUCTION_PAPER`, por lo que puede generar `NO_GO` falsos.

No se reemplaza por otro chequeo monolítico de una única hora de apertura.

## Reemplazo funcional

HF6 v2 usa readiness continuo y sesiones por familia/mercado:

- modo `PRODUCTION_PAPER` y órdenes reales bloqueadas;
- `real_orders_sent=0`;
- DB `quick_check=ok`;
- observer/readiness vivo;
- PPI fresco cuando corresponda;
- sesión oficial por familia/mercado/fecha efectiva;
- contrato, costos, sizing e histórico con estados explícitos;
- una familia HOLD no bloquea familias independientes que estén READY.

Esto evita asumir `MARKET_OPEN_HOUR=11` para todo el mercado.

## Acción de deploy propuesta

El deploy debe:

1. capturar `systemctl cat/status` y journal actual de `porota-preopen.timer/service`;
2. copiar cualquier unidad/script host a un backup versionado por timestamp;
3. `disable --now porota-preopen.timer`;
4. detener el service sólo si estuviera activo y ya no ejecutando una acción crítica;
5. no borrar de inmediato las unidades ni el journal;
6. `daemon-reload`;
7. verificar que el timer no esté scheduled;
8. verificar que observer/dashboard sigan sanos y `real_orders_sent=0`;
9. conservar rollback que reinstala/rehabilita exactamente la unidad respaldada.

## Qué NO se hace

- no se modifica la etiqueta inmutable HF6;
- no se cambia el modo a real;
- no se reinicia el observer sólo para retirar el timer;
- no se transforma un HOLD contractual en READY;
- no se inventan horarios;
- no se elimina evidencia histórica del pre-open.

## Condición de aceptación

El retiro es VERDE sólo si:

- timer legacy inactivo/deshabilitado;
- readiness vivo disponible;
- sesiones por familia cargadas con fuente/effective date;
- observer/dashboard sin restart inesperado;
- DB íntegra;
- `real_orders_sent=0`.
