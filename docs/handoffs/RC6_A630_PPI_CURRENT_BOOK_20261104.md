# A630 — PPI Current+Book de rueda 04/11/2026, evidencia sin Deploy

Estado de este documento: **DESARROLLADO / CI pendiente**. La existencia del
Action no demuestra que la consulta PPI haya ocurrido. Producto runtime sigue
anclado en `7e1e744d6ca9ab30c51b5747dd4467af5ece0145` e imagen existente
`sha256:4a8f7306188979fe6976f400209151bd529bf0d9f46f068b50bbc54fc96fd70b`.
Ningún Predeploy, Deploy, reinicio, orden real o PPI Watch se invoca.

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
   mercados o divisas arbitrariamente, A630 exige unicidad del 5-key completo
   en el catálogo PPI físico, y rechaza hermanos con la misma terna API.
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

Un slot, cuando **todos** los guards efectivamente certifican:

1. `main@GITHUB_SHA`, imagen productiva exacta y modo `PRODUCTION_PAPER`,
   `REAL_ORDER_CAPABILITY=BLOCKED`; claves sólo del secreto montado original.
2. Catálogo `financial_instrument_catalog` **fuente PPI** y 5-key único desde
   DB con marker `POROTA_PAPER_V17_FRESH`; controles de TOCTOU: image pin y
   período muy breve entre examen y lectura.
3. Sidecar PPI previamente existente, identidad inode estable, estado OBSERVED,
   `receipt_retention=READY`, EXIT no suspendido y ronda EXIT completa con
   `required_scope_verified_current=True`. El adaptador nunca crea sidecar ni
   activa cuotas. `RuntimePPIBudget` concede dos leases distintos reales.
4. Un único `reader.current()` y luego un único `reader.book()`, invocando el
   guard existente del producto. Reintentos, endpoint ajeno, lote ambiguo,
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
  si falta algún requisito; no hay atajos `budget=False` ni GET extra.
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

19 tests aislados OFFHOST (sin GET): faltante sidecar o credenciales=0,
dos leases, denial/lease nulo/repetido, replay, POST/GET duplicado, forma
Book inválida, reloj futuro/naive/stale, alias 3-key divisa, fuente IOL,
mount RO obligatorio, recibo falsificado, cero órdenes. Compilar script,
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
