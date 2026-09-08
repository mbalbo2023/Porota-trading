# POROTA TRADING RC6 — ADDENDUM CANÓNICO — DISK / EMPIRICAL EVIDENCE ARCHITECTURE A→E

Fecha: 2026-09-08
Rama: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`
Estado: CORRECCIÓN CANÓNICA DE BACKLOG

## Regla de continuidad

Este addendum debe leerse junto con:

- `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-08_CONTINUIDAD.md`
- `POROTA_TRADING_CHECKPOINT_PPI_DOM_API_OPERABILITY_ALERTING_2026-09-08.md`
- `POROTA_TRADING_CHECKPOINT_PPI_DOM_FULL_SWEEP_2026-09-08.md`
- `POROTA_TRADING_CHECKPOINT_DISK_EVIDENCE_ARCHITECTURE_PHASES_A_E_2026-09-08.md`

## Corrección

Donde el checkpoint canónico actual resume el frente de almacenamiento/evidence como:

- `storage containment Phase A`, o
- `Evidence Architecture Phase A`, o
- cleanup físico final,

NO debe interpretarse que el plan termina allí.

El programa completo y canónico es:

1. **FASE A — Contención + nueva arquitectura de escritura**
   - RAW Evidence Store separado;
   - ingest manifests/provenance;
   - content-addressing;
   - History Ingestion Coordinator;
   - bootstrap/incremental/reconciliation;
   - cero borrado legacy.

2. **FASE B — Migración SHADOW / reconciliación del legado**
   - legacy read-only;
   - manifests/hashes/raw objects;
   - reconciliación con History Store y demás evidencia;
   - explicación de PARTIAL;
   - equivalencia/reconstrucción demostrada.

3. **FASE C — Retirada de redundancia física**
   - única fase donde empieza recuperación física de espacio;
   - sólo redundancia demostrada;
   - preservar manifests, versiones, conflictos, PARTIAL y errores;
   - `EVIDENCE_LOST=0`.

4. **FASE D — Learning Evidence completa**
   - `decision -> evidence -> outcome`;
   - enlazar históricos, contratos, snapshots, strategy/config/features, decisión y outcomes 5m/15m/1h/close/T+1, MAE/MFE/P&L/fees/slippage.

5. **FASE E — Retención / COLD storage**
   - actualizar `STORAGE_LIFECYCLE_HF6_V2.md`;
   - no borrar raw único automáticamente a 180 días;
   - KEEP para evidencia/versiones/revisiones/conflictos/PARTIAL relevante/incidentes/manifests/outcomes;
   - cold storage comprimido, eventualmente fuera del filesystem operacional, preservando hash/retrieval/integrity/provenance.

## Estado

Las FASES A→E permanecen PENDIENTES hasta evidencia posterior de ejecución y cierre.

Este addendum NO marca ninguna fase GREEN por sí mismo y NO autoriza deploy ni cleanup.

Antes de iniciar A debe hacerse Gate 0 sobre el estado ACTUAL de runtime/GitHub Actions y derivar una nueva rama desde el SHA live REAL.

## Invariantes

- no borrar `historical_raw_archive` antes de la equivalencia requerida;
- no `docker system prune`;
- no `VACUUM` prematuro;
- no migraciones destructivas;
- preservar evidencia única y versiones;
- no mezclar este frente con trading/scalping;
- `real_orders_sent=0` absoluto durante RC6.

## Fuente detallada

Ver:

`POROTA_TRADING_CHECKPOINT_DISK_EVIDENCE_ARCHITECTURE_PHASES_A_E_2026-09-08.md`

que consolida el checkpoint histórico `CHECKPOINT_RC6_EMPIRICAL_EVIDENCE_ARCHITECTURE_2026-09-07.md` y el plan ampliado de arquitectura de disco/evidencia aportado el 2026-09-08.
