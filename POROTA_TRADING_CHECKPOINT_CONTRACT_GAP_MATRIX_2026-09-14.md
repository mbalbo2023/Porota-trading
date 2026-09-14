# POROTA TRADING — matriz de brechas contractuales — 2026-09-14

Estado: `HOLD_FAIL_CLOSED`.

## Evidencia base

La recaptura PPI Web autenticó correctamente, pero devolvió sólo endpoints `SCHEMA_ONLY`; no hubo InstrumentosOperables, DatosTecnicos ni CaucionesOperables importables. No se infieren campos.

## Campos PPI obligatorios

| Familia | Campos operativos que faltan validar explícitamente en PPI | Estado |
|---|---|---|
| Cauciones | especie/tenor vigente, tasa o precio, mínimo/máximo, step, liquidación, garantía/aforo y disponibilidad | HOLD |
| Bonos, Letras, ON (incluido AL30) | especie habilitada, mercado/moneda, liquidación, price tick, nominal/mínimo/step, horario y disponibilidad | HOLD |
| Acciones, CEDEAR, ETF | habilitación, mercado, liquidación, tick, lote/step, horario y disponibilidad | HOLD |
| Opciones y Futuros | contrato listado y operable, strike/vencimiento, liquidación, multiplicador, margen, tick y step | HOLD |
| FCI y FCI exterior | disponibilidad de suscripción/rescate, mínimos, cut-off, moneda y liquidación | HOLD |

## Fuentes

- PPI API/Web: única fuente aceptable para operabilidad, disponibilidad y parámetros activos.
- Mercado, CNV, emisor o administradora: sólo datos estables/regulatorios y validación cruzada.
- Ninguna fuente externa sustituye condiciones operativas vigentes de PPI.

## Requisitos antes de una nueva prueba

1. Lock global único para cualquier actividad browser/PPI.
2. Captura de familia realmente filtrada; para AL30 además se requiere filtro de instrumento, no disponible en el harness actual.
3. Run-id/timestamp nuevo y validación de frescura.
4. Asserts fail-closed de PRODUCTION_PAPER, SIMULATED, real_orders_sent=0, DB_IMPORT_EXECUTED=NO y métodos GET/HEAD/OPTIONS.
5. Telegram con resumen redactado, no log crudo.
