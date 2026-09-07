# POROTA TRADING RC6 — CHECKPOINT EMPIRICAL EVIDENCE ARCHITECTURE — 2026-09-07

STATUS=READY_FOR_SINGLE_OWNER_IMPLEMENTATION_AFTER_ACTIVE_DEPLOY
OWNER_RUNTIME_TODAY=CHAT_LUNES_07_09_POROTA_TRADING
DO_NOT_DEPLOY_CONCURRENTLY=YES

## 1. Coordinación obligatoria

Al crear este checkpoint hay un único workflow de GitHub Actions en progreso que puede modificar runtime:

- Workflow: `RC6 scalping postclose transactional deploy 2026-09-07`
- Run ID: `34159808294`
- Branch: `hotfix/rc6-scalping-intraday-contract-20260907`
- Target SHA del run: `aaeb09dc74414140c252d91199abe024a67e995b`
- Base live declarada por ese workflow: `5076b6dff8c644ed73160b4148eb7a1cf9ca7e43`
- El deploy es observer-only y reemplaza/reinicia `porota_production_observer` de forma transaccional.

REGLA ABSOLUTA: no iniciar un segundo deploy del observer ni modificar la rama `hotfix/rc6-scalping-intraday-contract-20260907` mientras el run 34159808294 no haya terminado.

Al finalizar el run:

1. Si termina GREEN, usar como nueva base de implementación el SHA live resultante de ese deploy, validado directamente contra runtime.
2. Si termina RED/rollback, usar el SHA que quede efectivamente live, no asumir que es `aaeb09dc...`.
3. Confirmar antes de cualquier implementación: branch/SHA live, `observer_v17.db quick_check=ok`, `PRODUCTION_PAPER`, `real_orders_sent=0`, observer/dashboard running, y disco con margen seguro.
4. Crear una rama NUEVA desde el SHA live final. No implementar desde esta rama checkpoint ni desde la rama de auditoría.

## 2. Principio de negocio no negociable

El objetivo principal es maximizar evidencia empírica e historia útil para aprendizaje. No se debe perder evidencia única.

La corrección arquitectónica NO consiste en guardar menos historia. Consiste en evitar copias físicas redundantes y separar responsabilidades.

Reglas:

- evidencia empírica única: KEEP;
- versiones históricas únicas: KEEP;
- revisiones/cambios de fuente: KEEP;
- conflictos, parciales, errores e incidentes: KEEP;
- manifest de cada intento de ingesta: KEEP;
- learning evidence y outcomes: KEEP;
- contenido físicamente redundante con identidad/hash comprobado: puede deduplicarse sin perder referencias;
- no borrar raw existente hasta completar migración, reconstrucción y pruebas de equivalencia.

## 3. Hallazgo actual

`observer_v17.db` contiene `historical_raw_archive` con aproximadamente 1.266 GB y 25.860 filas.

Las 25.860 filas actuales son `origin='PPI_HISTORY'`.

El writer productivo en `bf_production_paper_observer.py::_download_histories()` guarda en cada intento un `body_json` con un wrapper que incluye `payload_json` con la respuesta histórica completa de PPI, normalmente con ventana de 365 días. La `row_key` incluye `attempted_at`, por lo que cada intento crea una nueva clave aunque el histórico se superponga casi completamente con el anterior.

El mismo payload luego se ingresa al History Store v2 (`history_versions_v2` / `history_canonical_v2`).

Por lo tanto el problema es doble:

1. almacenamiento raw voluminoso dentro de la DB operacional;
2. múltiples disparadores/ráfagas de ingesta que aumentan el crecimiento.

No es correcto resolverlo borrando evidencia histórica.

## 4. Arquitectura objetivo — POROTA Empirical Evidence Architecture v1

### HOT — `observer_v17.db`

Debe conservar principalmente:

- estado operacional;
- PAPER ledger/fills/positions/settlements;
- decisiones y razones;
- riesgo/health/introspection hot;
- referencias/hash/manifests hacia evidencia histórica externa.

No debe contener indefinidamente payloads históricos PPI completos y repetitivos.

### WARM — History Store

`market_history.db` / History Store v2 debe conservar permanentemente:

- `history_versions_v2` append-only;
- `history_canonical_v2`;
- identidad financiera completa;
- OHLCV;
- source;
- `observed_at`/`known_at`;
- payload hash;
- revisiones históricas sin sobrescritura silenciosa.

### RAW EVIDENCE STORE

Crear almacenamiento separado del observer DB, content-addressed por hash.

Cada payload único se almacena físicamente una sola vez, preferentemente comprimido fuera del hot path.

Dos intentos distintos que obtienen el mismo contenido deben conservar dos manifests, pero pueden apuntar al mismo objeto raw/hash.

### INGEST MANIFEST / PROVENANCE

Crear registro permanente e inmutable por intento, con como mínimo:

- attempt_id;
- attempted_at;
- symbol;
- instrument_type;
- market;
- settlement;
- requested_from/requested_to;
- source;
- status/quality;
- valid_rows;
- payload/root hash;
- raw object reference;
- versions appended;
- canonical changes;
- parser/schema version;
- error/rejection reason.

### LEARNING EVIDENCE

Vincular de manera explícita:

`qué sabía -> qué decidió -> por qué -> qué ocurrió después`.

Cada decisión debería poder referenciar las versiones históricas/contractuales y snapshots utilizados, y luego asociar outcomes: 5m/15m/1h/close/T+1, MAE, MFE, P&L, fees/slippage simulated y clasificación posterior de la decisión.

## 5. Estrategia de ingesta objetivo

Un solo `History Ingestion Coordinator` debe ser owner de las llamadas a PPI History.

Modalidades:

1. BOOTSTRAP: instrumento nuevo o gap importante -> ventana larga (ej. 365 días o lo necesario).
2. INCREMENTAL: actualización frecuente -> sólo período nuevo + ventana de overlap para detectar revisiones.
3. RECONCILIATION: barrido histórico amplio periódico para detectar correcciones antiguas de fuente.

Otros schedulers/jobs/comandos no deben llamar directamente `_download_histories()` sin pasar por el coordinador.

Auditar especialmente los disparadores existentes:

- `_daily_sync()`;
- `_background_ingest()`;
- `LOGIN_AND_SYNC`;
- `rc6_postclose_history_job.py`;
- scheduler/catalog/mode-manager;
- cualquier GitHub Action o systemd/cron que invoque sincronización.

La evidencia de auditoría mostró ritmo normal de 40 escrituras por hora activa, pero también ráfagas de ~1000-1200 escrituras/hora. Debe eliminarse la duplicación de disparadores, no la evidencia resultante.

## 6. Implementación recomendada HOY — por fases

### FASE A — Contención y nueva escritura, sin borrar nada

Objetivo: detener crecimiento arquitectónicamente incorrecto sin perder evidencia.

- agregar Evidence Store + manifest de intentos;
- escribir raw nuevo fuera de `observer_v17.db`;
- mantener History Store v2 como fuente histórica normalizada;
- introducir History Ingestion Coordinator;
- conservar compatibilidad de lectura mientras migra el legado;
- no borrar ni modificar `historical_raw_archive` existente;
- no VACUUM;
- no schema-destructive migration;
- no manipular WAL/SHM manualmente.

Gates antes de activar:

- focused tests;
- fail-closed;
- History Store equivalence/replay;
- `observer_v17.db quick_check=ok`;
- `market_history.db quick_check=ok`;
- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- observer/dashboard health;
- rollback reconstruible desde GitHub, no rollback local como política de aplicación.

### FASE B — Shadow/reconciliation del legado

- recorrer `historical_raw_archive` read-only;
- generar manifests y objetos content-addressed;
- reconciliar contra `history_versions_v2`, `history_canonical_v2` y otras fuentes;
- probar reconstrucción semántica de muestras y luego cobertura completa;
- producir hashes/counts/coverage antes/después.

Todavía no borrar.

### FASE C — Retiro de redundancia física

Sólo después de equivalencia demostrada:

- retirar del observer DB los blobs raw migrados/redundantes;
- preservar manifest, hashes, provenance, versiones y learning evidence;
- compactar/reconstruir SQLite únicamente en ventana segura;
- medir ahorro real;
- postflight completo.

## 7. Qué NO debe hacer el chat owner

- no ejecutar en paralelo con otro deploy observer-only;
- no tomar `main`, RC5, HF6 viejo o una rama audit como nueva base live;
- no borrar `historical_raw_archive` por tamaño;
- no deduplicar por heurística sin hash/provenance;
- no sobrescribir revisiones históricas;
- no reducir cobertura histórica para ahorrar disco;
- no confundir RAW Evidence con learning evidence;
- no pausar market data live por esta migración salvo gate de seguridad explícito;
- no cambiar lógica de trading/scalping como parte de este frente.

## 8. Evidencia previa en GitHub

Rama de auditoría:
`audit/rc6-historical-raw-architecture-20260907`

Reportes:

- `POROTA_RC6_HISTORICAL_RAW_ARCHITECTURE_AUDIT_2026-09-07.txt`
- `POROTA_RC6_HISTORICAL_RAW_INDEX_AUDIT_2026-09-07.txt`
- `POROTA_RC6_HISTORICAL_RAW_GROWTH_AUDIT_2026-09-07.txt`
- `POROTA_RC6_HISTORICAL_RAW_TRIGGER_AUDIT_2026-09-07.txt`

Política existente a actualizar posteriormente:
`STORAGE_LIFECYCLE_HF6_V2.md`

## 9. Definition of Done de la FASE A de hoy

FASE_A_DONE=YES sólo si:

- active scalping deploy ya terminó y se tomó su SHA live final como base;
- no existe segundo deploy concurrente;
- nueva evidencia se escribe fuera del observer DB;
- cada intento sigue siendo trazable permanentemente;
- History Store versionado sigue recibiendo toda evidencia válida;
- raw único se preserva por hash;
- ninguna evidencia legacy fue borrada;
- ingesta duplicada queda coordinada/fail-closed;
- tests + preflight + postflight GREEN;
- `real_orders_sent=0`;
- checkpoint final publicado en GitHub con SHA runtime y métricas de crecimiento antes/después.
