# RC6 — caución PAPER pendiente y Deploy V2 (22/10/2026)

## RCA y decisión

Deploy V2 #39851933599 se detuvo en la clasificación anterior a cualquier escritura de host: `QUIESCENCE_OPEN_POSITION`. La auditoría READ_ONLY #39852269848 observó una sola caución ARS `OPEN`, iniciada 22/10 16:30 ART y con vencimiento contractual 23/10 17:00 ART, cero posiciones spot/futuros, `PRODUCTION_PAPER` y `real_orders_sent=0`. La protección de libro plano era correcta para una exposición desconocida; carecía de una ruta para preservar una colocación simulada válida hasta su vencimiento. Reetiquetar `MATURED` o inventar un cierre anticipado habría alterado caja, devengo, PnL y el linaje de la asignación. Esta corrección no escribe en la DB financiera.

## Guard de continuidad

La excepción `CLOSED_CAUCION` sólo se admite con los dos contenedores propios en marcha, observador fresco en `WAITING_MARKET/MARKET_CLOSED`, exactamente una caución `PRODUCTION_PAPER` ARS `OPEN` de este incidente, apertura 22/10 16:30 ART, vencimiento original 23/10 17:00 ART aún futuro, ninguna otra exposición y ninguna orden real. Valida contrato/economía mediante `validate_position` y la asignación ligada con `_validated`. Una fotografía de la fila completa y su recibo de asignación, con SHA-256, queda en el recibo duradero de quiescencia. Se repite antes y después de detener, antes de instalar, después de reiniciar y al persistir `verify-carryover`. Cualquier diferencia o vencimiento transcurrido falla cerrado; la fecha nunca se adelanta.

Predeploy V2 ejecuta el replay de transición y las pruebas de la suite. La regresión construye un libro PAPER real con asignación interna válida, conserva `OPEN` y caja inmovilizada, rechaza alteración del principal antes de instalar y del hash de asignación tras reiniciar. Los paths de órdenes reales y PPI Watch no cambian. Backups permanecen suspendidos; no se incorpora estrés host ni rollback.

## Evidencia de promoción pendiente

Anotar aquí el SHA/tree candidato, PR, run/artifact/digest exactos de Predeploy V2, autorización #624, merge de dos padres, Deploy V2, imagen/runtime, controles y cleanup. Hasta entonces el cambio está EN_GITHUB sólo si se publica el PR; no es `VALIDATED_RUNTIME`. La auditoría integral #713 sigue `NO_GO` por linaje anual y evidencia de capacidad OPEN, aun si esta publicación alcanza `VALIDATED_RUNTIME`.
