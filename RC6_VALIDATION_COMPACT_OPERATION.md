# RC6 — Operación de Validación liviana

## Regla principal

La página `/validacion` lee únicamente el snapshot compacto
`validation_milestones_rc6.json`. Nunca abre ni verifica el ledger JSONL
durante una solicitud HTTP.

## Proyección diaria

La tarea `validation_projection` se ejecuta una vez por día y también una
vez al finalizar un deploy RC6. Su recorrido es acotado:

1. Evalúa M0 con `PRAGMA quick_check` de SQLite en modo solo lectura.
2. Evalúa M1 con los invariantes `PRODUCTION_PAPER`, `SIMULATED` y rutas
   de órdenes bloqueadas.
3. Mantiene evidencia de otros hitos sólo si el snapshot anterior fue marcado
   `FULLY_VERIFIED`.
4. Mantiene M11 en `RED`: real-money sigue bloqueado por diseño.

La proyección diaria no lee el ledger completo, no escribe la base de trading,
no cambia parámetros del motor y no puede habilitar órdenes reales.

## Verificación integral

La verificación de cadena JSONL es una tarea explícita de operador mediante
`POROTA_VALIDATION_FULL_VERIFY=1`. No debe ejecutarse como parte de:

- carga del dashboard;
- deploy;
- worker de frecuencia corta;
- ejecución de la rueda.

Si la verificación integral falla, la proyección conserva sólo evidencia que
ya estaba verificada y muestra `VERIFY_ERROR`; nunca promociona un hito.

## Semántica de estado

- `FULLY_VERIFIED`: cadena completa verificada; su evidencia puede quedar
  retenida en días posteriores.
- `DAILY_COMPACT`: cálculo diario corto; conserva únicamente evidencia
  previamente verificada.
- `VERIFY_ERROR`: la verificación explícita falló; no habilita ningún verde.
- `GREEN`: evidencia actual o previamente verificada suficiente.
- `GRAY`: todavía no hay evidencia verificable suficiente.
- `RED`: objetivo no cumplido o bloqueado. M11 permanece bloqueado.

## Seguridad

Todo el circuito es read-only respecto de las decisiones de trading. La
política sigue siendo:

```
POROTA_MODE=PRODUCTION_PAPER
EXECUTION=SIMULATED
REAL_ORDER_CAPABILITY=BLOCKED
real_orders_sent=0
```
