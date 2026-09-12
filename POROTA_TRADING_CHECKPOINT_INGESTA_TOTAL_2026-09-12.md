# POROTA TRADING — CHECKPOINT INGESTA TOTAL — 2026-09-12

## Estado canónico

Secuencia BINDING vigente:

1. `PPI_API_DISCOVERY_CERT`
2. `PPI_API_HISTORY_CLOSEOUT`
3. `PPI_WEB_SCRAPING`
4. `IOL_RESIDUAL`

No se adelanta una fase sobre la anterior. Universo DATA y universo operacional/PAPER permanecen separados. Runtime obligatorio: `PRODUCTION_PAPER`, `real_orders_sent=0`, sin llamadas a rutas de órdenes.

## DIRECTIVA MAESTRA — BINDING

La fuente maestra primaria es **PPI API**. Debe capturarse todo lo que PPI API realmente exponga y sea utilizable para operar: familias, identidades, metadatos/contratos y el histórico disponible. El histórico se completa hacia atrás por chunks idempotentes hasta agotar la cobertura que PPI API pueda entregar o alcanzar una frontera técnica explícitamente demostrada; las ventanas por familia son tamaños de chunk/bootstrap, no un límite definitivo.

Una vez agotada PPI API se genera un manifiesto de gaps por identidad/campo/fecha. Recién entonces se usa **PPI Web scraping autenticado y read-only** para cubrir diferencias o información no expuesta por API. Sólo después del cierre PPI API + PPI Web se permite **IOL** como complemento residual y nunca como reemplazo de PPI.

El resultado debe converger a **un único maestro canónico** para consumo del motor, con identidad completa `(ticker,instrument_type,market,currency,settlement)`, precedencia `PPI_API > PPI_AUTHENTICATED_WEB > IOL_RESIDUAL`, provenance obligatoria y versiones/evidencia preservadas fuera de la superficie canónica activa. No se aceptan duplicados silenciosos, sobreescrituras silenciosas ni dos productores canónicos simultáneos.

Toda exclusión actual de familia/mercado/endpoint es provisional hasta demostrar que PPI no lo expone o que no aplica al universo operativo. Nada queda olvidado por el solo hecho de no estar en el certificado actual.

## HITO 1 — PPI API DISCOVERY CERTIFICADO — GREEN

Workflow: `RC6 PPI API universe certification 2026-09-12`.
Run definitivo: `34701618310` — `SUCCESS`.

Evidencia: `CERT_BASE_CATALOG=901`, `CERT_DIRECT_UNIQUE=1059`, `CERT_TRUSTED_TOTAL=1960`, `CERT_SHADOW_TOTAL=1960`, `CERT_EXTRA=0`, `CERT_MISSING=0`, `CERT_SHADOW_DUP_GROUPS=0`, errores discovery=0, seguridad antes/después `PRODUCTION_PAPER|0`, `ORDER_ROUTES=NOT_CALLED`, `REAL_ORDERS_SENT=0`.

Universo local certificado actual:

| Familia | Identidades |
|---|---:|
| ACCIONES | 55 |
| BONOS | 42 |
| CAUCIONES | 10 |
| CEDEARS | 191 |
| FCI | 1.040 |
| FUTUROS | 52 |
| LEBACS | 1 |
| LETRAS | 23 |
| LICITACIONES | 18 |
| ON | 91 |
| OPCIONES | 437 |
| **TOTAL** | **1.960** |

Mercados certificados en este universo local: `BYMA`, `ROFEX`, `A3`, `OTC`. `ACCIONES-USA`, `FCI-EXTERIOR`, `NYSE`, `NASDAQ` y ETF local no forman parte de las 1.960 actuales; esto **no equivale a exclusión maestra definitiva** y queda como gap de cobertura/taxonomía a verificar contra PPI.

## RCA — sobre-descubrimiento 8.422 → 1.960 — CERRADO

Run reparación `34700865427` — `SUCCESS`. Un closeout anterior generó 6.462 identidades extra. El gate anti-duplicación las detectó y bloqueó Web. Se preservó evidencia en cuarentena y se retiró de superficies activas: 6.462 shadow extras, 4.912 attempts, 162 históricos legacy, 4.913 clasificaciones; además 234.521 filas canónicas v2 y 86.620 close-canónicas asociadas al universo contaminado fueron apartadas de las vistas activas, preservando versiones append-only. Final: shadow=1960, unclassified=0, coarse collisions=0, quick_check=ok, `PRODUCTION_PAPER|0`.

## AISLAMIENTO TEMPORAL DE INGESTA — ACTIVO

Los schedulers/agentes que puedan interferir permanecen pausados hasta terminar este closeout y luego se replanifican. Los 18 timers POROTA relevados quedaron `disabled + inactive`; manifest reversible: `/opt/porota-trading/data/audit/ingestion_isolation_pause_20260912.txt`. Observer/dashboard/runtime y servicios core no productores permanecen activos.

No se reactivan productores recurrentes del histórico y no se usa IOL durante PPI API/PPI Web. Sólo se permite un productor canónico por vez. Probes/monitores read-only no cuentan como productores.

## FASE 2 — PPI API HISTORY CLOSEOUT — STORAGE-GATED

### Regla de almacenamiento — BINDING TEMPORAL

La política anterior 8/6 GiB fue reemplazada por instrucción del usuario. Durante esta ingesta se prioriza completar PPI y la política final de almacenamiento/retención se decide con volumen real al terminar.

- mínimo libre para iniciar/continuar batch: **3 GiB**;
- **hard stop: 2 GiB libres**;
- guard porcentual: aproximadamente **88% usado** como máximo para iniciar batch;
- batches pequeños con control de disco entre batches;
- omitir lo ya cubierto;
- reintentos de `ERROR` acotados e idempotentes;
- `EMPTY_OR_INVALID` confirmado pasa a residual PPI Web, no loop infinito;
- provenance + deduplicación canónica obligatorias;
- evidencia raw exacta content-addressed/gzip cuando `EXTERNAL_EXACT_V1` está activo;
- post-backfill: delta incremental, no repetición masiva innecesaria.

Workflow de auditoría actualizado con esta política: `.github/workflows/rc6-ppi-history-storage-audit-20260912.yml`, commit `023730072194d97c52aa84ff0137f20219e66611`.

### Chunks iniciales PPI API

Son tamaños de chunk/bootstrap, no límites de cobertura final:

| Familia | Chunk inicial |
|---|---:|
| ACCIONES | 365 días |
| BONOS | 365 días |
| CAUCIONES | 365 días |
| CEDEARS | 365 días |
| FCI | 365 días |
| LETRAS | 365 días |
| ON | 365 días |
| FUTUROS | 180 días |
| OPCIONES | 180 días |
| LICITACIONES | 90 días |
| LEBACS/CEDI | 90 días |

Si PPI API entrega historia anterior, se sigue retrocediendo serializada e idempotentemente hasta agotarla o demostrar la frontera del proveedor.

### Integridad / idempotencia / anti-duplicación — BINDING

Entre batches y al cierre de cada familia: `PRAGMA quick_check=ok`; universo/identidades esperadas; cero duplicados por clave canónica completa; una fila canónica activa por identidad+fecha; hashes/versiones para no repetir payload idéntico; provenance; conteos antes/después; min/max date; gaps; errores/empty; raw/quarantine preservados fuera del maestro activo.

`cu_history_store_v2_hf6.py` mantiene PK canónica `(symbol,instrument_type,market,settlement,date)` y versiones append-only; diferencias legítimas de fuente/versiones se resuelven por precedencia, no creando múltiples maestros.

### Política incremental posterior

Tras cerrar backfill completo: `date_from = last_stored_date - 5 días calendario`, `date_to = hoy`, merge idempotente.

## CLEANUP 54 OPCIONES NO CANÓNICAS — RCA DE LENTITUD — 2026-09-12 16:37Z

Workflow original: `RC6 noncanonical history 54 quarantine 2026-09-12`, run `34702471513`. El runner GitHub terminó `cancelled`, pero el proceso remoto SSH/Docker quedó vivo bajo PID 1 y continuó reteniendo `/run/porota-trading-history/ppi-fullfamily-history.lock`.

### Lo ya terminado correctamente

El log original demuestra que la parte del observer no era masiva ni lenta:

- `PREFLIGHT.extra=54`, todas `OPCIONES`;
- `production_history`: 54 puestas en cuarentena y 54 eliminadas;
- `production_history_attempts`: 54 puestas en cuarentena y 54 eliminadas;
- `NONCANONICAL_ACTIVE_AFTER=0`;
- esa etapa tardó aproximadamente 25 segundos entre `SAFETY_BEFORE` y `OBSERVER_REPAIR`.

Por lo tanto **54 claves no significan 54 filas físicas en todo el History Store**, pero tampoco justifican por volumen una hora de CPU en la fase actual.

### Probe vivo más reciente

Probe read-only run `34704131027`, attempt/job más reciente `103585353023`, observado `2026-09-12T16:36:26Z`:

- proceso Python cleanup PID host/container `1015832` seguía vivo;
- elapsed: **3910 s (~65 min)**;
- CPU: **80,7%**;
- lock histórico exclusivo seguía retenido por PID `1015813`;
- filesystem root: `24.883.167.232` bytes total, `9.673.183.232` bytes libres, **62% usado** (~9,1 GiB disponibles);
- data tree: `1.937.620.132` bytes;
- todos los servicios históricos systemd controlados seguían `inactive/dead`;
- no hay evidencia de doble productor.

La evidencia exacta/manifest no cambió entre los probes de 16:26 y 16:36, mientras el Python continuó consumiendo ~80% CPU. Esto cambió el diagnóstico: no se considera ya suficiente decir simplemente “está trabajando”; se investigó el plan SQL.

### Diagnóstico read-only — CONFIRMADO

Workflow diagnóstico: `RC6 noncanonical cleanup diagnostic 2026-09-12`, run `34705796508`, commit `68835673e0cdf1f70b4760831b64bf9d28e30800`, `SUCCESS`, `HOST_MUTATION=NONE`.

Estado visible desde una segunda conexión read-only:

- target lógico: **54 claves**;
- `history_canonical_v2`: **322.565 filas**;
- filas activas que realmente corresponden a esas 54 claves: **1.363**;
- `history_canonical_v2_quarantine_rc6`: **234.521 filas**;
- **sin índices** en la tabla de cuarentena;
- `history_close_canonical_v1`: 17.796 filas;
- matches de las 54 claves en close-canónica: **0**;
- `history_close_canonical_v1_quarantine_rc6`: 86.620 filas y **sin índices**;
- `history_versions_v2`: 561.578 filas;
- `history_close_versions_v1`: 104.433 filas;
- `ppi_ingest_quarantine_version_ids_rc6`: 323.759 filas y sin índices.

`EXPLAIN QUERY PLAN` de la sentencia lenta confirmó:

- `SCAN x` sobre `history_canonical_v2`;
- subquery correlacionada `NOT EXISTS`;
- `SCAN q` sobre `history_canonical_v2_quarantine_rc6` para resolver esa subquery;
- recién después búsqueda de la temp `extra54`.

La tabla de cuarentena fue creada por `CREATE TABLE ... AS SELECT ... WHERE 0`, por lo que no heredó los índices/PK del origen. El SQL de la reparación usa `NOT EXISTS` contra esa tabla sin índice. Con 322.565 filas activas y 234.521 en cuarentena, el plan puede realizar una cantidad enorme de comparaciones repetidas aunque el target útil sea sólo 1.363 filas. **La demora está causada principalmente por un plan SQL patológico/falta de índice, no por que 54 opciones representen un volumen razonablemente enorme.**

### Decisión operativa pendiente sobre el proceso lento

No se lanza backfill, scraping ni otro escritor mientras este proceso retenga el lock. A partir de este RCA ya no conviene dejarlo correr indefinidamente sólo porque use CPU. El camino seguro es una intervención controlada: terminar el proceso viejo, dejar que SQLite haga rollback de la transacción del History Store aún no cerrada, verificar `quick_check`, crear/usar índices adecuados en las superficies de cuarentena o reescribir el filtro para seleccionar primero las 54 claves, y relanzar el cleanup idempotente. No ejecutar una segunda reparación concurrente.

La parte observer ya quedó saneada y es idempotente; la reparación optimizada debe tolerar ese estado previo y conservar toda evidencia existente.

## AUDITORÍA DE CAPACIDAD Y COBERTURA — ESPERANDO LOCK

Workflow: `RC6 PPI history storage audit 2026-09-12`.
Run con política 3/2 GiB: `34705180201`; sigue serializado esperando el lock del cleanup. El disk headroom medido (~9,1 GiB libres) supera holgadamente el gate temporal, pero el audit completo no se declara GREEN hasta poder correr post-cleanup y medir cobertura/integridad.

## Próximo paso exacto

1. Resolver el cleanup 54 con intervención controlada y SQL/indexado, sin doble escritor.
2. Verificar inmediatamente `PRAGMA quick_check=ok`, `NONCANONICAL_ACTIVE_AFTER=0`, conteos de cuarentena, ausencia de duplicados y seguridad `PRODUCTION_PAPER|0`.
3. Liberado el lock, dejar ejecutar la auditoría read-only de capacidad/cobertura con política 3 GiB start / 2 GiB hard stop.
4. Construir faltantes PPI API para las 1.960 identidades certificadas y registro explícito de familias/mercados/endpoints PPI todavía no incorporados.
5. Backfill PPI API por chunks, saltando cobertura existente y retrocediendo hasta agotar histórico disponible.
6. Entre batches: disco, quick_check, duplicados, hashes/versiones, provenance, rangos, gaps y seguridad.
7. Cerrar PPI API con manifiesto residual exacto por identidad/campo/fecha.
8. Abrir PPI Web scraping autenticado read-only sólo sobre gaps y reconciliar contra el mismo maestro.
9. Sólo el residual posterior a PPI API + PPI Web pasa a IOL.
10. Con ingesta completa medir volumen/crecimiento real y definir almacenamiento/retención definitiva.

## FASE 3 — PPI WEB SCRAPING — BLOQUEADA HASTA CIERRE API

Trabaja sólo sobre residuales explícitos o campos contractuales/reference faltantes, con provenance `PPI_AUTHENTICATED_WEB`, lock de navegador, read-only y sin auto-habilitar `READY_PAPER`. No crea segundo datastore maestro.

## FASE 4 — IOL RESIDUAL — ÚLTIMO RECURSO

Sólo después del gap PPI API + PPI Web. Cada dato IOL debe conservar provenance `IOL_RESIDUAL`, pasar los mismos gates y respetar precedencia. Nunca sustituye cobertura disponible de PPI.

## Invariantes finales

- `PRODUCTION_PAPER`
- `real_orders_sent=0`
- no order routes
- PPI API > PPI Web > IOL
- objetivo: todo lo que PPI exponga y sea necesario para operar, sin exclusiones silenciosas
- identidad completa `(ticker,instrument_type,market,currency,settlement)`
- un único maestro canónico
- no doble productor
- no silent overwrite
- deduplicación/hash/versionado/provenance obligatorios
- raw/cuarentena fuera del maestro activo
- no reingesta masiva innecesaria
- no rollback automático de datos canónicos; cualquier rollback técnico de transacción incompleta debe ser controlado y seguido de integridad
- disk gate temporal 3 GiB / hard stop 2 GiB
- política final de almacenamiento pendiente de medición post-ingesta
