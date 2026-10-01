# BACKLOG — BITÁCORA POST-SESIÓN RC6

## Objetivo

Crear una bitácora general, inmutable y auditable de cada rueda PAPER una vez finalizada la jornada y después de que hayan corrido los procesos postclose necesarios.

No forma parte del deploy inmediato salvo la documentación del backlog. No debe modificar estrategia automáticamente.

## Momento

Ejecutar sólo en día hábil operativo y después del cierre, cuando:
- snapshot postclose esté VERIFIED;
- exits EOD hayan alcanzado estado terminal o estén explicitamente pendientes con razón;
- reconciliación de ledger/riesgo haya finalizado;
- reportes diarios requeridos estén disponibles o su ausencia quede declarada;
- jobs de históricos/candles que correspondan hayan terminado o queden con estado explícito.

Evitar el clúster 17:15–17:25 existente; definir hora/cadena final después de reordenar timers.

## Contenido mínimo

1. Identidad del release/runtime: candidate SHA, image ID, artifact/digest, modo, safety invariants.
2. Estado de infraestructura: uptime, CPU/load, memoria/swap, disco/inodos, Docker, DB/WAL.
3. Timers y servicios: expected vs actual, failures, retries y duración.
4. Fuentes: PPI, IOL, BYMA/A3/GDELT, freshness, LKG/fallback, errores scoped.
5. Universo/readiness por familia y cambios del día.
6. Ingesta/históricos/candles: cobertura, filas, freshness, rejects, anomalías.
7. Trading PAPER: decisiones, gates, posiciones, fills, exits, PnL separado por moneda.
8. Scalping: universo, contratos intraday, HOLD/reject reasons, fills PAPER.
9. Cauciones: ofertas, planner/allocator/sweep, fee provenance, HOLD/OPEN/SETTLED.
10. Riesgo: equity por moneda, daily PnL, budgets, latched state.
11. Dashboard: rutas críticas, latencias, payload, coherencia contra fuentes de verdad.
12. Validación M0–M11 y cambios de evidencia.
13. Reportes/Telegram: generación, hash/ubicación y ACK.
14. Incidentes del día con secuencia ERROR → RCA → FIX → GUARD → TEST → EVIDENCIA.
15. Pendientes priorizados P0/P1/P2 y owner.
16. Lecciones del día.
17. Sign-off: `PAPER_ONLY`, `real_orders_sent=0`, rutas reales `NOT_CALLED`.

## Reglas

- Read-only sobre runtime/DB.
- No recalcular o reescribir historia operativa.
- No mezclar monedas.
- No inferir freshness sin timestamp.
- No considerar workflow GREEN como validación runtime por sí solo.
- Salida JSON estructurada + Markdown humano.
- Hash de cada salida y retención definida.
- Debe poder responder una auditoría externa sin depender del chat.
- Un estado faltante se expresa como `NO_VERIFICADO`, nunca se completa por inferencia.

## Criterio de aceptación futuro

La bitácora se considerará implementada sólo cuando:
- exista schema versionado;
- tenga test de invariantes;
- corra en Actions/systemd según el plano de control decidido;
- se pruebe en al menos dos jornadas;
- sus cifras se reconcilien contra DB/logs/APIs;
- no introduzca carga que afecte la sesión de mercado.
