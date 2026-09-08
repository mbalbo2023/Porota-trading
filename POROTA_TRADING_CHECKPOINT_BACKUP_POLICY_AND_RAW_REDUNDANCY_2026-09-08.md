# POROTA TRADING RC6 — CHECKPOINT BACKUP POLICY + RAW REDUNDANCY CLARIFICATION

Fecha: 2026-09-08
Rama canónica: checkpoint/rc6-ppi-dom-api-operability-alerting-20260908
Estado: PENDIENTE / POLÍTICA DEFINIDA, NO EJECUTADA

## 1. Política de backups propuesta

Objetivo: reducir consumo de disco sin perder una ventana mínima de recuperación.

Política preferida:
- backup diario por cada base de datos;
- mantener 2 generaciones rotativas: `daily-current` + `daily-previous`;
- no acumular backups diarios indefinidamente en el Droplet;
- ejecutar auditoría post-Wave4 antes de materializar la política;
- medir tamaño exacto de `.db`, `-wal`, `-shm`, backups, RAW Evidence Store y filesystem antes de activar limpieza/rotación.

Motivo de mantener 2 generaciones y no 1 sola sobrescrita:
- si el backup nuevo captura una corrupción o inconsistencia tardíamente detectada, `daily-previous` conserva el último punto sano.

## 2. Clarificación canónica sobre `historical_raw_archive`

Sí existe redundancia física importante dentro de `historical_raw_archive`:
- múltiples intentos pueden almacenar payloads PPI muy solapados o idénticos;
- `row_key` incluye `attempted_at`, por lo que intentos distintos generan filas distintas aunque el contenido raw sea igual o fuertemente solapado;
- auditorías previas mostraron que este patrón explica gran parte del crecimiento de `observer_v17.db`.

Pero la conclusión correcta NO es `borrar la tabla ahora`.

`historical_raw_archive` contiene además evidencia empírica y provenance por intento. Por lo tanto:
- FASE A: detener nuevas escrituras raw ineficientes y mover nuevos payloads a RAW Evidence Store content-addressed; legacy queda intacto.
- FASE B: recorrer legacy en SHADOW/read-only, calcular hashes, crear manifests, reconciliar con History Store y demostrar qué objetos son duplicados exactos y cuáles representan evidencia única/revisiones/PARTIAL/conflictos.
- FASE C: recién después de equivalencia demostrada y `EVIDENCE_LOST=0`, retirar redundancia física comprobada del legacy y recuperar espacio.

La tabla puede terminar reduciéndose de forma muy significativa e incluso quedar sin payloads redundantes legacy, pero no debe eliminarse ni vaciarse a ciegas antes de B/C.

## 3. Regla de decisión

- Duplicado físico demostrado + provenance preservada + History Store equivalente + raw object externo íntegro => candidato a retiro en FASE C.
- Evidencia única, revisión, conflicto, PARTIAL relevante, incidente o dato no reconstruible => KEEP.

## 4. Secuencia acordada

1. Cerrar Wave4 / Phase A GREEN.
2. Auditoría exacta post-Wave4 de todas las DB, WAL/SHM, backups y Evidence Store.
3. Materializar política de backups 2-generation rotating.
4. Ejecutar Phase B shadow reconciliation.
5. Ejecutar Phase C sólo con equivalencia demostrada y `EVIDENCE_LOST=0`.
6. Recién ahí compactar/reclamar espacio del legacy bajo ventana segura.

## 5. Invariantes

- PRODUCTION_PAPER.
- `real_orders_sent=0`.
- no borrar `historical_raw_archive` antes de Phase C.
- no `VACUUM`, no truncar WAL/SHM, no `docker system prune` sin gates explícitos.
- preservar evidencia única, versiones históricas y provenance.
