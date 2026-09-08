# POROTA TRADING RC6 — CHECKPOINT DISK / EMPIRICAL EVIDENCE ARCHITECTURE — PHASES A→E

Fecha: 2026-09-08
Estado: PENDIENTE CANÓNICO RC6 — NO EJECUTADO EN ESTA ACTUALIZACIÓN
Rama: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`

## 1. Corrección canónica de backlog

El frente de optimización/arquitectura de disco y evidencia empírica NO termina en `storage containment Phase A`.

El programa correcto tiene CINCO fases encadenadas:

- FASE A — Contención + nueva arquitectura de escritura.
- FASE B — Migración SHADOW / reconciliación del legado.
- FASE C — Retirada de redundancia física demostrada.
- FASE D — Learning Evidence completa (`decision -> evidence -> outcome`).
- FASE E — Retención / COLD storage.

Las cinco fases forman una única iniciativa: `POROTA EMPIRICAL EVIDENCE ARCHITECTURE v1`.

Este checkpoint corrige cualquier resumen previo de RC6 que mencione únicamente `Evidence Architecture Phase A`.

## 2. Fuentes históricas canónicas

Checkpoint previo:

- archivo: `CHECKPOINT_RC6_EMPIRICAL_EVIDENCE_ARCHITECTURE_2026-09-07.md`
- rama: `checkpoint/rc6-empirical-evidence-architecture-20260907`

Evidencia técnica previa:

- rama: `audit/rc6-historical-raw-architecture-20260907`
- `POROTA_RC6_HISTORICAL_RAW_ARCHITECTURE_AUDIT_2026-09-07.txt`
- `POROTA_RC6_HISTORICAL_RAW_INDEX_AUDIT_2026-09-07.txt`
- `POROTA_RC6_HISTORICAL_RAW_GROWTH_AUDIT_2026-09-07.txt`
- `POROTA_RC6_HISTORICAL_RAW_TRIGGER_AUDIT_2026-09-07.txt`

Documento ampliado aportado por el usuario el 2026-09-08:

- `plan mejoras arquitectura de disco.md`

La versión ampliada es la referencia para FASES A→E.

## 3. Objetivo general no negociable

El objetivo NO es reducir la historia ni borrar evidencia para ahorrar disco.

La arquitectura debe:

1. preservar toda evidencia empírica única;
2. preservar todas las versiones históricas;
3. preservar revisiones/cambios de PPI u otras fuentes;
4. preservar parciales, conflictos, errores e incidentes;
5. conservar permanentemente cada intento de ingesta;
6. preservar learning evidence y outcomes;
7. eliminar solamente redundancia FÍSICA demostrada;
8. separar almacenamiento operacional de almacenamiento histórico/evidencia;
9. detener el crecimiento incorrecto de `observer_v17.db`;
10. mantener la trazabilidad:

`qué sabía POROTA -> qué decidió -> por qué -> qué ocurrió después`.

## 4. Hallazgos históricos que motivan el plan

Evidencia auditada previamente:

- `historical_raw_archive` dentro de `observer_v17.db`: aproximadamente 1.266 GB;
- alrededor de 25.860 filas;
- filas auditadas con `origin='PPI_HISTORY'`;
- writer productivo histórico: `bf_production_paper_observer.py::_download_histories()`;
- cada intento almacenaba un `body_json` grande, normalmente con una respuesta PPI de ~365 días;
- `row_key` incorpora `attempted_at`, por lo que se generan filas nuevas incluso con fuerte solapamiento semántico;
- el mismo payload luego alimenta History Store v2;
- se observaron ritmos normales cercanos a 40 escrituras/hora y ráfagas extraordinarias de aproximadamente 1.000–1.200 escrituras/hora;
- auditoría previa indicó aproximadamente 69% de ingestas históricas clasificadas `PARTIAL`, causa que debe investigarse antes de retirar raw.

Interpretación canónica:

`historical_raw_archive` NO es basura. Es evidencia empírica almacenada de forma ineficiente.

## 5. Arquitectura objetivo

### HOT — `observer_v17.db`

Orientada a operación:

- estado del motor;
- PAPER ledger;
- fills/posiciones/settlement;
- riesgo;
- decisiones/rechazos;
- introspección/health;
- referencias/hash/manifests hacia evidencia externa.

No debe seguir acumulando indefinidamente payloads históricos PPI completos repetidos.

### WARM — History Store

`market_history.db` / History Store v2 mantiene:

- `history_versions_v2` append-only;
- `history_canonical_v2`;
- OHLCV;
- identidad financiera completa;
- source/settlement/asset class;
- `observed_at` / `known_at`;
- hashes;
- revisiones/versiones históricas.

Las revisiones de fuente no deben sobrescribir evidencia anterior silenciosamente.

### RAW EVIDENCE STORE

Separado del observer DB:

- content-addressed por hash;
- deduplicable físicamente;
- compresible;
- fuera del hot path;
- trazable desde cada intento.

Dos intentos distintos con el mismo contenido conservan dos manifests, pero pueden apuntar a un solo objeto raw físico.

### INGEST MANIFEST / PROVENANCE

Cada intento debe registrar como mínimo:

- attempt_id / attempted_at;
- symbol / instrument_type / asset_class;
- market / settlement;
- requested_from / requested_to;
- source;
- status / quality;
- valid_rows / rejected_rows;
- payload/root hash;
- raw evidence reference;
- History Store versions appended;
- canonical changes;
- parser/schema version;
- error/rejection reason;
- trigger/origin.

### LEARNING EVIDENCE

Cada decisión debe poder reconstruir:

- historical version IDs;
- contract evidence version;
- quote/snapshot;
- strategy version;
- configuration hash;
- features;
- regla de aprobación/rechazo;
- decisión final;
- outcomes 5m/15m/1h/cierre/T+1;
- MAE/MFE;
- P&L;
- fees;
- slippage simulado;
- resultado posterior.

## 6. History Ingestion Coordinator

Debe existir un único owner lógico de PPI History.

Todos los disparadores relevantes deben pasar por el `History Ingestion Coordinator`, incluyendo:

- `_daily_sync()`;
- `_background_ingest()`;
- `LOGIN_AND_SYNC`;
- `rc6_postclose_history_job.py`;
- scheduler/catalog;
- mode manager;
- cualquier workflow/systemd/cron equivalente.

Modalidades:

1. `BOOTSTRAP`: ventana larga para instrumentos nuevos o gaps grandes.
2. `INCREMENTAL`: datos nuevos + overlap suficiente para detectar revisiones.
3. `RECONCILIATION`: barrido amplio periódico y controlado para revisiones antiguas.

Regla: el mismo payload obtenido de PPI debe reutilizarse para raw evidence + normalización; Evidence Store no debe provocar una segunda llamada al broker.

## 7. FASE A — Contención + nueva arquitectura de escritura

Objetivo:

`DETENER EL CRECIMIENTO ARQUITECTÓNICAMENTE INCORRECTO SIN BORRAR NADA EXISTENTE`.

Incluye:

- RAW Evidence Store separado;
- ingest manifest/provenance;
- content addressing por hash;
- History Store v2 intacto;
- `history_versions_v2` append-only;
- `history_canonical_v2`;
- History Ingestion Coordinator;
- eliminación de descargas simultáneas/redundantes;
- bootstrap/incremental/reconciliation;
- `historical_raw_archive` legacy intacto;
- compatibilidad de lectura;
- métricas de attempts, unique raw objects, dedupe ratio, bytes, History Store rows, canonical changes, VALID/PARTIAL/INVALID, trigger counts, last/next ingestion y crecimiento estimado.

Durante FASE A está prohibido:

- borrar `historical_raw_archive`;
- `VACUUM`;
- truncar WAL/SHM;
- schema destructivo;
- tocar manualmente DB viva;
- reducir cobertura histórica;
- eliminar PARTIAL;
- mezclar cambios de trading/scalping;
- activar órdenes reales.

## 8. FASE B — SHADOW / reconciliación del legado

Sólo después de FASE A GREEN y estable.

Legacy en modo read-only:

- recorrer `historical_raw_archive`;
- generar manifest por intento;
- calcular/verificar hashes;
- crear RAW objects content-addressed;
- deduplicar físicamente sólo por hash/identidad demostrada;
- asociar cada intento con raw object;
- reconciliar con `history_versions_v2`, `history_canonical_v2`, `market_historical_ohlcv`, `production_history`, source_sync y evidence de quality/rejection;
- medir intentos, payloads únicos, duplicados exactos, overlap, versiones, filas no reconstruibles, PARTIAL, INVALID y conflictos.

Investigación obligatoria:

explicar el volumen de `PARTIAL` y distinguir si proviene de PPI, parser, volume/null/zero, fechas/settlements/tipos, expected vs valid o pérdida de evidencia por validación.

Gate de salida:

`manifest + raw object + History Store` debe reconstruir semánticamente la evidencia original.

Cualquier diferencia no explicada => `FASE_B=RED` y no se pasa a C.

## 9. FASE C — Retirada de redundancia física

Primera fase en la que se permite recuperar espacio del legacy.

Sólo después de equivalencia demostrada y checkpoint explícito.

Debe:

- identificar `body_json` legacy preservado en Evidence Store;
- demostrar provenance/referencias;
- demostrar History Store equivalente;
- preservar manifests, versiones, PARTIAL/conflict/error;
- producir lista exacta de candidatos a retiro;
- calcular espacio recuperable;
- retirar sólo redundancia física demostrada;
- `quick_check`;
- compactación/reconstrucción SQLite sólo en ventana segura;
- `VACUUM` únicamente bajo gates explícitos de writers/readers, espacio temporal, quick_check, rollback y ahorro significativo.

Invariante de cierre:

`EVIDENCE_LOST=0`.

## 10. FASE D — Learning Evidence completa

Después de estabilizar la arquitectura histórica.

Completar vínculo:

`decision -> evidence -> outcome`.

Revisar y conectar:

- decisiones;
- paper trades;
- rejects;
- fills;
- historical snapshots;
- contract evidence;
- learning logs;
- EOD learning.

Debe poder responder posteriormente, entre otras preguntas:

- qué histórico conocía POROTA al decidir;
- qué contrato conocía;
- qué spread existía;
- qué señales participaron;
- por qué aprobó/rechazó;
- qué ocurrió a 5m/15m/1h/cierre/T+1;
- cuánto habría ganado/perdido;
- si un rechazo fue correcto;
- qué reglas funcionan empíricamente;
- impacto de costos/slippage/settlement.

## 11. FASE E — Retención / COLD storage

Actualizar posteriormente `STORAGE_LIFECYCLE_HF6_V2.md`.

Política requerida:

NO aplicar borrado automático de raw único a los 180 días.

KEEP permanente para:

- evidencia única;
- history version única;
- revision/source change;
- conflict;
- PARTIAL relevante;
- incident/audit;
- manifest;
- learning outcome.

Lo antiguo pasa a COLD storage comprimido.

Si el Droplet no alcanza, mover COLD evidence fuera del filesystem operacional manteniendo:

- hashes;
- manifests;
- retrieval;
- integrity;
- provenance.

Nunca perder evidencia sólo para liberar disco.

## 12. Gate 0 antes de iniciar cualquier fase

Este checkpoint registra el plan; NO autoriza ejecutarlo automáticamente.

Antes de comenzar FASE A se debe verificar de nuevo el estado ACTUAL:

- workflows GitHub Actions que puedan modificar observer/runtime;
- branch/SHA realmente live;
- observer running/restarts;
- dashboard running/healthy;
- critical approval healthy;
- `observer_v17.db PRAGMA quick_check=ok`;
- `market_history.db PRAGMA quick_check=ok`;
- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- filesystem con margen seguro;
- ausencia de deploy concurrente.

Luego crear una rama NUEVA desde el SHA live REAL.

No implementar desde `main`, RC5, HF6 viejo, rama de auditoría o rama checkpoint.

## 13. Definition of Done global

El programa sólo está cerrado cuando:

- `FASE_A=GREEN`;
- `FASE_B=GREEN`;
- `FASE_C=GREEN`;
- `FASE_D=GREEN`;
- `FASE_E=GREEN`;
- evidencia única preservada;
- historia versionada preservada;
- manifests permanentes;
- raw deduplicado arquitectónicamente;
- observer DB deja de crecer por snapshots históricos completos repetitivos;
- History Ingestion Coordinator activo;
- ráfagas duplicadas eliminadas;
- bootstrap/incremental/reconciliation funcionando;
- PARTIAL explicado;
- History Store íntegro;
- learning evidence ligada a decisiones/outcomes;
- filesystem bajo control;
- quick_check GREEN;
- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- observer/dashboard/critical approval GREEN;
- checkpoint final publicado en GitHub.

## 14. Posición en backlog RC6

Este frente NO debe representarse como un único item `storage containment Phase A`.

Representación canónica desde ahora:

### P1 / DATA-EVIDENCE-STORAGE PROGRAM

- A — Containment + new write architecture;
- B — Legacy shadow migration/reconciliation;
- C — Proven physical redundancy retirement;
- D — Complete learning evidence linkage;
- E — Retention / compressed cold storage.

FASE A es sólo el primer paso.

No iniciar B antes de A GREEN; no iniciar C antes de B GREEN/equivalencia; D completa el objetivo de aprendizaje; E cierra lifecycle/retención.

`real_orders_sent=0` permanece invariante absoluto durante todo RC6.
