# POROTA TRADING — CHECKPOINT INGESTA TOTAL — 2026-09-12

## Estado canónico

Secuencia BINDING vigente:

1. `PPI_API_DISCOVERY_CERT`
2. `PPI_API_HISTORY_CLOSEOUT`
3. `PPI_WEB_SCRAPING`
4. `IOL_RESIDUAL`

No se adelanta una fase sobre la anterior. Universo DATA y universo operacional/PAPER permanecen separados. Runtime obligatorio: `PRODUCTION_PAPER`, `real_orders_sent=0`, sin llamadas a rutas de órdenes.

## DIRECTIVA MAESTRA — BINDING

El objetivo no es una muestra ni un subconjunto arbitrario. La fuente maestra primaria es **PPI API** y se debe capturar todo lo que la API de PPI realmente exponga y sea utilizable para operar: familias, identidades, metadatos/contratos y el histórico disponible. El histórico se completa hacia atrás por chunks idempotentes hasta agotar la cobertura que PPI API pueda entregar o alcanzar una frontera técnica explícitamente demostrada; las ventanas por familia son tamaños de chunk/bootstrap, no un límite definitivo de cobertura.

Una vez agotada PPI API se genera un manifiesto de gaps por identidad/campo/fecha. Recién entonces se usa **PPI Web scraping autenticado y read-only** para cubrir diferencias o información no expuesta por API. Sólo después del cierre PPI API + PPI Web se permite **IOL** como complemento residual y nunca como reemplazo de PPI.

El resultado debe converger a **un único maestro canónico** para consumo del motor, con identidad completa `(ticker,instrument_type,market,currency,settlement)`, precedencia de fuente `PPI_API > PPI_AUTHENTICATED_WEB > IOL_RESIDUAL`, provenance obligatoria y versiones/evidencia preservadas fuera de la superficie canónica activa. No se aceptan duplicados silenciosos, sobreescrituras silenciosas ni dos productores canónicos simultáneos.

Toda exclusión actual de una familia/mercado/endpoint es provisional hasta demostrar que PPI no lo expone o que no aplica al universo operativo; no se convierte en exclusión definitiva sin justificación explícita. En particular, universos no incluidos en el certificado local actual deben quedar registrados como gap a verificar y no olvidados.

## HITO 1 — PPI API DISCOVERY CERTIFICADO — GREEN

Workflow: `RC6 PPI API universe certification 2026-09-12`.
Run definitivo: `34701618310` — `SUCCESS`.

Evidencia: `CERT_BASE_CATALOG=901`, `CERT_DIRECT_UNIQUE=1059`, `CERT_TRUSTED_TOTAL=1960`, `CERT_SHADOW_TOTAL=1960`, `CERT_EXTRA=0`, `CERT_MISSING=0`, `CERT_SHADOW_DUP_GROUPS=0`, errores discovery=0, seguridad antes/después `PRODUCTION_PAPER|0`, `ORDER_ROUTES=NOT_CALLED`, `REAL_ORDERS_SENT=0`.

Universo local certificado PPI API actual:

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

Mercados certificados en este universo local: `BYMA`, `ROFEX`, `A3`, `OTC`. `ACCIONES-USA`, `FCI-EXTERIOR`, `NYSE`, `NASDAQ` y ETF local no forman parte de las 1.960 identidades certificadas actuales; esto **no equivale a una exclusión maestra definitiva**. Deben quedar como gaps de cobertura/taxonomía a verificar contra lo que PPI exponga, sin inventar identidades.

## RCA — sobre-descubrimiento 8.422 → 1.960 — CERRADO

Run reparación `34700865427` — `SUCCESS`. Un closeout anterior generó 6.462 identidades extra. El gate anti-duplicación las detectó y bloqueó Web. Se preservó evidencia en cuarentena y se retiró de superficies activas: 6.462 shadow extras, 4.912 attempts, 162 históricos legacy, 4.913 clasificaciones; además 234.521 filas canónicas v2 y 86.620 close-canónicas asociadas al universo contaminado fueron apartadas de las vistas activas, preservando versiones append-only. Final: shadow=1960, unclassified=0, coarse collisions=0, quick_check=ok, `PRODUCTION_PAPER|0`.

## AISLAMIENTO TEMPORAL DE INGESTA — ACTIVO

Por instrucción del usuario, todos los schedulers/agentes que puedan interferir permanecen pausados hasta terminar este closeout y luego se replanifican. Los 18 timers POROTA relevados quedaron `disabled + inactive`; el manifest reversible está en `/opt/porota-trading/data/audit/ingestion_isolation_pause_20260912.txt`. Observer/dashboard/runtime y servicios core no productores permanecen activos.

No se reactivan productores recurrentes del histórico y no se usa IOL durante PPI API/PPI Web. Sólo se permite un productor canónico por vez. Los probes/monitores read-only no cuentan como productores y no pueden mutar datos.

## FASE 2 — PPI API HISTORY CLOSEOUT — STORAGE-GATED

### Regla crítica de almacenamiento — BINDING TEMPORAL

La reserva anterior de 8/6 GiB queda reemplazada por instrucción del usuario. Durante esta ingesta se prioriza completar PPI y después se decidirá la política final de almacenamiento con mediciones reales.

Política temporal vigente:

- mínimo libre para iniciar/continuar un batch masivo: **3 GiB**;
- **hard stop: 2 GiB libres**;
- guard porcentual alineado: aproximadamente **88% usado** como máximo para iniciar un batch;
- la política final de retención/almacenamiento se define **después** de terminar PPI API + gaps PPI Web y medir volumen real;
- batches pequeños, con control de disco entre batches;
- se omite lo ya cubierto: no se vuelve a descargar por rutina;
- `ERROR`/faltante se reintenta de forma acotada e idempotente;
- `EMPTY_OR_INVALID` confirmado se clasifica como residual para PPI Web, no entra en loop infinito;
- provenance obligatoria y deduplicación canónica v2;
- la evidencia raw exacta usa almacenamiento content-addressed/gzip cuando `EXTERNAL_EXACT_V1` está activo;
- después del backfill completo se migra a **delta incremental**, nunca a repetir innecesariamente toda la historia.

### Chunks iniciales de histórico PPI API

Los siguientes valores son **tamaños de chunk/bootstrap**, no límites máximos del histórico final. Si PPI API entrega datos anteriores, el proceso debe seguir retrocediendo de forma serializada e idempotente hasta agotar la historia disponible o documentar una frontera del proveedor.

| Familia | Chunk inicial |
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

Futuros/opciones y familias de vida corta usan chunks menores para evitar volumen/API inútil, pero esto no autoriza a cortar cobertura si el proveedor expone histórico adicional relevante.

### Integridad, idempotencia y anti-duplicación — BINDING

Entre batches y al cierre de cada familia se verifican como mínimo: `PRAGMA quick_check=ok`; universo/identidades esperadas; cero grupos duplicados en la clave canónica completa; una sola fila canónica activa por identidad+fecha; hashes/versiones para impedir repetir payload idéntico; provenance/origen; conteos antes/después; fechas mínima/máxima; gaps; errores/empty; y preservación de raw/quarantine sin reinsertarlos en el maestro.

`cu_history_store_v2_hf6.py` evita agregar una versión idéntica cuando ya existe el mismo payload hash para identidad/fecha/fuente y mantiene la superficie canónica deduplicada. Las diferencias legítimas entre fuentes/versiones se conservan como evidencia/versionado y se resuelven por precedencia, no creando múltiples maestros.

### Política incremental posterior

Una vez cerrado el backfill completo: `date_from = last_stored_date - 5 días calendario`, `date_to = hoy`, con merge idempotente. El solapamiento permite capturar correcciones tardías del proveedor sin repetir meses/años completos.

### Auditoría de capacidad y cobertura — EN EJECUCIÓN / SERIALIZADA

Workflow: `RC6 PPI history storage audit 2026-09-12`.
Run base: `34702766005`.

La auditoría es read-only y mide total/usado/libre del filesystem, tamaño de `/opt/porota-trading/data`, observer DB, History Store DB, filas/versiones canónicas, cobertura por familia, tamaños raw/legacy y estado por familia. Exige además `shadow=1960`, `quick_check=ok` y `PRODUCTION_PAPER|0` para el universo certificado actual.

La definición del workflow fue actualizada el 2026-09-12 a la política temporal **3 GiB start / 2 GiB hard stop / 88% guard**, commit `023730072194d97c52aa84ff0137f20219e66611`. El workflow también deja explícito `HISTORY_SCOPE=EXHAUST_PPI_API_AVAILABLE_BY_BACKWARD_CHUNKS`.

### Cleanup no canónico previo — serializado

Antes de la auditoría sigue el cleanup `RC6 noncanonical history 54 quarantine 2026-09-12`, run `34702471513`. No es una nueva ingesta: aparta 54 claves históricas `OPCIONES` no canónicas preservando evidencia en cuarentena. Mientras mantenga el lock no se lanza otro productor.

Los probes confirmaron que los servicios históricos systemd relevantes permanecen `inactive/dead`; el proceso de cleanup mantiene CPU activa y el manifest siguió creciendo, por lo que no había evidencia de cuelgue en el último control.

## Próximo paso exacto

1. Dejar terminar y validar el cleanup de 54 claves no canónicas sin matarlo mientras siga mostrando progreso.
2. Ejecutar la auditoría read-only con la política temporal 3/2 GiB y obtener cobertura real post-reparación.
3. Construir el plan de faltantes de PPI API para las 1.960 identidades certificadas **y** el registro explícito de familias/mercados/endpoints PPI todavía no incorporados al certificado local.
4. Iniciar backfill PPI API por batches/chunks, saltando cobertura existente y retrocediendo hasta agotar el histórico disponible del proveedor.
5. Entre batches: disco, quick_check, duplicados, hashes/versiones, provenance, rango de fechas, gaps y seguridad `PRODUCTION_PAPER|0`.
6. Cerrar API PPI con un manifiesto residual exacto por identidad/campo/fecha.
7. Abrir PPI Web scraping autenticado read-only **sólo** sobre el gap residual y reconciliarlo contra el mismo maestro canónico.
8. Verificar nuevamente el gap total. Sólo lo que siga faltando pasa a `IOL_RESIDUAL`.
9. Con la ingesta completa, medir tamaño/crecimiento real y recién entonces fijar la política definitiva de almacenamiento/retención.

## FASE 3 — PPI WEB SCRAPING — BLOQUEADA HASTA CIERRE API

No comienza hasta cerrar históricos/API. PPI Web trabaja sobre residuales explícitos o campos contractuales/reference faltantes, con provenance `PPI_AUTHENTICATED_WEB`, lock de navegador, read-only y sin auto-habilitar `READY_PAPER`. Su resultado se reconcilia contra el maestro, no crea un segundo datastore maestro.

## FASE 4 — IOL RESIDUAL — ÚLTIMO RECURSO

IOL entra sólo después de medir el gap posterior a PPI API + PPI Web. Cada dato incorporado debe conservar provenance `IOL_RESIDUAL`, pasar los mismos gates de integridad y deduplicación y respetar la precedencia de fuente. No se toma una ingesta IOL como sustituto de una cobertura disponible en PPI.

## Invariantes finales

- `PRODUCTION_PAPER`
- `real_orders_sent=0`
- no order routes
- PPI API > PPI Web > IOL
- objetivo: todo lo que PPI exponga y sea necesario para operar, sin exclusiones silenciosas
- identidad canónica completa `(ticker,instrument_type,market,currency,settlement)`
- un único maestro canónico
- no doble productor
- no silent overwrite
- deduplicación/hash/versionado/provenance obligatorios
- raw y cuarentena preservados fuera del maestro activo
- no reingesta masiva innecesaria
- no rollback automático
- disco protegido temporalmente por gate 3 GiB / hard stop 2 GiB durante la ingesta
- política final de almacenamiento pendiente de medición post-ingesta
