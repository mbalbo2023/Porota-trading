# POROTA TRADING RC6 — CHECKPOINT CP2 SOURCE WIRING

Fecha: 2026-09-11
Branch: `fix/rc6-w10-sector-map-binding-20260910`
Head al cerrar CP2 source wiring: `47f61d5723201afd5b76dc7e4cbf9394ecafdbcb`
Runtime productivo PAPER: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Runtime mutado: NO
Seguridad: `PRODUCTION_PAPER`, órdenes reales observadas 0, generic news feed OFF intencionalmente.

## Problema corregido en fuente

La pantalla `/validacion` mostraba `Todavía no hay jornadas registradas en el ledger RC6` porque la sección histórica dependía exclusivamente del ledger append-only de campaña M0–M11. Ese ledger no es el historial operacional del motor PAPER y no debe rellenarse artificialmente.

## Solución CP2 preparada

Se cableó `rc6_validation_operational_daily.py` en `en_validation_project_dashboard_rc6.py` como una sección separada **Actividad PAPER por jornada**.

La fuente operacional:
- abre `observer_v17.db` read-only (`mode=ro`, `PRAGMA query_only`);
- exige `PRODUCTION_PAPER` y `real_orders_sent=0`;
- agrupa evidencia real por `America/Argentina/Buenos_Aires`;
- usa `paper_events`, posiciones, decisiones y fills;
- informa aperturas, cierres, decisiones, fills, P&L realizado y tipos de evento;
- no escribe DB;
- no modifica ni auto-promueve M0–M11.

La vista ahora separa explícitamente:
1. **Actividad PAPER por jornada** = evidencia operacional real.
2. **Ledger de campaña / auditoría por día** = observaciones append-only M0–M11.

Si el ledger de campaña está vacío, el texto deja claro que eso **no significa ausencia de actividad PAPER**.

## Cabeceras

La nueva tabla operacional usa `<thead>` real y `classic-responsive-table`. La corrección global sticky/visibilidad de cabeceras en todas las tablas queda en el siguiente paso UX compartido para evitar soluciones parciales por página.

## Tests agregados

`tests/test_rc6_validation_dashboard_operational_wiring.py` verifica:
- presencia de `<thead>` y columna `Fecha AR`;
- render de día/P&L real devuelto por la fuente read-only;
- separación de actividad operacional y hitos M0–M11;
- mensaje correcto para ledger de campaña vacío;
- fail-visible sin inventar jornadas si la fuente operacional no puede leerse.

## GitHub Actions

Los tests están preparados pero todavía no pudieron ejecutarse porque GitHub Actions continúa fallando antes del primer step. El runner probe independiente ya demostró el mismo patrón en ubuntu-22.04 y ubuntu-24.04 (`steps=null`).

No se declara CP2 runtime GREEN hasta ejecutar tests y deploy/postflight.

`CP2_SOURCE=READY`
`CP2_TEST=BLOCKED_BY_ACTIONS_RUNNER`
`CP2_RUNTIME=NOT_DEPLOYED`
`RUNTIME_MUTATED=NO`
`NEXT=CP5_RISK_GDELT_SOURCE_WIRING + GLOBAL_TABLE_HEADER_FIX -> REPROBE_ACTIONS`
