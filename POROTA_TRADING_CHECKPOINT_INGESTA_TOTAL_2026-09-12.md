# POROTA TRADING — CHECKPOINT INGESTA TOTAL — 2026-09-12

## Estado canónico

Secuencia BINDING vigente:

1. `PPI_API_DISCOVERY_CERT`
2. `PPI_API_HISTORY_CLOSEOUT`
3. `PPI_WEB_SCRAPING`
4. `IOL_RESIDUAL`

No se adelanta una fase sobre la anterior. Universo DATA y universo operacional/PAPER permanecen separados. Runtime obligatorio: `PRODUCTION_PAPER`, `real_orders_sent=0`, sin llamadas a rutas de órdenes.

## HITO 1 — PPI API DISCOVERY CERTIFICADO — GREEN

Workflow: `RC6 PPI API universe certification 2026-09-12`.
Run definitivo: `34701618310` — `SUCCESS`.

Evidencia: `CERT_BASE_CATALOG=901`, `CERT_DIRECT_UNIQUE=1059`, `CERT_TRUSTED_TOTAL=1960`, `CERT_SHADOW_TOTAL=1960`, `CERT_EXTRA=0`, `CERT_MISSING=0`, `CERT_SHADOW_DUP_GROUPS=0`, errores discovery=0, seguridad antes/después `PRODUCTION_PAPER|0`, `ORDER_ROUTES=NOT_CALLED`, `REAL_ORDERS_SENT=0`.

Universo local certificado PPI API:

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

Mercados: `BYMA`, `ROFEX`, `A3`, `OTC`. Fuera de alcance: `ACCIONES-USA`, `FCI-EXTERIOR`, `NYSE`, `NASDAQ`. ETF local API: probes correctos pero sin identidad local normalizada; no se inventan identidades.

## RCA — sobre-descubrimiento 8.422 → 1.960 — CERRADO

Run reparación `34700865427` — `SUCCESS`. Un closeout anterior generó 6.462 identidades extra. El gate anti-duplicación las detectó y bloqueó Web. Se preservó evidencia en cuarentena y se retiró de superficies activas: 6.462 shadow extras, 4.912 attempts, 162 históricos legacy, 4.913 clasificaciones; además 234.521 filas canónicas v2 y 86.620 close-canónicas asociadas al universo contaminado fueron apartadas de las vistas activas, preservando versiones append-only. Final: shadow=1960, unclassified=0, coarse collisions=0, quick_check=ok, `PRODUCTION_PAPER|0`.

## AISLAMIENTO TEMPORAL DE INGESTA — ACTIVO

Por instrucción del usuario, todos los schedulers/agentes que puedan interferir permanecen pausados hasta terminar el trabajo de hoy y luego se replanifican. Las automations de POROTA están deshabilitadas. Los 18 timers POROTA relevados quedaron `disabled + inactive`; el manifest reversible está en `/opt/porota-trading/data/audit/ingestion_isolation_pause_20260912.txt`. Observer/dashboard/runtime y servicios core no productores permanecen activos.

No se reactivan timers, no se crean automations recurrentes y no se usa IOL durante PPI API/PPI Web. Sólo se permite un productor canónico por vez.

## FASE 2 — PPI API HISTORY CLOSEOUT — STORAGE-GATED

### Regla crítica de almacenamiento — BINDING

No se hará una descarga histórica indiscriminada de 365 días para todos los instrumentos ni se repetirá una ventana completa cada noche. Antes del backfill se mide capacidad real de disco y cobertura post-reparación. El backfill sólo empieza si el gate de espacio queda GREEN.

Política inicial:

- mínimo para iniciar fase masiva: **8 GiB libres**;
- hard stop durante la ingesta: **6 GiB libres**;
- no iniciar si el filesystem raíz supera aproximadamente **75% usado**;
- batches pequeños, con control de disco entre batches;
- una identidad con cobertura suficiente se salta: no se vuelve a descargar por rutina;
- `ERROR`/faltante se reintenta de forma acotada e idempotente;
- `EMPTY_OR_INVALID` confirmado se clasifica como residual para PPI Web, no entra en loop infinito;
- provenance obligatoria y deduplicación canónica v2;
- la evidencia raw exacta usa almacenamiento content-addressed/gzip cuando `EXTERNAL_EXACT_V1` está activo;
- después del backfill inicial se migra a **delta incremental**, nunca a repetir la ventana completa.

### Ventanas máximas propuestas para el backfill inicial

Estas son cotas de solicitud, no obligación de almacenar filas inexistentes ni de volver a pedir datos ya cubiertos:

| Familia | Ventana máxima inicial |
|---|---:|
| ACCIONES | 365 días calendario |
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

Racional: 365 días calendario da margen razonable para indicadores de hasta ~200 ruedas sin almacenar años innecesarios. Futuros y opciones tienen ciclo de vida/expiración, por lo que 365 días por contrato agrega poco valor y mucho volumen. Licitaciones/CEDI son principalmente gaps de endpoint/taxonomía y se prueban con ventana corta.

### Política incremental posterior

Una vez cerrado el backfill inicial: `date_from = last_stored_date - 5 días calendario`, `date_to = hoy`, con merge idempotente. Se conservan 5 días de solapamiento para correcciones tardías del proveedor sin volver a bajar 6/12 meses.

`cu_history_store_v2_hf6.py` evita agregar una versión idéntica si ya existe el mismo payload hash para la identidad/fecha/fuente, y mantiene una sola fila canónica por identidad completa y fecha. Aun así, repetir ventanas completas genera costo en API, ledgers y raw evidence; por eso queda prohibido como política normal.

### Auditoría de capacidad y cobertura — EN EJECUCIÓN / SERIALIZADA

Workflow: `RC6 PPI history storage audit 2026-09-12`.
Run: `34702766005`.
Commit: `40e90f225806b1475a40571a93a09171b35e66a9`.

La auditoría es read-only y mide antes de ingerir: total/usado/libre del root filesystem, tamaño de `/opt/porota-trading/data`, archivos más grandes, observer DB, History Store DB, filas/versiones canónicas, cobertura por familia, tamaños de payload legacy/raw y estado actual por familia dentro de las 1.960 identidades. También exige shadow=1960, quick_check=ok y `PRODUCTION_PAPER|0`.

No se ha iniciado todavía un nuevo backfill histórico post-reparación: primero debe salir este inventario de capacidad.

### Cleanup no canónico previo — serializado

Antes de la auditoría de capacidad sigue en curso el workflow `RC6 noncanonical history 54 quarantine 2026-09-12`, run `34702471513`. No es una nueva ingesta: aparta 54 claves históricas `OPCIONES` no canónicas que quedaron activas después del saneamiento, preservando evidencia en cuarentena. La auditoría de disco está esperando correctamente el mismo lock y no compite.

Probe de locks `34702851116` / `34702976351`: todos los servicios históricos systemd están `inactive/dead`; el holder del lock actual es exactamente ese cleanup y el segundo `flock` corresponde a la auditoría de capacidad esperando turno. No hay un scheduler histórico paralelo.

## Próximo paso exacto

1. Dejar terminar y validar el cleanup de 54 claves no canónicas.
2. Ejecutar automáticamente a continuación la auditoría read-only `34702766005` ya en espera del lock.
3. Leer capacidad real y cobertura real post-reparación.
4. Sólo si `DISK_POLICY.start_gate=GREEN`, iniciar backfill PPI API selectivo y por batches sobre las 1.960 identidades certificadas, usando las ventanas máximas por familia y saltando cobertura suficiente.
5. Revalidar disco, quick_check, duplicados, provenance y seguridad entre batches.
6. Cerrar API históricos y generar manifiesto residual.
7. Recién entonces abrir PPI Web scraping read-only residual.

## FASE 3 — PPI WEB SCRAPING — BLOQUEADA

No comienza hasta cerrar históricos API. PPI Web sólo trabaja sobre residuales explícitos o campos contractuales/reference faltantes, con provenance `PPI_AUTHENTICATED_WEB`, lock de navegador y sin auto-habilitar `READY_PAPER`. La sesión weekend previa estaba expirada (`ExecMainStatus=4`); su RCA se hará al abrir esta fase.

## Invariantes finales

- `PRODUCTION_PAPER`
- `real_orders_sent=0`
- no order routes
- PPI API > PPI Web > IOL
- identidad canónica completa `(ticker,instrument_type,market,currency,settlement)`
- no doble productor
- no silent overwrite
- no reingesta masiva innecesaria
- no rollback automático
- disco protegido por gate antes y durante el backfill
