# A630 — PPI Current+Book de rueda 04/11/2026, evidencia sin Deploy

Estado de este documento: **DESARROLLADO / CI pendiente**. La existencia del
Action no demuestra que la consulta PPI haya ocurrido. Producto runtime sigue
anclado en `7e1e744d6ca9ab30c51b5747dd4467af5ece0145` e imagen existente
`sha256:4a8f7306188979fe6976f400209151bd529bf0d9f46f068b50bbc54fc96fd70b`.
Ningún Predeploy, Deploy, reinicio, orden real o PPI Watch se invoca.

**Reconciliación del PR:** rama A630 se creó desde `main@d94027f7...`, que
GitHub continúa exponiendo como `pull_request.base.sha` de #908. El HEAD
actual de main avanzó de modo serial con #905/#907 hasta
`a031275c3dbe67af11ed756843e2616e3c0dd1ac`; no se reescribió la rama
ni se incorporó otro scope. El campo `Base SHA` de la atestación debe tomar
el **SHA real del objeto PR**, no sustituirlo por una inferencia sobre main.

## RCA y separación de hechos

1. La Action A331 capturó *History*, no Current ni Book de rueda, y no valida
   operación causal. La A338 implementó sólo **un Book GET**, SDK digest; no
   prueba un par de Current+Book ni firma independiente de PPI.
2. La versión productiva base NO contiene ScannerPrimaryRecorder del candidato
   #778. El A385 `PPI.present=0` es un reporte sobre JSON exportado, NO
   diagnóstico físico completo de los journals ni prueba de API imposible.
3. `RuntimePPIBudget.acquire()` puede devolver `allowed=true, lease=null` sin
   autoridad activa. A597 vio sidecar PPI ausente en contexto auxiliar: es
   evidencia de bloqueo allí, **NO** certificación del host actual. La reserva
   EXIT y retención también deben estar atestadas justo antes de hacer GET.
4. La identidad API del SDK sólo pide `ticker/type/settlement`; para no colapsar
   mercados o divisas arbitrariamente, A630 replica el guard probado A595/#883:
   exige unicidad del 5-key entre `settlement_source IN ('PPI_FIELD',
   'REQUEST_CANDIDATE')`. Un hermano IOL/BYMA exclusivamente complementario
   no veta el GET PPI; otro hermano PPI sí. `REQUEST_CANDIDATE` es autoridad
   local de selección, **no** procedencia PIT independiente.
5. SQLite `mode=ro` contra WAL activo puede mutar `-shm`. El chequeo de catálogo
   corre exclusivamente en namespace de mount **privado con bind RO del data**;
   si `sudo unshare/mount` falla, no abre la DB ni hace GET.

## FIX, guard y muestreo representativo acotado

| Slot ART 04/11 | UTC | Instrumento solicitado | Filtro obligatorio |
|---|---|---|---|
| 10:45 | 13:45 | GGAL / ACCIONES / BYMA / ARS / A-24HS | catálogo PPI 5-key único |
| 11:20 | 14:20 | AL30 / BONOS / BYMA / ARS / A-24HS | catálogo PPI 5-key único |
| 14:30 | 17:30 | GGAL / ACCIONES / BYMA / ARS / A-24HS | catálogo PPI 5-key único |
| 16:30 | 19:30 | AL30 / BONOS / BYMA / ARS / A-24HS | catálogo PPI 5-key único |

GitHub cron es best-effort: más de 30 minutos tarde ⇒ cero GET. Fallback
`workflow_dispatch` con slot elegido, sólo el 04/11 y hasta 60 minutos después
del slot correspondiente; sin pedir intervención de shell/SSH al usuario.
La misión queda inerte automáticamente otros días (guard de ventana temporal
de esta acción puntual, **no** vencimiento del código RC6).
**Limpieza de cron:** el 05/11 abrir PR control-plane para quitar las cuatro
expresiones `on.schedule` una vez publicados los recibos; conservar el workflow
manual/histórico y no tocar PPI Watch ni ramas productivas. Hasta entonces,
todo miércoles posterior fallará en guard de fecha previo a SSH.

Un slot, cuando **todos** los guards efectivamente certifican:

1. `main@GITHUB_SHA`, imagen productiva exacta y modo `PRODUCTION_PAPER`,
   `REAL_ORDER_CAPABILITY=BLOCKED`; claves sólo del secreto montado original.
2. Catálogo `financial_instrument_catalog` **identidad primaria PPI** y 5-key único desde
   DB con marker `POROTA_PAPER_V17_FRESH`; controles de TOCTOU: image pin y
   período muy breve entre examen y lectura.
3. Sidecar PPI previamente existente, identidad inode estable, estado OBSERVED,
   `receipt_retention=READY`, EXIT no suspendido y ronda EXIT completa con
   `required_scope_verified_current=True`. El adaptador nunca crea sidecar ni
   activa cuotas. `RuntimePPIBudget` concede dos leases distintos reales.
   Ronda EXIT con `recorded_at` UTC con zona y antigüedad máxima **300s**,
   no futura; la afirmación `required_scope_verified_current` debe existir y
   el acquire nativo mantiene su revalidación atómica sobre posiciones actuales.
   Consumidor declarado **`UNSCOPED`** (clase legítima del budget productivo),
   prioridad `DISCOVERY`: nunca se atribuye un consumidor artificial A630 ni
   se imposta cupo EXIT/SCANNER.
4. Un único `reader.current()` y luego un único `reader.book()`, invocando el
   guard existente del producto. URL HTTPS de host exacto puerto 443, sin
   usuario incrustado y query PPI oficial única Ticker/Type/Settlement.
   **Guard adicional antes de `HTTPAdapter.send`** rechaza Search/Estimate/
   Configuration/Orders, redirect, Book2, GET sin lease *started*, retries,
   TLS `verify=False` y cualquier URL no canónica *antes del socket*.
   **Orden de transporte verificado:** instalar strict guard ANTES de construir
   `ProductionMarketReader`; su `ReadOnlyTransportGuard.guarded_send` externo
   adquiere lease nativo, construye/ENTRA `wire_scope`, recién dentro ejecuta
   `start(lease)` y delega al strict pre-wire interno. Por eso nuestro
   `wire_scope` acepta lease adquirido/no finalizado sin exigir `started`
   prematuramente; el strict adapter sí lo exige antes de tocar el socket.
   Cerrar reader/restaurar guard de producto ANTES de desmontar strict (LIFO).
   Reintentos, endpoint ajeno, lote ambiguo,
   lease nulo, body excesivo o reloj nativo sin zona/antiguo/futuro ⇒ HOLD.
5. Dos SHA-256 de los **bytes de respuesta HTTP ya descomprimidos consumidos
   por el SDK** (NO TLS wire ni firma del proveedor), hora nativa de cada
   respuesta, primera observación y cantidad de niveles del Book. Nunca
   se publica Book entero, precios, claves, cabeceras, URL con parámetros,
   cookies ni datos privados.

Máximo teórico por día: 4 slots × (1 Current + 1 Book) = **8 GET**; real
esperado **0–8**, según autoridad efectiva. En caso de un GET exitoso y segundo
rechazado, el recibo registra HOLD, sin publicar un falso par. Login API POST
de autenticación sólo con credenciales existentes y control de rutas.

## EVIDENCIA y límites epistemológicos

Artifact v4 inmutable por `run_id` mientras GitHub lo conserva (90 días
configurados; retención de 365 días #478 sigue pendiente, NO sustituida).

- `EVIDENCE_ONLY_NO_GO` únicamente si 2 leases distintos empiezan/acaban,
  1 Current + 1 Book reales en allowlist, respuesta con native timestamp
  fresco, hashes presentes, checkout/imagen/secretos verificados.
- `NO_VERIFICADO` con código fijo (incluido `SHARED_BUDGET_OR_EXIT_RESERVE_NOT_ATTESTED`)
  si falta algún requisito; el host preflight conserva **sólo razón fija
  sanitizada** en `a630_host_preflight.json`, incluso sin abrir la DB, para
  distinguir ausencia de esquema/fuente/identidad de falta de lease/EXIT.
  No hay atajos `budget=False` ni GET extra.
- Distinción de stoppers del presupuesto sin acceder a provider: sidecar
  `PPI_BUDGET_SIDECAR_ABSENT`, integridad de ruta, snapshot no OBSERVED,
  `PPI_RETENTION_NOT_READY`, `PPI_EXIT_LOWER_SUSPENDED`,
  `PPI_EXIT_ROUND_NOT_ATTESTED`,
  `PPI_EXIT_ROUND_STALE_OR_CLOCK_INVALID`, backend distinto al global real. Estos
  códigos no autorizan activar cuotas ni sustituyen una prueba de lease.
- **Conteo transport indeterminado ≠ cero:** si se pierde SSH/stdio después de
  un Current GET parcial, ambos contadores del artifact son `null` y
  `transport_count_authority=NO_VERIFICADO`. El consumidor jamás deduce
  `0 GET` de una excepción de transporte; sólo el recibo validado determina
  consumos reales, y el sidecar nativo es fuente de presupuesto.
- `source_pit_verified=false`, `independent_provider_signature_verified=false`,
  `paper_sim_verified=false`, `execution_allowed=false` **siempre**. Los hashes
  prueban integridad del dato recibido localmente bajo el SDK/host pin; no
  certifican firma de PPI ni secuencia de órdenes/fills causal.
- El resultado de esta misión no transforma `candidate_identity_v2` ni
  `market_snapshots` en READY, no altera COLD2, retención #478 o PPI Watch.
- Las cuatro fechas/horas/tickers son **solicitudes propuestas**. No afirmar
  cotización presente ni GET ejecutado sin run/artifact de ese slot.

## TEST y aceptación

`python3 -m unittest discover -s tests -p test_rc6_a630_host_ppi_current_book.py -v`

29 tests aislados OFFHOST (sin GET): faltante sidecar o credenciales=0,
dos leases, denial/lease nulo/repetido, replay, POST/GET duplicado, forma
Book inválida, reloj futuro/naive/stale, alias 3-key divisa, fuente IOL,
URL host/puerto/query falsificado, Book2 duplicado, hermano IOL permitido,
hermano PPI denegado y source=PPI_FIELD, mount RO obligatorio,
recibo falsificado, no-wire adapter ante Search/Estimate/Orders/Book2,
ausencia real de sidecar sin creación, clasificación y vigencia EXIT
retención/EXIT, integración sin socket con guard de transporte externo que
adquiere lease nativo sin preadquirir, cierre LIFO, pérdida SSH tras Current
parcial sin falso 0, cero órdenes. Compilar script,
validar workflow YAML, Bash y CI focal.

## Estado runtime abierto al cierre de PR

Falta **Action principal real en main** + run físico de host (inexistente al
momento de desarrollar). Hasta que ocurran, los cuatro slots son
`NO_VERIFICADO`. Queda sujeto a integración SERIAL por DEPLOY_OWNER/control
plane main; no desplegar candidato #778 por este trabajo.

Si persiste `SHARED_BUDGET_OR_EXIT_RESERVE_NOT_ATTESTED`, solución posterior:
certificar en Actions READ_ONLY `runtime_budget_snapshot` y política EXIT de
observador existente; formalizar reserva de dos GET con lease real y retención
sin perjudicar capacidad EXIT. **No** activar sidecar a ciegas ni reducir
cuota EXIT para forzar Current/Book.
