# POROTA TRADING — CHECKPOINT INGESTA TOTAL — 2026-09-12

## Estado canónico

Checkpoint operativo vivo. Secuencia BINDING solicitada por el usuario y vigente desde esta actualización:

1. `PPI_API_DISCOVERY_CERT` — descubrir/certificar primero todo el universo argentino expuesto por la API productiva PPI.
2. `PPI_API_HISTORY_CLOSEOUT` — recién con discovery certificado, auditar/recuperar históricos para ese universo exacto, sin dobles ingestas.
3. `PPI_WEB_SCRAPING` — sólo después de agotar API + históricos, usar PPI Web autenticada/read-only para contratos, referencia y residuales históricos necesarios.
4. `IOL_RESIDUAL` — únicamente como tercera fuente para huecos que sigan sin resolver después de PPI API + PPI Web.

No se permite adelantar una fase sobre la anterior.

## Actualización crítica — reparación de sobre-descubrimiento

El `API_CLOSEOUT` anterior produjo una sobre-expansión incorrecta del shadow a 8.422 identidades. El origen fue un discovery demasiado amplio que incorporó 6.462 identidades fuera del conjunto local PPI previamente validado. El gate anti-duplicación detectó la anomalía y bloqueó PPI Web antes de que se usara ese universo como base de scraping.

La reparación se ejecutó en GitHub Actions run `34700865427` (`RC6 ingestion shadow repair quarantine 2026-09-12`) y terminó `SUCCESS`.

Resultado verificado de la reparación:

- preflight: `current=8422`, `trusted=1960`, `extra=6462`, `missing=0`, `errors=0`, `coarse_overlap=0`;
- 6.462 identidades extra fueron puestas en cuarentena y retiradas del shadow activo;
- 4.912 attempts asociados exclusivamente al universo extra fueron puestos en cuarentena y retirados del ledger activo;
- 162 históricos legacy extra fueron puestos en cuarentena y retirados;
- 4.913 clasificaciones del closeout contaminado fueron puestas en cuarentena;
- History Store: 234.521 filas canónicas y 86.620 close-canónicas asociadas al universo extra quedaron apartadas de las vistas activas; las versiones append-only se preservaron y se marcaron, no se destruyeron silenciosamente;
- estado final: `shadow=1960`, `classified_join=1960`, `unclassified=0`, `trusted_coarse_collision_groups=0`;
- `pragma quick_check=ok`;
- seguridad: `PRODUCTION_PAPER|0`;
- `ORDER_ROUTES=NOT_CALLED`, `REAL_ORDERS_SENT=0`.

La evidencia contaminada queda conservada en tablas/ledgers de cuarentena para trazabilidad. No se considera parte del dataset canónico.

Corrección de registro: la afirmación previa de que el primer `Broken pipe` no había dejado mutación no es válida como descripción final del incidente. La evidencia posterior demostró que el proceso remoto continuó y contribuyó al universo sobre-expandido. La reparación anterior es el estado canónico posterior al RCA.

## Hito actual — PPI API DISCOVERY

Universo local esperado y restaurado: **1.960 identidades provider-returned** del mercado argentino expuestas por PPI.

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

ETF local API: los probes BYMA previos respondieron correctamente pero no devolvieron una identidad ETF local normalizada. ETF Web permanece discovery/reference-only hasta reconciliar una identidad local válida; no se inventan identidades sintéticas.

### Certificación fresca en curso

Workflow: `RC6 PPI API universe certification 2026-09-12`.

- primer intento run `34701547019`: `FAIL-CLOSED` porque detectó `porota-history-postclose-rc6.service` activo. No hizo mutación de host ni discovery concurrente;
- forward fix: el workflow ahora espera serialmente a que terminen todos los writers históricos y adquiere ambos locks de histórico antes de certificar;
- run vigente: `34701618310`;
- la certificación es read-only y reconstruye el universo trusted desde el catálogo normalizado local más las búsquedas PPI productivas complementarias usadas y validadas para FCI/licitaciones/taxonomías faltantes;
- condición de éxito: `trusted=1960`, `shadow=1960`, `extra=0`, `missing=0`, duplicados exactos=0, conteos por familia iguales a la tabla precedente, errores de discovery=0, `PRODUCTION_PAPER|0`.

El run vigente está serializado detrás de un writer histórico ya existente (`porota-history-postclose-rc6.service`). No se lo mata ni se lanza una segunda ingesta; se espera su finalización para tomar un snapshot consistente.

**Estado del hito:** `PPI_API_DISCOVERY_CERT = IN_PROGRESS / SERIALIZED`. No se declara cumplido hasta que el run `34701618310` cierre `SUCCESS` con todos los asserts anteriores.

## Fase 2 — PPI API HISTORY CLOSEOUT

No iniciar nuevos backfills de esta fase hasta certificar discovery.

Al abrir esta fase se debe usar exclusivamente el universo certificado de 1.960 identidades. Se prohíbe derivar targets desde `can_simulate` o desde universos operacionales PAPER.

El primer trabajo será una auditoría post-reparación del estado real de históricos, porque los números previos al incidente ya no deben reutilizarse como verdad canónica sin revalidación. Deben medirse nuevamente, por identidad completa y por familia:

- `VALID_PAYLOAD`;
- `PARTIAL`;
- `EMPTY_OR_INVALID`;
- `ERROR` persistente después de retry acotado;
- filas válidas almacenadas;
- cobertura temporal efectiva (no confundir `usable` con 365 días completos).

Luego se reintentan únicamente `ERROR`/faltantes del universo certificado. Un `EMPTY_OR_INVALID` confirmado después de agotamiento API se considera clasificación válida de API y pasa al manifiesto Web; no se vuelve a ejecutar masivamente el resto.

### Regla de clave histórica

La identidad informacional canónica es `(ticker, instrument_type, market, currency, settlement)`.

Las tablas legacy `production_history_attempts`/`production_history` usan una clave más reducida `(symbol, instrument_type, settlement)`. Antes de incorporar históricos Web se debe verificar que esa reducción no colisione entre identidades completas. Si aparece una colisión real, se bloquea la fase y se usa/introduce una superficie canónica v2 con la identidad completa; nunca se resuelve con overwrite silencioso.

## Fase 3 — PPI WEB SCRAPING

Bloqueada hasta completar discovery + históricos API.

PPI Web sólo puede:

- reconciliar contratos/referencia faltante;
- completar residuales históricos explícitos del manifiesto posterior a API;
- preservar `source/provenance` como `PPI_AUTHENTICATED_WEB`;
- funcionar read-only con POST/PUT/PATCH/DELETE prohibidos;
- usar el lock `/run/lock/porota-ppi-web-browser.lock` para impedir dos navegadores concurrentes sobre el mismo perfil.

El servicio weekend `porota-contract-evidence-weekend-backfill-rc6.service` había quedado `failed`, `ExecMainStatus=4`, asociado a sesión autenticada expirada. Debe hacerse RCA/repair de autenticación antes del scraping residual. No se toma la evidencia Web existente como cobertura completa.

PPI Web no habilita `READY_PAPER`, no cambia tradability y no reemplaza automáticamente evidencia PPI API válida.

## Política anti-doble-ingesta e inconsistencia — BINDING

1. Identidad única: `(ticker, instrument_type, market, currency, settlement)`.
2. Discovery no escribe histórico.
3. Histórico API sólo consume el universo discovery certificado.
4. Reintentos son idempotentes sobre la misma identidad; no crean una segunda identidad lógica.
5. PPI Web sólo consume residuales o campos contractuales faltantes; `VALID_PAYLOAD`/`PARTIAL` API no se reingiere como dato Web nuevo.
6. IOL nunca sobrescribe PPI silenciosamente.
7. Toda fuente conserva provenance.
8. Writers API de histórico deben compartir locks; browser collectors deben compartir su lock Web.
9. No hay dos productores simultáneos sobre la misma superficie.
10. Antes de cerrar cada fase: `pragma quick_check=ok`, duplicados exactos=0, integridad de claves verificada, seguridad `PRODUCTION_PAPER|0` y no-order evidence.

## Invariantes de seguridad

- Runtime: `PRODUCTION_PAPER`.
- `real_orders_sent=0`.
- Ninguna tarea de discovery, histórico o scraping puede llamar rutas de órdenes.
- Universo DATA separado del universo operacional/PAPER.
- Scraping o disponibilidad de datos nunca auto-habilita operatoria.
- Host productivo observado: `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`; toda mutación futura debe volver a gatear contra el SHA real del host y contra deploys/writers concurrentes.

## Deuda técnica permanente

`bf_production_paper_observer.py::_historical_targets(store)` todavía acopla el histórico a `can_simulate`/estado operacional. Debe corregirse con tests para separar permanentemente universo DATA de universo PAPER. Los backfills actuales desacoplados permiten completar la data, pero no reemplazan esa corrección de código.

## Próxima transición permitida

`DISCOVERY_CERT SUCCESS` → checkpoint con evidencia exacta → auditoría histórica post-repair del universo 1.960 → retries acotados y clasificación 100% → manifiesto Web residual limpio → RCA de autenticación PPI Web → scraping residual/read-only → sólo después evaluar IOL residual.
