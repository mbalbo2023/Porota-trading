# POROTA TRADING — CHECKPOINT CANÓNICO RC6 DE CONTINUIDAD
## Corte operativo y técnico: 2026-09-08

**Repositorio:** `mbalbo2023/Porota-trading`  
**Rama de checkpoint actual:** `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`  
**Propósito:** permitir abrir una conversación nueva y continuar exactamente desde este punto, sin reconstruir contexto, sin repetir pruebas ya superadas y sin perder pendientes, decisiones, invariantes ni evidencias.

---

# 0. REGLA ABSOLUTA DE CONTINUIDAD

Este documento es el contexto canónico de continuidad de POROTA TRADING RC6 al cierre de esta conversación.

La próxima conversación debe:

1. leer este archivo completo antes de proponer cambios;
2. conectarse a GitHub y verificar el estado actual de las ramas/checkpoints citados;
3. no repetir pruebas que aquí figuran como GREEN salvo que exista una razón de regresión;
4. no inventar estado runtime actual a partir de información histórica;
5. distinguir siempre:
   - estado confirmado históricamente;
   - estado confirmado en el último probe;
   - estado planificado pero todavía no implementado;
6. conservar absolutamente los invariantes de seguridad;
7. mantener `real_orders_sent=0` como condición absoluta mientras RC6 continúe en PAPER/NO-TRADE;
8. continuar desde el **P0 #1: matriz de operabilidad real por API familia por familia**, salvo que un preflight muestre una regresión crítica.

---

# 1. ESTADO EJECUTIVO ACTUAL

## 1.1 Semáforo global

| Área | Estado | Resumen |
|---|---|---|
| Observer / DB | 🟢 GREEN | Último postflight: `ok|PRODUCTION_PAPER|0`; `PRAGMA quick_check=ok` |
| Órdenes reales | 🟢 GREEN SEGURIDAD | `real_orders_sent=0` |
| PPI Cuenta auth | 🟢 GREEN | Login API autenticado |
| PPI SSO Trading | 🟢 GREEN | SSO 200, sesión Trading autenticada |
| PPI trusted device | 🟢 GREEN | `AUTHENTICATED_TRUSTED_DEVICE`, sin 2FA en la prueba validada |
| Browser read-only policy | 🟢 GREEN | mutaciones no autorizadas; terceros abortados; first-party desconocido fail-closed |
| Contract Evidence scheduler | 🟢 GREEN | último runtime: `GREEN_NOT_DUE`, navegador no iniciado |
| Contract Evidence captura actual | 🟡 YELLOW | mecanismo GET/XHR antiguo ya no captura endpoints contractuales; DOM sí funciona |
| PPI DOM autenticado | 🟢 GREEN técnico | 16/16 familias autenticadas y capturables |
| Cobertura DOM total | 🟡 YELLOW | 12 familias posiblemente truncadas en 50 filas; 4 `LIKELY_COMPLETE`, aún no demostradas contra universo |
| Opciones DOM | 🟡 YELLOW | 50 renderizadas / 43 únicas; identidad/duplicación pendiente |
| API operabilidad por familia | 🔴 NO PROBADO | crítico: visible/scrapeable NO equivale a ejecutable por API |
| DOM Contract Evidence V1 | 🟡 PENDIENTE | diseño confirmado, integración todavía no realizada |
| Dashboard > Scraping semáforo | 🟡 PENDIENTE | requisito definido, implementación pendiente |
| Alerting staleness/coverage | 🟡 PENDIENTE | requisito definido, implementación pendiente |
| PPI Web History SHADOW | 🟡 PENDIENTE | ejecutar después de cerrar Contract Evidence seguro/cobertura |
| History Store completeness/price-basis | 🟡 PENDIENTE DE PROMOCIÓN | rama de hardening existente; validar antes de promover |
| Evidence Architecture | 🟡 DISEÑO APROBADO | arquitectura definida; migración física futura |
| Disk cleanup | 🟡 PENDIENTE CONTROLADO | solo limpieza con evidencia; no borrar `historical_raw_archive` prematuramente |
| SRE/introspección | 🟡 PARCIAL | quick-health corregido; RCA de latencia y panel completo siguen pendientes |
| MFE/MAE / Forward Lab / Event Risk | 🟡 PENDIENTE | después de cerrar data/evidence readiness |
| READY_PAPER por familia | 🔴 NO | ninguna familia debe promoverse por el solo DOM |
| READY_PROD | 🔴 NO-GO | producción real no autorizada |

---

# 2. INVARIANTES ABSOLUTOS

## 2.1 Trading y seguridad

- `real_orders_sent=0` es invariante absoluto.
- RC6 permanece en PAPER/SIMULATED; producción real continúa NO-GO.
- No ejecutar pruebas de red de rutas de orden.
- No usar browser scraping para comprar, vender, cancelar, modificar, caucionar, suscribir FCI, operar opciones/futuros o cualquier otra operación.
- Browser PPI: read-only.
- Nunca guardar ni imprimir credenciales, contraseña, OTP, cookies, tokens, headers `Authorization` o secretos.
- Si aparece 2FA, no evadirlo.
- Cualquier mutación first-party PPI no explícitamente reconocida como telemetría/soporte debe seguir `ABORT + RECORD + FAIL-CLOSED`.
- Terceros de telemetría/soporte pueden ser abortados silenciosamente; nunca autorizados.
- `ORDER_ROUTES_VISITED=NO` debe mantenerse en los sweeps de evidencia.
- No confundir `browser authenticated` con `API execution authorized`.
- No promover una familia a `READY_PAPER` sin prueba separada de capacidad API y datos contractuales suficientes.

## 2.2 Operación y despliegue

- No asumir que un cambio host-local existe en GitHub.
- No asumir runtime SHA sin preflight.
- Toda modificación de host debe tener backup, validación y rollback cuando corresponda.
- Preferir GitHub Actions → SSH estricto para despliegues normales.
- La intervención manual debe ser excepcional y en scripts `.sh` listos para subir/ejecutar.
- No usar `docker system prune`.
- No hacer cleanup destructivo amplio.
- No `VACUUM`, migraciones destructivas ni eliminación de evidencia sin equivalencia demostrada.
- No borrar `historical_raw_archive` hasta completar arquitectura de evidencia y reconciliación.
- No mezclar un deploy de almacenamiento con un deploy del observer.
- No reiniciar observer simplemente porque exista un warning.
- No improvisar HF7 ni tocar contracts/risk salvo necesidad demostrada.

## 2.3 Ergonomía operativa

- Para acciones manuales en host: entregar preferentemente un solo archivo `.sh` descargable.
- El `.sh` debe emitir resumen compacto y, si es posible, copiarlo al portapapeles vía OSC52.
- Directorio práctico usado: `/home/porotaadmin/`.
- Evitar heredocs largos pegados manualmente en Termius porque se comprobó que pueden corromperse.
- No usar ZIP salvo pedido expreso.

---

# 3. REPOSITORIO, RAMAS Y COMMITS CANÓNICOS RELEVANTES

## 3.1 Checkpoints / evidencia

- `checkpoint/rc6-20260906-posthotfix`
  - SHA conocido: `103158fb08fdf6417ced8d8747a6ae453f74341c`
  - checkpoint previo de RC6 postdeploy/PPI Web.
- `checkpoint/rc6-empirical-evidence-architecture-20260907`
  - SHA conocido: `604d575a0d0bd81f329b2b290d1cac7d95a1c504`
  - arquitectura de evidencia empírica.
- `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`
  - checkpoint actual.
  - `POROTA_TRADING_CHECKPOINT_PPI_DOM_API_OPERABILITY_ALERTING_2026-09-08.md`
    - commit: `82ebeba4df112290f46eabe983d6897ba6323b15`
  - `POROTA_TRADING_CHECKPOINT_PPI_DOM_FULL_SWEEP_2026-09-08.md`
    - commit: `11d663963057c87b387cab6f4c47f4083f90808f`

## 3.2 Ramas técnicas relevantes

- `fix/rc6-history-partial-semantics-20260907`
  - SHA conocido: `c17b0d777ba49d88c53c5a7ed14218d8eaa94638`
  - último baseline runtime confirmado históricamente antes de esta sesión; **no asumir que sigue siendo HEAD live sin preflight**.
- `fix/rc6-history-store-completeness-price-basis-20260907`
  - SHA: `34dcdabe8d643a61257756cddd93af2189f37b96`
  - hardening History Store.
- `hotfix/rc6-cauciones-official-live-20260907`
  - SHA: `6f47ec943...` (usar GitHub para resolver SHA completo si se necesita).
- hotfix CEDEAR US Labor Day:
  - SHA conocido: `db26c76723bb988c956589c572b87cbcb4191731`.
- auth/Contract Evidence branch:
  - `hotfix/rc6-contract-evidence-auth-api-20260907`
  - tip conocido previamente: `de6443ee0aa1403c993be38f65670ef87f5a43e6`.
- rama creada durante diagnóstico auth:
  - `hotfix/rc6-contract-evidence-auth-endpoint-20260907`.
- diagnóstico classifier:
  - `diag/rc6-ppi-web-auth-classifier-20260907`
  - tip conocido: `60b657210d58e9100c01518d85faf13fbbf692e9`.
- otras ramas de auditoría/operación registradas:
  - `audit/rc6-critical-health-20260907`
  - `audit/rc6-disk-usage-20260907`
  - `audit/rc6-historical-raw-architecture-20260907`
  - `audit/rc6-ppi-history-coverage-20260907`
  - `cleanup/rc6-disk-safe-20260907`
  - `closure/rc6-critical-control-plane-20260907`
  - `control-plane/rc6-critical-approval-canonical-20260907`

---

# 4. PPI WEB AUTHENTICATION — HISTORIA Y ESTADO FINAL

## 4.1 Problema inicial

El Contract Evidence browser estaba fallando por sesión/auth PPI.

Se investigó el frontend read-only y se descubrieron endpoints first-party de autenticación, incluyendo:

- `/api/Seguridad/Auth/User`
- `/api/Seguridad/Auth/Login`
- `/api/Seguridad/Auth/ValidateOnboardingToken`
- `/api/Seguridad/Auth/User/`
- `/api/Seguridad/Auth/RefreshToken`
- `/Seguridad/Auth/ValidateUser2FA`
- `/Seguridad/Auth/Resend2FAToken`
- `/api/v1/Auth/Get2FA`

El host real del login resultó ser:

`api.portfoliopersonal.com/api/Seguridad/Auth/Login`

Payload observado de frontend:

- `usuario`
- `clave`

Nunca se debe almacenar el valor de la contraseña en GitHub/checkpoints.

## 4.2 Secuencia empírica

1. Endpoint asumido inicialmente: falló.
2. Network trace encontró el host real.
3. Clasificador sanitizado detectó primero credenciales inválidas.
4. Luego hubo `ACCOUNT_LOCKED`.
5. Reintento posterior:
   - `STATUS=AUTHENTICATED_API_SUCCESS`
   - `HTTP_STATUS=200`
   - `TOKEN_PRESENT=True`
   - `TWOFA=False`
   - `PAGE_URL=https://cuenta.portfoliopersonal.com/cuentas`
6. SSO Trading:
   - `ACCOUNT_AUTH_USED=True`
   - `ACCOUNT_HTTP_STATUS=200`
   - `SSO_REQUEST_SEEN=True`
   - `SSO_RESPONSE_SEEN=True`
   - `SSO_HTTP_STATUS=200`
   - `TRADING_AUTHENTICATED=True`
   - `FINAL_URL=https://trading.portfoliopersonal.com/estadoDeCuenta`
   - `AUTHENTICATED_TRUSTED_DEVICE`
   - `real_orders_sent=0`

## 4.3 Estado actual

- PPI Cuenta auth: GREEN.
- SSO: GREEN.
- Trading browser session: GREEN.
- Trusted device: GREEN.
- 2FA no apareció en la validación final.
- No guardar credenciales/tokens en repo.
- Si en el futuro aparece 2FA: fail-closed y flujo legítimo; nunca bypass.

---

# 5. CONTRACT EVIDENCE — POLÍTICA READ-ONLY ACTUAL

## 5.1 Problema detectado

El collector original abortaba cualquier método fuera de `GET/HEAD/OPTIONS` y además contaba todo como `blocked_nonread`, provocando RED incluso por telemetría irrelevante.

Se observaron secuencialmente:

- `POST trading.portfoliopersonal.com/api/logger`
- `POST api.refiner.io/.../identify-user`
- `POST portfoliopersonalsupport.zendesk.com/frontendevents/pv`
- `POST api2.amplitude.com/2/httpapi`
- `POST k.clarity.ms/collect`
- `POST o.clarity.ms/collect`
- variantes adicionales de Clarity (`f.`, `u.`, `j.`, `n.`)
- `POST api.portfoliopersonal.com/api/v1/zendesk/zendesk-session`
- `POST portfoliopersonalsupport.zendesk.com/sc/sdk/v2/apps/.../login`

## 5.2 Política V3 validada en host

La política que quedó validada:

- `GET/HEAD/OPTIONS`: permitidos.
- cualquier mutación hacia terceros: abortada silenciosamente, nunca autorizada;
- first-party PPI:
  - `/api/logger`: abort silencioso;
  - `/api/v1/zendesk/zendesk-session`: abort silencioso;
  - cualquier otra mutación: abort + `blocked_nonread` + fail-closed.

Resultado:

`POROTA RC6 CONTRACT EVIDENCE NONREAD POLICY V3`

- `STATUS=GREEN`
- `PATCH_STATE=WRITTEN`
- `THIRD_PARTY_MUTATIONS_ALLOWED=NO`
- `THIRD_PARTY_MUTATIONS_ABORTED=YES`
- `OTHER_PPI_MUTATIONS=FAIL_CLOSED`
- `PROBE_AUTH=AUTHENTICATED_TRUSTED_DEVICE`
- `PROBE_BLOCKED_NONREAD=0`
- `PROBE_REAL_ORDERS_SENT=0`
- `OBSERVER_BEFORE=ok|PRODUCTION_PAPER|0`
- `OBSERVER_AFTER=ok|PRODUCTION_PAPER|0`

### IMPORTANTE

Esta política fue **parcheada y validada host-localmente** en:

`/usr/local/lib/porota-contract-evidence-rc6/rc6_trusted_browser_contract_collector.py`

No debe asumirse que ese V3 exacto ya está versionado en GitHub. **PENDIENTE P0:** portar la política probada al repo de forma limpia, revisar diff y recién después desplegarla por el flujo canónico.

Backup host fue creado automáticamente por los scripts antes de parches.

---

# 6. CONTRACT EVIDENCE SCHEDULER / DB

## 6.1 Último runtime

Después de la política V3:

- `CONTRACT_EVIDENCE_RC=0`
- `CONTRACT_EVIDENCE_STATE=COMPLETED`
- `CE_RUNS_BEFORE=84`
- `CE_RUNS_AFTER=84`

Diagnóstico posterior:

- `STATUS=GREEN_NOT_DUE`
- `AUTH_BROWSER_STARTED=NO`
- `REAL_ORDERS_SENT=0`

Conclusión:

**No aumentar de 84 a 85 fue normal** porque el job no estaba `DUE`; el runtime no abrió browser.

## 6.2 DB Contract Evidence

- `DB_QUICK_CHECK=ok`
- `MODE=PRODUCTION_PAPER`
- `REAL_ORDERS_SENT=0`
- `CE_RUNS=84`

Últimos runs vistos seguían siendo del `2026-09-04` y estado `AMARILLO`.

Ejemplos:

- `CONTRACT_EVIDENCE_AUCTIONS`: 121 records / 120 changed / 0 conflicts / AMARILLO
- `CONTRACT_EVIDENCE_CAUCIONES`: 121 / 120 / 0 / AMARILLO
- `CONTRACT_EVIDENCE_DERIVATIVES`: 247 / 246 / 0 / AMARILLO
- `CONTRACT_EVIDENCE_DYNAMIC`: 247 / 246 / 0 / AMARILLO

No interpretar `GREEN_NOT_DUE` como evidencia nueva importada.

---

# 7. DESCUBRIMIENTO: GET/XHR ANTIGUO YA NO SIRVE PARA STATIC

El collector original buscaba respuestas GET cuyos URLs incluyeran:

- `InstrumentosOperables`
- `CaucionesOperables`
- `ConfiguracionOperatoriaSimplificada`
- `SubyacenteOpciones`
- `DatosTecnicos`

En un trace autenticado moderno se observaron 31 eventos GET first-party, pero ninguno de esos endpoints contractuales antiguos.

GET comunes observados:

- `api.portfoliopersonal.com/api/Configuracion/Servicios/GetServiciosAplicaciones`
- `api.portfoliopersonal.com/api/Cuenta/ComitentesAsignados`
- `api.portfoliopersonal.com/api/TranYDep/BuscarDepositoAutomaticoByUserId`
- `api.portfoliopersonal.com/api/v1/notifications`
- documentos `/Cotizaciones/...`
- Zendesk GET

Resultado:

- 6/6 rutas autenticadas.
- `FIRST_PARTY_GET_EVENTS=31`
- `UNIQUE_FIRST_PARTY_GET=11`
- endpoints contractuales buscados: 0.

Conclusión:

**El mecanismo XHR/GET histórico del collector STATIC está obsoleto para estas páginas.**

---

# 8. WEBSOCKET + DOM — HALLAZGO ARQUITECTÓNICO

Trace autenticado:

- `WEBSOCKET_UNIQUE=1`
- endpoint:
  - `realtime-hw.portfoliopersonal.com/socket.io/`
- `WEBSOCKET_OPENED=6`
- `WEBSOCKET_RECV_FRAMES=12`
- `WEBSOCKET_RECV_BYTES=528`

Comparado con tablas DOM completas de 30–50 filas, el volumen recibido por WS fue mínimo.

Conclusión:

- WebSocket existe y puede ser útil para realtime;
- para Contract Evidence estático, **el DOM autenticado es hoy la fuente primaria viable**;
- XHR/WebSocket deben quedar como fuentes complementarias cuando aporten algo que el DOM no exponga.

---

# 9. DOM STATIC SNAPSHOT — 6 FAMILIAS

Prueba:

`POROTA RC6 PPI DOM STATIC SNAPSHOT`

Resultado:

- `FAMILIES=6`
- `AUTHENTICATED_FAMILIES=6`
- `REAL_ORDERS_SENT=0`
- primer `<table>` solamente
- navegación global no capturada
- input values no capturados
- ningún click en `Operar`
- DB writes: NO

Familias y campos:

## ACCIONES
50 filas observadas.

Headers:
- ESPECIE
- ÚLTIMO PRECIO
- VAR.
- CANT. COMP.
- PRECIO COMP.
- PRECIO VENTA
- CANT. VENTA
- VOLUMEN
- APER.
- MÍN.
- MÁX.
- CIERRE ANT.
- ÚLTIMA ACT.

## CEDEARs
50 filas.

Lo anterior +:
- RATIO

## BONOS
50 filas.

Incluye:
- TIR
- Mod.Duration
- OHLC
- bid/ask
- volumen

## LETRAS
30 filas.

Incluye:
- FECHA VTO.
- TNA

## ON
50 filas.

Incluye:
- TIR
- Mod.duration

## FCI
50 filas.

Incluye:
- NOMBRE
- CATEGORÍA
- MONEDA
- PLAZO RESCATE
- HORARIO LÍMITE
- REND. 30 DÍAS
- REND. AÑO
- REND. 12 MESES
- RIESGO
- PATRIMONIO

---

# 10. FULL DOM SWEEP — 16 FAMILIAS

Prueba final de esta conversación:

`POROTA RC6 PPI DOM FULL SWEEP + COVERAGE`

UTC:
`20260908T040124Z`

Seguridad:

- `OBSERVER_PREFLIGHT=ok|PRODUCTION_PAPER|0`
- `FAMILIES=16`
- `AUTHENTICATED_FAMILIES=16`
- `BLOCKED_FIRST_PARTY_NONREAD=0`
- `BLOCKED_THIRD_PARTY_NONREAD=224`
- `REAL_ORDERS_SENT=0`
- `ORDER_ROUTES_VISITED=NO`
- `DB_WRITES=NO`
- `CONTRACT_EVIDENCE_IMPORT=NO`
- postflight intacto.

## 10.1 Cobertura observada

| Familia | Inicial | Únicas | Estado |
|---|---:|---:|---|
| FCI | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| FCI Exterior | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Acciones | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Acciones USA | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Bonos | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Cauciones | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| CEDEARs | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| ETF | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Futuros | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Letras | 30 | 30 | LIKELY_COMPLETE |
| Licitaciones | 3 | 3 | LIKELY_COMPLETE |
| ON | 50 | 50 | POSSIBLY_TRUNCATED_50 |
| Opciones | 50 renderizadas | 43 únicas | POSSIBLY_TRUNCATED_50 + identidad/duplicados pendiente |
| Índices | 17 | 17 | LIKELY_COMPLETE |
| Monedas | 30 | 30 | LIKELY_COMPLETE |
| Tasas | 50 | 50 | POSSIBLY_TRUNCATED_50 |

### Regla

`LIKELY_COMPLETE != COMPLETE`.

Hay que reconciliar contra universo esperado/API/otra fuente antes de declarar cobertura total.

## 10.2 Campos por familias adicionales

### Cauciones
- cantidad de días
- último operado
- variación
- monto tomador
- TNA tomadora
- TNA colocadora
- monto colocador
- volumen
- fecha vencimiento
- OHLC
- cierre anterior
- última actualización

### Futuros
- especie/contrato
- último
- bid/ask y cantidades
- volumen
- OHLC
- ajuste anterior
- última actualización

### Licitaciones
- nombre
- inversión mínima
- moneda/especie
- tasa
- plazo
- fecha fin
- estado

### Opciones
- tabla de mercado visible;
- identidad completa todavía pendiente:
  - subyacente
  - vencimiento
  - strike
  - call/put
  - moneda/mercado
  - deduplicación

### Índices / Monedas / Tasas
Útiles como contexto y evidencia; no asumir que son instrumentos ejecutables.

---

# 11. P0 CRÍTICO #1 — MATRIZ DE OPERABILIDAD REAL POR API

## 11.1 Pregunta canónica

**¿Cada una de las 16 familias visibles/scrapeables en PPI Web puede realmente operarse mediante la API que utilizará POROTA?**

Aún NO está respondida.

Esto es crítico porque POROTA debe ejecutar vía API, no vía browser.

## 11.2 Estados separados obligatorios

Por familia:

- `WEB_VISIBLE`
- `DOM_EXTRACTABLE`
- `API_SEARCHABLE`
- `API_MARKETDATA`
- `API_HISTORY`
- `API_ORDER_SUPPORTED`
- `API_CANCEL_SUPPORTED`
- `API_MODIFY_SUPPORTED` si existe
- `SETTLEMENT_SUPPORTED`
- `CURRENCY_MARKET_SUPPORTED`
- `CONTRACT_FIELDS_SUFFICIENT`
- `READY_PAPER`
- `READY_PROD`

## 11.3 Matriz requerida

Para:

- Acciones
- CEDEARs
- Bonos
- Letras
- ON
- Cauciones
- Opciones
- Futuros
- ETF
- FCI
- FCI Exterior
- Acciones USA / Exterior
- Licitaciones
- Índices
- Monedas
- Tasas
- cualquier familia adicional descubierta

Cada familia debe quedar:

- `EXECUTABLE`
- `PARTIAL`
- `NON_EXECUTABLE`
- `NOT_PROVEN`

### Reglas

- no inferir ejecutabilidad por la presencia de botón `Operar` en PPI Web;
- no usar browser para probar órdenes;
- la auditoría debe empezar por SDK/código instalado, endpoints/documentación y llamadas read-only;
- si una familia no es ejecutable por API:
  - puede seguir siendo fuente de contexto/análisis;
  - debe quedar explícitamente `NON_EXECUTABLE_VIA_API`;
  - jamás debe generar orden.

---

# 12. P0 CRÍTICO #2 — RESOLVER COBERTURA >50

La repetición de 50 filas indica probable limitación de UI/tabla.

No se detectó:

- crecimiento por scroll;
- paginación `Siguiente/Next`.

Pendiente investigar read-only:

1. search/filter por ticker;
2. particionado por prefijo;
3. endpoint de búsqueda read-only;
4. universo de instrumentos expuesto por API;
5. estado interno del frontend;
6. comparación con universe/history store.

No importar snapshot de 50 filas como universo total.

Especial:
- Opciones: resolver 50 renderizadas / 43 únicas.

---

# 13. P0 CRÍTICO #3 — VERSIONAR EN GITHUB LA POLÍTICA V3 PROBADA

La política read-only V3 funciona en host, pero debe quedar versionada.

Pendiente:

1. recuperar el archivo instalado host actual;
2. comparar contra `hotfix/rc6-contract-evidence-auth-api-20260907`;
3. aplicar cambio mínimo limpio;
4. tests unitarios:
   - third-party POST abort silencioso;
   - logger/zendesk first-party abort silencioso;
   - otro first-party POST => bloqueado y registrado;
   - GET permitido;
5. CI;
6. deploy transaccional;
7. E2E con `real_orders_sent=0`;
8. checkpoint SHA.

No desplegar a ciegas copiando host-local sin diff.

---

# 14. P0 CRÍTICO #4 — DOM CONTRACT EVIDENCE V1

Diseño confirmado, implementación pendiente.

## 14.1 Fuente

- primaria STATIC: DOM autenticado;
- complementarias:
  - GET/XHR cuando exista semántica útil;
  - WebSocket solo para realtime/evidencia dinámica;
  - API para reconciliación y ejecutabilidad;
  - History Store para pasado/provenance.

## 14.2 Requisitos

- `NO_AUTO_ACTIVATION`;
- provenance por campo;
- timestamp captura;
- hash/schema;
- coverage observada/esperada;
- campos críticos ausentes;
- versión del extractor;
- row identity robusta;
- evitar PII;
- ningún dato de cuenta/saldo;
- ninguna acción browser;
- no inferir tick/step de decimales.

## 14.3 Normalizador actual

Regla vigente del normalizador:

- `quantity_decimal_places` NO implica `quantity_step`;
- `price_decimal_places` NO implica `price_tick`;
- `order_quantity_step=None` mientras no exista evidencia;
- `order_price_tick=None` mientras no exista evidencia.

Mantener este guard.

---

# 15. P0 CRÍTICO #5 — DASHBOARD > SCRAPING / CONTRACT EVIDENCE

Pendiente solicitado explícitamente.

## 15.1 Semáforo de captura

Por fuente/familia:

- AUTH
- LAST_SUCCESS
- AGE
- SLA/TTL
- SCHEMA/HASH
- COVERAGE
- ROW_COUNT
- EXPECTED_UNIVERSE
- SOURCE
- FALLBACK

## 15.2 Semáforo de readiness operativa

Separado del scraper:

- API_EXECUTABLE
- MARKET_DATA_FRESH
- CONTRACT_FRESH
- HISTORY_FRESH
- SETTLEMENT_KNOWN
- FEES_KNOWN
- RATIO_KNOWN
- MATURITY_KNOWN
- TICK_STEP_KNOWN
- FAMILY_BLOCKED
- BLOCK_REASON

### Semántica

Una fuente puede ser GREEN como scraper y la familia RED para ejecución.

---

# 16. P0 CRÍTICO #6 — ALERTING POR STALENESS / PÉRDIDA DE INFORMACIÓN

No alcanza con UI.

Estados canónicos sugeridos:

- `STALE_BUT_USABLE`
- `STALE_NOT_SAFE_FOR_DECISION`
- `SOURCE_DOWN_FALLBACK_OK`
- `SOURCE_DOWN_NO_FALLBACK`
- `SCHEMA_DRIFT`
- `COVERAGE_GAP`
- `API_EXECUTION_UNSUPPORTED`

## 16.1 Severidad

### INFO
- un fallo aislado;
- fallback sano;
- dato no participa en decisión activa.

### YELLOW
- varios fallos;
- dato excede TTL normal;
- cobertura degradándose;
- schema drift;
- reconciliación pendiente;
- aún hay evidencia suficiente.

### RED
- dato contractual crítico excede `max_staleness`;
- no hay fallback;
- dato necesario para sizing/precio/settlement/fees/maturity/ratio/tick/step/elegibilidad/riesgo;
- discrepancia material no resuelta;
- el motor no puede demostrar decisión segura.

## 16.2 Acción RED

- fail-closed selectivo por familia/instrumento;
- bloquear nuevas órdenes de la familia;
- mantener market data/history/backfill/observer/dashboard/introspección;
- Telegram técnico con:
  - causa;
  - última captura válida;
  - edad;
  - fallback;
  - acción tomada;
- deduplicar alertas;
- recovery explícito.

---

# 17. NO-TRADE 2026-09-08

Decisión operativa canónica:

- no operar durante 2026-09-08;
- ingesta debe continuar.

Objetivo de estado:

- `TRADING=HARD_BLOCKED`
- `NEW_POSITIONS=BLOCKED`
- `PAPER_FILLS=BLOCKED`
- `BROKER_ORDER_ROUTES=BLOCKED`
- `MARKET_DATA=RUNNING`
- `HISTORICAL_INGESTION=RUNNING`
- `BACKFILL=RUNNING`
- `CONTRACT_EVIDENCE=RUNNING`
- `PPI_WEB_HISTORY_SHADOW=RUNNING`
- `OBSERVER=RUNNING`
- `DASHBOARD=RUNNING`
- `REAL_ORDERS_SENT=0`

### IMPORTANTE

La **decisión NO-TRADE está aprobada**, pero en esta conversación no se demostró todavía un **date-scoped hard execution gate implementado en código/runtime**.

No confundir la decisión operativa con una implementación probada.

Pendiente:
- inspeccionar frontera central de ejecución;
- agregar gate fecha Argentina;
- bloquear paper fills y cualquier ruta de orden;
- mantener análisis/signal SHADOW y todas las ingestas.

No usar simplemente `ORDER_EXECUTION_MODE=confirm` como sustituto: confirm espera aprobación, no constituye necesariamente hard block.

---

# 18. HISTÓRICOS / HISTORY STORE

## 18.1 Estado previo confirmado

History Store v2 estaba operativo con:

- `PRAGMA quick_check=ok`;
- decenas de miles de versiones (~48k en auditoría previa);
- cobertura canónica PPI para Acciones y CEDEARs llegando a 2026-09-07;
- ejemplos de evidencia canónica incluyendo CEPU/AAPL.

## 18.2 Semántica corregida

Estados de ingestión:

- `VALID_PAYLOAD` = completo;
- `PARTIAL` = evidencia usable YELLOW, `hard_failure=NO`;
- `EMPTY_OR_INVALID` / `ERROR` = hard failure.

## 18.3 Hardening pendiente

Rama:

`fix/rc6-history-store-completeness-price-basis-20260907`
SHA `34dcdabe8d643a61257756cddd93af2189f37b96`

Invariante obligatorio:

- `FULL_OHLCV` no debe ser desplazado por `CLOSE_ONLY` solo por prioridad de fuente;
- RAW y ADJUSTED deben ser identidades distintas;
- settlement mismatch / identity mismatch => fail-closed;
- provenance completo.

Validar y promover en flujo seguro.

---

# 19. PPI WEB HISTORY SHADOW

Pendiente después de cerrar Contract Evidence.

Objetivo:

- usar sesión PPI Web autenticada;
- inventariar endpoints/history/UI para histórico;
- profundidad disponible;
- OHLCV;
- settlement;
- RAW vs ADJUSTED;
- paginación;
- familias;
- source provenance.

Reglas:

- SHADOW;
- `canonical_write=DENY`;
- `db_write=NO` inicialmente;
- no mezclar con History Store canónico hasta cerrar hardening;
- no órdenes.

---

# 20. CALENDARIOS / CEDEAR US HOLIDAY

Se trabajó el problema de doble calendario para instrumentos con subyacente/mercado USA.

Hotfix conocido:
`db26c76723bb988c956589c572b87cbcb4191731`

Principio:

- BYMA abierto no implica que todo CEDEAR deba operar si el mercado/subyacente relevante está cerrado;
- no limitar la validación a AAPL: debe cubrir todos los CEDEARs;
- mantener calendario BYMA + calendario del mercado externo relevante;
- bloquear la familia/instrumento cuando corresponda.

Debe permanecer en regresión antes de READY_PAPER.

---

# 21. CAUCIONES

Estado: YELLOW / no READY_PAPER.

Se descubrió naming oficial read-only por SearchInstrument:

- `PESOS{dias}`
- `DOLAR{dias}`

Pendientes:

- vencimiento exacto;
- day-count;
- rounding;
- quantity step;
- mínimos;
- depth/pagination;
- Budget semantics;
- saldo DOLAR semantics;
- settlement;
- fees;
- validación API de ejecución;
- reconciliación DOM/API/Contract Evidence.

`CAUCIONES_AUTO_PLACEMENT=false` debe mantenerse hasta cerrar todo.

---

# 22. IOL / A3

IOL se mantiene como:

- read-only;
- shadow;
- fallback/cross-check secundario.

Regla:
`IOL_CANONICAL_WRITE_NOT_AUTHORIZED`

No promover IOL como fuente canónica sin decisión explícita.

A3 también debe mantenerse como evidencia/fallback según wiring existente.

REST 401/A3 fallback siguen como pendientes secundarios.

---

# 23. EVIDENCE ARCHITECTURE

Checkpoint:
`604d575a0d0bd81f329b2b290d1cac7d95a1c504`

Principio:
**maximizar evidencia empírica e historial; no perder evidencia única.**

## Arquitectura objetivo

### HOT — observer DB
- estado operacional;
- paper ledger;
- decisiones;
- riesgo;
- health;
- referencias.

### WARM — History Store
- versiones append-only;
- canonical;
- reconciliación.

### RAW Evidence Store
- payloads únicos content-addressed.

### Manifests
- inmutables;
- source/revision/hash/provenance.

### Learning evidence
- datos conocidos → decisión → razón → outcome.

## Ingestion Coordinator único

Modos:

- BOOTSTRAP
- INCREMENTAL + overlap
- RECONCILIATION

## Fases

### A
contener nuevas escrituras fuera del observer DB; no borrar legacy.

### B
shadow/reconcile legacy read-only.

### C
solo con equivalencia demostrada:
- dedupe contenido físicamente redundante;
- eliminar blobs redundantes;
- compactar.

No hacer dedupe heurístico.

---

# 24. STORAGE / DISK

Auditorías existentes:

- `audit/rc6-disk-usage-20260907`
- `audit/rc6-historical-raw-architecture-20260907`
- `cleanup/rc6-disk-safe-20260907`

Dato histórico relevante:

`historical_raw_archive` en `observer_v17.db` había llegado aproximadamente a:
- ~1.266 GB
- ~25,860 rows
- `origin='PPI_HISTORY'`

Muchos payloads representaban snapshots completos de histórico con distintas `attempted_at`.

Regla:

- no borrar hasta que Evidence Architecture haya movido/normalizado evidencia;
- cleanup solo targeteado;
- hash/ref proof;
- no perder revisiones, conflictos, errores, manifests ni learning evidence.

---

# 25. SRE / INTROSPECCIÓN

Objetivo pendiente:

- introspección periódica / early warning;
- semáforo;
- Telegram solo para incidentes materiales;
- dashboard `/vivo` / salud;
- evitar spam.

Debe cubrir como mínimo:

- observer/dashboard;
- DB/heartbeat;
- `real_orders_sent=0`;
- modo/sesión;
- scheduler;
- PPI auth/consumo/errores/rate limit;
- histórico/backfill/source_sync;
- timestamps/staleness;
- coverage/gaps/fallidos;
- APIs;
- todos los YELLOW;
- distinguir warning informativo de incidente accionable.

Trabajo previo:

- quick-health se separó de `PRAGMA quick_check` pesado.
- permanece pendiente RCA de latencia/introspection y panel consolidado.

---

# 26. DASHBOARD / UX / OPERACIÓN

Pendientes acumulados:

- submenú Scraping / Contract Evidence con semáforo;
- `/vivo` con motor de introspección;
- drill-down de operaciones abiertas/cerradas;
- P&L actual por operación con semáforo;
- fecha/hora exacta última actualización/mark;
- mostrar modo actual:
  - sandbox
  - simulación
  - producción
- mostrar APIs/fuentes usadas;
- health por API;
- cambios rápidos de modo documentados;
- accesibilidad tablet/Voice Access;
- tablas responsive al ancho;
- controles grandes/claros;
- minimizar interacción manual.

---

# 27. MOTOR DE DECISIÓN / PAPER / RIESGO

Principios acumulados:

- MOTOR_DECISION=PYTHON.
- IA intradiaria desactivada.
- IA solo post-jornada para lecciones/aprendizaje, si se usa.
- bot conservador / baja frecuencia.
- no arbitraje de microsegundos.
- mantener señal/decisión SHADOW aunque una familia esté bloqueada, pero no ejecución.

Pendientes:

- MFE/MAE ejecutable + provenance;
- Forward Lab v2;
- walk-forward;
- robustness;
- Event Risk:
  - preopen
  - open
  - close
  - postclose
  - backtests
- sector/correlations/family normalization;
- cohorts/version separation;
- PAPER/SHADOW campaign;
- concentración sectorial binding según decisión previa;
- revisión final de lógica de negocio por familia.

---

# 28. REAL-TIME / HISTORICAL / LEARNING

Mantener:

- market data;
- velas;
- históricos;
- backfill;
- source sync;
- provenance;
- timestamps/staleness;
- reconciliation.

Aprendizaje:

- no IA intradía como decisor;
- conservar log de decisiones/outcomes;
- construir evidencia conocida al momento de la decisión;
- evitar hindsight leakage;
- lecciones fin de jornada.

---

# 29. SCRIPTS GENERADOS EN ESTA CONVERSACIÓN Y RESULTADOS

Los scripts locales fueron auxiliares de diagnóstico/host, no son automáticamente código canónico de repo.

## 29.1 `POROTA_RC6_CE_LOGGER_FIX_AND_RETEST.sh`
Resultado:
- falló por selección de capture stale y condición lógica;
- no aplicar conclusiones de seguridad a partir de ese fallo.

## 29.2 `POROTA_RC6_CE_LOGGER_FIX_AND_RETEST_V2.sh`
Resultado:
- probe fresh;
- 13 nonread;
- no patch.

## 29.3 `POROTA_RC6_CE_NONREAD_DIAG.sh`
Encontró:
- PPI logger
- Refiner
- Zendesk frontend events.

## 29.4 `POROTA_RC6_CE_TELEMETRY_POLICY_AND_RETEST.sh`
Encontró nuevas:
- Amplitude
- Clarity
- Zendesk session/login.

## 29.5 `POROTA_RC6_CE_NEW_NONREAD_DIAG.sh`
Clasificó nuevos endpoints.

## 29.6 `POROTA_RC6_CE_TELEMETRY_SUPPORT_V2_AND_RETEST.sh`
Quedó un nuevo subdominio Clarity.

## 29.7 `POROTA_RC6_CE_NONREAD_POLICY_V3_AND_RETEST.sh`
Resultado GREEN:
- política estable;
- blocked_nonread=0;
- auth trusted;
- orders=0.

## 29.8 `POROTA_RC6_CE_GREEN_EXPLAIN.sh`
Confirmó:
- `GREEN_NOT_DUE`;
- CE runs 84;
- DB ok;
- últimos imports antiguos AMARILLO.

## 29.9 `POROTA_RC6_PPI_FIRST_PARTY_GET_TRACE.sh`
Confirmó:
- 6/6 rutas auth;
- 31 GET events;
- no endpoints contractuales antiguos.

## 29.10 `POROTA_RC6_PPI_REALTIME_DOM_TRACE.sh`
Confirmó:
- WebSocket existe;
- DOM contiene tablas completas visibles;
- DOM es fuente útil.

## 29.11 `POROTA_RC6_PPI_DOM_STATIC_SNAPSHOT.sh`
Confirmó:
- 6 familias;
- headers/rows estructurados;
- 50-row truncation probable.

## 29.12 `POROTA_RC6_PPI_DOM_FULL_SWEEP_AND_COVERAGE.sh`
Confirmó:
- 16/16 familias;
- 0 first-party unknown mutations;
- 224 third-party abortadas;
- 12 familias posiblemente truncadas;
- 4 likely complete;
- Opciones 43 únicas/50.

---

# 30. PRIORIDADES CANÓNICAS A PARTIR DE AHORA

## P0 — antes de cualquier READY_PAPER

### P0.1 Matriz real de operabilidad API
PRÓXIMA TAREA.

### P0.2 Resolver universo/cobertura DOM >50
No importar truncado como completo.

### P0.3 Versionar política browser V3 en GitHub
Host-local → repo → tests → CI → deploy.

### P0.4 DOM Contract Evidence V1 SHADOW
Sin auto-activation.

### P0.5 Reconciliar DOM/API/History Store
Provenance y discrepancias.

### P0.6 Definir SLA/TTL/max_staleness
Por dato y familia.

### P0.7 Dashboard > Scraping semáforo
Captura + readiness separados.

### P0.8 Alerting/introspección/Telegram
Fail-closed selectivo.

### P0.9 Hard NO-TRADE date gate
Solo si todavía no está probado implementado.

## P1 — data/evidence hardening

- History Store completeness/price_basis;
- PPI Web History SHADOW;
- Cauciones contrato completo;
- opciones identity/dedup;
- doble calendario CEDEAR/mercados extranjeros;
- API contract coverage;
- SRE latency RCA;
- storage containment Phase A.

## P2 — model/risk validation

- MFE/MAE;
- Forward Lab v2;
- walk-forward;
- event risk;
- correlations/sector;
- cohort/versioning;
- campaign PAPER/SHADOW.

## P3 — UX/operación y consolidación

- scraping dashboard final;
- vivo/introspection final;
- responsive/tablet;
- drill-down de operaciones;
- documentación operatoria;
- cleanup físico final;
- checkpoint release candidate.

---

# 31. PASO EXACTO PARA LA NUEVA CONVERSACIÓN

La nueva conversación debe comenzar así:

1. leer este checkpoint completo;
2. verificar en GitHub:
   - branch actual;
   - commits de checkpoints;
   - collector/auth files;
   - ramas History Store/Evidence Architecture;
3. no tocar runtime todavía;
4. realizar auditoría **read-only** de capacidad API por las 16 familias:
   - SDK `ppi-client` instalado;
   - wrappers/adapters de POROTA;
   - enums/market/instrument types;
   - métodos search/marketdata/history/order/cancel;
   - tests existentes;
   - docs/read-only evidence;
5. producir tabla:
   - EXECUTABLE
   - PARTIAL
   - NON_EXECUTABLE
   - NOT_PROVEN
6. jamás enviar orden para “probar” soporte;
7. después resolver coverage >50;
8. luego integrar DOM CE V1 SHADOW;
9. mantener `real_orders_sent=0`.

---

# 32. CRITERIOS DE ACEPTACIÓN ANTES DE READY_PAPER

No cerrar RC6 readiness hasta demostrar:

- cada familia clasificada por capacidad API;
- scrapeable != executable explicitado en código/UI;
- universo esperado por familia;
- cobertura medible;
- DOM Contract Evidence versionado;
- Contract Evidence auth robusto;
- staleness SLA;
- fail-closed selectivo;
- Dashboard semáforo;
- Telegram/introspection de degradaciones;
- History Store semantics correctas;
- calendars correctos;
- fees/settlement/tick/step/minimums conocidos para familias ejecutables;
- opciones/cauciones correctamente normalizadas;
- no regressions observer;
- `real_orders_sent=0` durante toda RC6;
- campaña PAPER suficiente;
- revisión final antes de cualquier producción real.

---

# 33. COSAS QUE NO HAY QUE HACER

- no reabrir el problema de autenticación PPI salvo regresión;
- no volver a asumir endpoints GET antiguos;
- no permitir POSTs de telemetría;
- no ampliar allowlist de mutaciones first-party sin análisis;
- no entrar a `/Operar` para scraping;
- no hacer clicks `Operar`;
- no ejecutar orden de prueba;
- no marcar 50 filas como cobertura completa;
- no inferir API support por UI;
- no inferir tick/step;
- no promover DOM directamente a canonical sin shadow/reconcile;
- no borrar históricos/evidencia;
- no mezclar cleanup y deploy funcional;
- no asumir live SHA;
- no tratar `GREEN_NOT_DUE` como captura nueva;
- no tratar `LIKELY_COMPLETE` como `COMPLETE`.

---

# 34. REFERENCIAS DE CHECKPOINT DE ESTA ETAPA

En la rama:

`checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`

deben existir:

1. `POROTA_TRADING_CHECKPOINT_PPI_DOM_API_OPERABILITY_ALERTING_2026-09-08.md`
2. `POROTA_TRADING_CHECKPOINT_PPI_DOM_FULL_SWEEP_2026-09-08.md`
3. este archivo canónico de continuidad.

Este archivo debe prevalecer como punto de entrada para la nueva conversación, y los dos anteriores quedan como evidencia detallada de la investigación PPI DOM/API/alerting.

---

# 35. ESTADO DE CIERRE DE ESTA CONVERSACIÓN

**Último hallazgo validado:**

- PPI Web auth/trusted-device: GREEN.
- Browser read-only policy: GREEN.
- Scheduler CE: GREEN_NOT_DUE.
- GET/XHR STATIC antiguo: obsoleto.
- DOM autenticado: GREEN técnico.
- 16/16 familias visibles y capturables.
- cobertura total: YELLOW.
- API operabilidad: todavía NO PROBADA.
- Dashboard Scraping/alerting: pendiente.
- Contract Evidence DOM V1: pendiente.
- real orders: 0.
- observer: intacto.

**PRÓXIMO CHECKPOINT OPERATIVO:**
`PPI API OPERABILITY MATRIX — 16 FAMILIES`

Ese es el punto exacto desde el cual debe continuar la próxima conversación.
