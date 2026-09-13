# POROTA TRADING — HANDOFF PPI WEB RESIDUAL — 2026-09-13

## Estado ejecutivo

Este checkpoint deja el scraper residual PPI Web en estado previo a ejecución masiva, con preflight real de servidor en verde, manifest residual congelado e infraestructura durable preparada para continuar aunque se cierre Termius, ChatGPT o se cambie de chat.

`SCRAPER_READY_TO_EXECUTE=YES`

`MASS_SCRAPING_STARTED=NO`

No iniciar el servicio hasta decisión explícita de ejecución.

## Rama y baseline de handoff

Rama:

`ops/rc6-ppi-web-residual-ready-20260913`

SHA base inmediatamente anterior a este handoff:

`aa57525728cdba372a4958bb6e772340e55c5eda`

Commit del freeze manifest:

`cb80c388fa7053ff76d4f62cebde1547260890f7`

Commit del preflight 24HS:

`45470371784fe189f59c098f2b7b1da90db8f41f`

## Preflight real del servidor

Workflow:

`RC6 PPI Web residual server preflight 2026-09-13`

Run:

`34742710566`

Resultado:

`SUCCESS`

Validaciones relevantes:

- `SAFETY_API_GATE=ok|PRODUCTION_PAPER|0|0|1960`
- API histórica anterior `inactive`
- runtime residual instalado pero deshabilitado
- `ENABLED` ausente
- servicio residual `inactive`
- auth `AUTHENTICATED_TRUSTED_DEVICE`
- smoke `GGAL` con settlement `24HS`
- `DIRECT_HISTORY_SMOKE=GREEN`
- `RESULT=CAPTURED`
- `ROWS_365D=244`
- `FCI_IN_RUNNABLE=NO`
- seguridad post-smoke `ok|PRODUCTION_PAPER|0|0|1960`
- `REAL_ORDERS_SENT=0`
- `MASS_SCRAPING_STARTED=NO`
- `READY_TO_START=YES`

## Freeze inmutable del residual

Workflow:

`RC6 PPI Web residual freeze manifest 2026-09-13`

Run:

`34742874694`

Job:

`103685455440`

Resultado:

`SUCCESS`

Cierre PPI API leído en modo read-only:

- universo total: `1960`
- tareas activas: `0`
- residual total: `679`
- `HARD_PROVIDER_ERROR=22`
- `NO_PROVIDER_ROWS=360`
- `PARTIAL_VALID=281`
- `PROVIDER_INVALID=16`

Separación congelada:

- runnable PPI Web: `642`
- FCI deferred: `37`

Política FCI:

`DEFERRED_PENDING_CHECKPOINT_NOT_DONE_EMPTY`

Hashes congelados:

- `RUNNABLE_FILE_SHA256=62d6280f1090e32f477917049166e5a942c755c36235f652264469f3dd895e9e`
- `DEFERRED_FILE_SHA256=6cdb5554c06e1a6514c65999391a7ab59624fd0f50bc4a885ddc2ffea928f99b`
- `RUNNABLE_ROWS_SHA256=4a2abad6f5b6f8b2001b15fcb5da56ca5fdafb8267b4fe82095621082ea67342`
- `DEFERRED_ROWS_SHA256=bef43e86c43778e7df278c0a7d3728d7f9d3e7bfeabea4eee68279dedce37fad`

Resultado del freeze:

- `MANIFEST_FREEZE=GREEN`
- `READY_FOR_HANDOFF_CHECKPOINT=YES`
- `MASS_SCRAPING_STARTED=NO`

## Identidad de ejecución

Run ID PPI Web:

`PPI-WEB-RESIDUAL-20260913-001`

Servicio systemd:

`porota-ppi-web-residual-rc6.service`

Servicio API previo que debe permanecer no activo:

`porota-ppi-fullfamily-history-rc6.service`

Root durable:

`/opt/porota-ingest/ppi-web-residual`

Archivos de control:

- gate de arranque: `/opt/porota-ingest/ppi-web-residual/ENABLED`
- manifest runnable: `/opt/porota-ingest/ppi-web-residual/residual.jsonl`
- FCI deferred: `/opt/porota-ingest/ppi-web-residual/deferred_fci.jsonl`
- metadata/hash freeze: `/opt/porota-ingest/ppi-web-residual/manifest_meta.json`
- estado durable SQLite: `/opt/porota-ingest/ppi-web-residual/state.sqlite3`
- estado resumido atómico: `/opt/porota-ingest/ppi-web-residual/status.json`
- batches/capturas: `/opt/porota-ingest/ppi-web-residual/batches`

## Arquitectura de recuperación

El runner no depende de la sesión SSH ni del chat.

`ppi_web_residual_state_rc6.py` mantiene:

- run status
- heartbeat
- manifest hash
- total/terminal
- porcentaje de avance
- conteos por estado
- pendientes por familia
- identidad actualmente RUNNING
- timestamps de inicio/finalización

Estados por tarea:

- `PENDING`
- `RUNNING`
- `DONE_VALID`
- `DONE_PARTIAL`
- `DONE_EMPTY`
- `ERROR`

El arranque recupera cualquier tarea huérfana `RUNNING` a `PENDING` con `RECOVERED_ORPHAN_RUNNING`.

Por lo tanto, si un chat se cuelga, otro chat debe reconstruir el estado leyendo este checkpoint y luego consultar `status.json`, `state.sqlite3` y `journalctl` del servicio.

## Seguridad y fail-closed

Antes de trabajar, el runner exige:

- archivo `ENABLED` presente
- API historical writer no activo
- observer DB existente y `quick_check=ok`
- `PRODUCTION_PAPER`
- `real_orders_sent=0`
- 1960 tareas API y 0 activas
- profile browser, secret file, Python, collector y reauth presentes

Si cambia el residual después del freeze, aborta con:

`API_RESIDUAL_CHANGED_AFTER_MANIFEST_FREEZE`

Si cambia un archivo/hash del manifest, aborta con:

`IMMUTABLE_MANIFEST_FILE_CHANGED`

FCI no puede entrar en batch runnable.

Collector PPI Web:

- GET/HEAD/OPTIONS únicamente
- auth headers sólo en memoria
- credenciales/query strings no logueadas
- `canonical_write=DENY` durante captura
- `real_orders_sent=0`
- FCI rechazado/deferred
- ventana efectiva `PREVIOUS_365D`

## Persistencia histórica

Fuente:

`PPI_WEB_HISTORY`

Precedencia:

PPI API > PPI Web > IOL.

PPI Web no debe sobreescribir una vela canónica PPI API de mayor autoridad.

No síntesis, no interpolación, no repair de OHLC.

La existencia de histórico NO implica `READY_PAPER`.

## FCI

FCI/FCI Exterior continúan pendientes y fuera de este scraping runnable.

Total deferred actual:

`37`

No convertir discovery incompleto de FCI en `DONE_EMPTY`.

Referencia pendiente:

`POROTA_TRADING_CHECKPOINT_DATA_LIFECYCLE_INGESTION_PENDING_2026-09-13.md`

## Contratos / READY_PAPER

La cobertura contractual continúa pendiente antes de declarar familias completamente `READY_PAPER`.

Referencia:

`POROTA_TRADING_CHECKPOINT_CONTRACT_COVERAGE_PENDING_2026-09-13.md`

Dimensiones obligatorias por familia:

- `DISCOVERY`
- `HISTORICO`
- `CONTRACT_METADATA`
- `OPERABILITY_RULES`
- `READY_PAPER`

## Productores/timers pausados

Los 18 productores/timers pausados deben permanecer suspendidos durante PPI Web residual, fallback final y validación integral.

No restaurar por mero `active/waiting` ni por job success. La restauración será uno por uno tras revisar lógica, utilidad de fuente y evidencia funcional.

## Próximo paso autorizado por este handoff

Este handoff deja técnicamente preparado el arranque, pero no lo ejecuta.

Antes de iniciar verificar nuevamente:

1. `PRODUCTION_PAPER|0`
2. API history service no activo
3. manifest hashes coinciden
4. `ENABLED` aún ausente
5. service residual inactivo
6. FCI fuera de runnable

Con autorización explícita de ejecución:

1. crear `/opt/porota-ingest/ppi-web-residual/ENABLED`
2. iniciar `porota-ppi-web-residual-rc6.service`
3. comprobar `systemctl is-active`
4. comprobar heartbeat y `status.json`
5. comprobar que `state.sqlite3` tenga `642` tareas del run ID
6. comprobar avance inicial y journal
7. confirmar nuevamente `PRODUCTION_PAPER|0` y cero órdenes reales

## Regla de seguimiento

Durante ejecución, reportar siempre de forma compacta:

`RUN_STATUS | TOTAL | TERMINAL | PENDING | RUNNING | DONE_VALID | DONE_PARTIAL | DONE_EMPTY | ERROR | PROGRESS_PCT | CURRENT | HEARTBEAT | FCI_DEFERRED`

No depender de un chat concreto para seguimiento.

## Pendientes posteriores ya registrados

- estrategia delta/incremental
- FCI/FCI Exterior
- rolling 365 días y archivado/pruning
- revisión de los 18 motores/timers suspendidos
- matriz de utilidad real de fuentes
- cobertura contractual por familia
- fallback IOL sólo para residual que permanezca tras PPI Web
- validación final de duplicados/integridad/seguridad antes de restaurar productores

## Estado final del handoff

`PPI_WEB_PREFLIGHT=GREEN`

`PPI_API_CLOSEOUT=GREEN`

`MANIFEST_FREEZE=GREEN`

`FCI_DEFERRED=37`

`RUNNABLE_RESIDUAL=642`

`RECOVERABLE_FROM_OTHER_CHAT=YES`

`PERSISTENT_PROGRESS=YES`

`SCRAPER_READY_TO_EXECUTE=YES`

`MASS_SCRAPING_STARTED=NO`
