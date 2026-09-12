# POROTA TRADING — CHECKPOINT INGESTA TOTAL — 2026-09-12

## Estado canónico

Secuencia BINDING vigente:

1. `PPI_API_DISCOVERY_CERT`
2. `PPI_API_HISTORY_CLOSEOUT`
3. `PPI_WEB_SCRAPING`
4. `IOL_RESIDUAL`

No se permite adelantar una fase sobre la anterior. Universo DATA separado del universo operacional/PAPER. Runtime debe permanecer `PRODUCTION_PAPER` y `real_orders_sent=0`.

## HITO 1 — PPI API DISCOVERY CERTIFICADO

Workflow: `RC6 PPI API universe certification 2026-09-12`.

Run definitivo: `34701618310` — `SUCCESS`.

Evidencia de cierre:

- `CERT_BASE_CATALOG=901`
- `CERT_DIRECT_UNIQUE=1059`
- `CERT_TRUSTED_TOTAL=1960`
- `CERT_SHADOW_TOTAL=1960`
- `CERT_EXTRA=0`
- `CERT_MISSING=0`
- `CERT_SHADOW_DUP_GROUPS=0`
- errores de discovery = `0`
- `SAFETY_BEFORE=PRODUCTION_PAPER|0`
- `SAFETY_AFTER=PRODUCTION_PAPER|0`
- `ORDER_ROUTES=NOT_CALLED`
- `REAL_ORDERS_SENT=0`
- `PPI_API_DISCOVERY_MILESTONE=CERTIFIED`

Conteo certificado por familia:

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

Mercados locales admitidos: `BYMA`, `ROFEX`, `A3`, `OTC`.

Fuera del alcance actual: `ACCIONES-USA`, `FCI-EXTERIOR`, `NYSE`, `NASDAQ`.

ETF local API: 13 probes BYMA correctos, 13 vacíos, 0 identidad ETF local normalizada. No se inventan identidades sintéticas. ETF Web queda sólo para discovery/reference posterior hasta reconciliar una identidad local válida.

### Discovery complementario certificado

- FCI: 34 búsquedas, 34 OK, 23 vacías, 0 errores, 3.170 resultados normalizados antes de dedupe.
- LICITACIONES: 24 búsquedas, 24 OK, 16 vacías, 0 errores, 45 normalizados antes de dedupe.
- LEBAC: 10 búsquedas, 10 OK, 10 vacías, 0 normalizados.
- NOBAC: 10 búsquedas, 10 OK, 8 vacías, 2 normalizados antes de reconciliación.
- ETF: 13 búsquedas, 13 OK, 13 vacías, 0 normalizados.

Resultado final después de dedupe/reconciliación: exactamente las 1.960 identidades certificadas.

## RCA previo — sobre-descubrimiento y reparación

Un closeout anterior expandió erróneamente el shadow a 8.422 identidades. El gate anti-duplicación detectó 6.462 extras y bloqueó el avance a Web.

Reparación: run `34700865427` — `SUCCESS`.

Resultado:

- `current=8422`, `trusted=1960`, `extra=6462`, `missing=0`, `errors=0`, `coarse_overlap=0`;
- 6.462 identidades extra a cuarentena y fuera del shadow activo;
- 4.912 attempts extra a cuarentena y fuera del ledger activo;
- 162 históricos legacy extra a cuarentena;
- 4.913 clasificaciones contaminadas a cuarentena;
- History Store: 234.521 filas canónicas y 86.620 close-canónicas asociadas al universo extra apartadas de vistas activas; versiones append-only preservadas;
- final: `shadow=1960`, `classified_join=1960`, `unclassified=0`, `trusted_coarse_collision_groups=0`;
- `pragma quick_check=ok`;
- `PRODUCTION_PAPER|0`.

No hubo borrado silencioso de evidencia; la contaminación quedó trazable en cuarentena.

## AISLAMIENTO TEMPORAL DE INGESTA — ACTIVO

Por pedido explícito del usuario, desde 2026-09-12 se suspende temporalmente todo scheduler, job o agente paralelo que pueda interferir con la secuencia de ingesta. Se replanificará al terminar el trabajo de hoy.

### ChatGPT automations

Las dos continuaciones que estaban activas fueron deshabilitadas:

- `Continuar pipeline PPI`
- `Continuar saneamiento PPI`

No queda ninguna automation activa de POROTA que pueda arrancar trabajo en paralelo.

### Inventario host previo al aislamiento

Workflow read-only: `RC6 ingestion interference inventory 2026-09-12`, run `34701961019`.

Detectó 18 timers POROTA habilitados y activos, incluyendo históricos, contract evidence/scraping, integrity, health, introspection, backup y exports. También detectó procesos stale de pruebas IOL y procesos antiguos `python -` de ejecuciones ad-hoc, además de los procesos normales del runtime.

### Timers pausados

Workflow: `RC6 ingestion isolation pause 2026-09-12`, run `34702034967`.

Quedaron `disabled` + `inactive` los 18 timers siguientes:

- `porota-a3-history-daily-rc6.timer`
- `porota-a3-history-reconcile-rc6.timer`
- `porota-a3-history-weekend-rc6.timer`
- `porota-candle-integrity-rc6.timer`
- `porota-contract-evidence-rc6.timer`
- `porota-contract-evidence-weekend-backfill-rc6.timer`
- `porota-fast-functional-health-rc6.timer`
- `porota-full-db-integrity-rc6.timer`
- `porota-functional-health-rc6.timer`
- `porota-history-postclose-rc6.timer`
- `porota-host-general-backup-rc6.timer`
- `porota-introspection-publish-rc6.timer`
- `porota-introspection-rc6.timer`
- `porota-log-export-hf6.timer`
- `porota-ppi-argentina-nightly-rc6.timer`
- `porota-preopen-rc6.timer`
- `porota-scheduler-export-hf6.timer`
- `porota-weekend-ingestion-audit-rc6.timer`

El workflow también detuvo servicios timer-triggered que estuvieran `active/activating` y ejecutó limpieza de procesos stale de IOL y `python -` antiguos. El post-check mostró `POST_PAUSE_OLD_STDIN` vacío.

El run figura `failure` únicamente por un bug de formato en el print final de seguridad (`TypeError: not enough arguments for format string`) después de haber completado la pausa. Antes del bug imprimió `DB_QUICK_CHECK=ok`. La última seguridad explícita inmediatamente anterior, en el run de certificación, fue `PRODUCTION_PAPER|0`, `REAL_ORDERS_SENT=0`. La pausa no invocó rutas de órdenes ni modificó datos de trading.

Manifest reversible guardado en host:

`/opt/porota-trading/data/audit/ingestion_isolation_pause_20260912.txt`

Ese manifest conserva el estado previo de los timers para replanificación/restauración posterior.

### Servicios deliberadamente NO detenidos

Se mantienen sólo los servicios core no scheduler que no son productores de ingesta:

- `porota-critical-approval-rc6.service`
- `porota-critical-github-proxy-rc6.service`

También siguen vivos el observer/dashboard/runtime principal; no se detuvo el sistema productivo PAPER.

### GitHub Actions paralelos

Se detectó un audit T673O paralelo que estaba ya en vuelo; terminó `SUCCESS` en el run `34702030484` y no quedó activo.

Luego se ejecutó `RC6 ingestion GitHub Actions quiesce 2026-09-12`, run `34702148595` — `SUCCESS`.

Durante el aislamiento aparecieron además dos workflows generados fuera de esta secuencia controlada: `RC6 T673O quarantine marker audit 2026-09-12` run `34702221186` y `RC6 canonical 1960 PPI API history audit 2026-09-12` run `34702320975`. Para respetar la orden de no tener agentes paralelos, se reejecutó el quiesce; el segundo quedó explícitamente `cancelled` y los dos dejaron de estar activos. No se toma ninguna de esas ejecuciones como autorización para adelantar Fase 2.

Snapshot final verificado después del segundo quiesce:

- GitHub Actions `in_progress=0`
- GitHub Actions `queued=0`

No queda ningún workflow GitHub concurrente en ese snapshot. Si otro chat/agente externo vuelve a crear un run, debe cancelarse antes de continuar con el productor canónico; no se permite competir por el runtime o las bases.

## Política de aislamiento hasta fin del trabajo de hoy

1. No reactivar timers automáticamente.
2. No crear automations recurrentes.
3. Sólo este chat puede iniciar un workflow puntual necesario para la secuencia canónica.
4. Cada workflow puntual debe terminar antes de iniciar el siguiente productor.
5. Discovery, históricos y scraping permanecen estrictamente serializados.
6. No se usa IOL durante PPI API / PPI Web.
7. No se permite doble ingesta ni overwrite silencioso.
8. Al finalizar hoy, revisar el manifest de pausa y replanificar explícitamente qué timers vuelven, con qué frecuencia y dependencia.

## FASE 2 — PPI API HISTORY CLOSEOUT

**Estado:** lista para iniciar, pero todavía no iniciada después del aislamiento.

Debe consumir exclusivamente las 1.960 identidades certificadas. Antes de reintentar nada, ejecutar una auditoría post-reparación que mida nuevamente, por identidad completa y familia:

- `VALID_PAYLOAD`
- `PARTIAL`
- `EMPTY_OR_INVALID`
- `ERROR`
- filas válidas almacenadas
- cobertura temporal efectiva
- provenance/origen
- duplicados exactos y colisiones de clave

No reutilizar como verdad canónica los totales históricos previos al incidente sin revalidación.

Reintentar solamente faltantes/`ERROR` con retry acotado e idempotente. `EMPTY_OR_INVALID` confirmado después de agotar PPI API pasa al manifiesto Web; no justifica reingesta masiva.

### Clave histórica

Identidad canónica: `(ticker, instrument_type, market, currency, settlement)`.

Las tablas legacy reducen identidad a `(symbol, instrument_type, settlement)`. Antes de PPI Web histórico debe demostrarse que no hay colisiones; si existen, se usa una superficie canónica v2 con identidad completa. Nunca `INSERT OR REPLACE` para ocultar conflictos.

## FASE 3 — PPI WEB SCRAPING

Bloqueada hasta cerrar históricos API.

PPI Web sólo podrá trabajar sobre residuales explícitos o campos contractuales/reference faltantes, read-only, con provenance `PPI_AUTHENTICATED_WEB`, lock de navegador compartido y sin auto-habilitar `READY_PAPER`.

El servicio weekend previo estaba `failed`, `ExecMainStatus=4`, por sesión autenticada expirada. Su RCA/repair se hará recién cuando corresponda abrir esta fase.

## Invariantes de seguridad

- `PRODUCTION_PAPER`
- `real_orders_sent=0`
- ninguna tarea DATA llama rutas de órdenes
- no real-order capability
- universo DATA != universo PAPER
- provenance obligatoria
- PPI API > PPI Web > IOL
- no doble productor sobre una misma superficie
- no rollback automático

## Próximo paso autorizado

Con `PPI_API_DISCOVERY_CERT=CERTIFIED` y el entorno aislado, el siguiente trabajo permitido es **auditoría histórica post-reparación read-only del universo exacto de 1.960 identidades**. Sólo después de esa auditoría se decidirán retries históricos selectivos.
