# POROTA TRADING — CHECKPOINT ACTIVE / READY 2026-09-14

**Estado:** ACTIVO / CANÓNICO PARA CONTINUIDAD  
**Fecha:** 2026-09-13  
**Objetivo inmediato:** maximizar instrumentos seguros en `READY_PAPER` para la rueda del lunes 2026-09-14, con CAUCIONES como prioridad operativa, manteniendo `PRODUCTION_PAPER`, `real_orders=0` y fail-closed.

## 0. REGLA DE CONTINUIDAD OBLIGATORIA

Este archivo es el único punto canónico de reanudación.

Antes de agregar cualquier paso nuevo:
1. verificar primero si ya existe código, commit, workflow, run, captura, evidencia persistida o prueba que lo haya cerrado;
2. no reconstruir desde checkpoints viejos si contradicen evidencia más reciente;
3. no repetir pruebas o ingestiones ya cerradas sin una razón nueva y verificable;
4. distinguir siempre entre **código existente**, **CI probada**, **runtime ejecutado**, **evidencia persistida** y **READY end-to-end**;
5. actualizar este checkpoint después de cada hito material y no declarar checkpoint actualizado hasta tener commit SHA verificado.

## 1. REPO / RAMA / SAFETY

- Repo: `mbalbo2023/Porota-trading`
- Rama canónica: `ops/rc6-ppi-web-residual-ready-20260913`
- HEAD verificado antes de esta actualización: `82bc6bad4c1059c4628b1c48616950bbe413c73d`
- Modo obligatorio: `PRODUCTION_PAPER`
- Órdenes reales: `0`
- `CASH_SWEEP_ORDER_ROUTING_ALLOWED=False`
- FCI y OPCIONES: fuera del target inmediato de forma reversible; no borrar datos/código/histórico.
- 18 producers/timers: mantener pausados hasta auditoría individual; no bulk-reactivate.

Safety snapshot read-only ya confirmado por workflow `RC6 Cauciones EOD read-only audit 2026-09-13`, run `34773204696`:
- writers históricos activos: 0
- servicios históricos de ingesta: inactive/dead con Result=success
- no se inició/reinició ningún servicio
- `can_simulate=1 AND AVAILABLE`: 246 = 55 ACCIONES + 191 CEDEARS

## 2. HISTÓRICO — CERRADO, NO REPETIR

PPI API full-family: corrida masiva cerrada sobre universo 1960.

PPI Web residual `PPI-WEB-RESIDUAL-20260913-001`:
- TOTAL 642/642
- ERROR 494
- DONE_PARTIAL 117
- DONE_EMPTY 31
- DONE_VALID 0
- pending/running 0

Histórico de los 246 simulables:
- cubiertos 239/246 = 97,15%
- ACCIONES 55/55
- CEDEARS 184/191

CEDEAR7 con `HISTORY_GAP` confirmado tras agotar PPI API + PPI Web + IOL:
`CRWVC`, `CVSC`, `MRVLC`, `VIVTC`, `VIVTD`, `VRTXC`, `VXXC`.

No inventar OHLCV ni hacer rerun masivo.

RCA 4 ON cerrado:
- MRCGC: provider180 / dropped_old180 / rows_365d0
- MRCGD: 339 / 339 / 0
- MRCLC: 279 / 279 / 0
- MRCPO: 480 / 480 / 0

## 3. READY VISUAL / DASHBOARD

`ready_paper_count` actual del dashboard cuenta `capability == READY_PAPER_SPOT`.

Confirmado:
- ACCIONES 55/55
- CEDEARS 191/191
- total 246

Esto NO equivale a READY end-to-end. Para READY integral siguen aplicando contrato + dinámica + settlement + calendario + histórico cuando corresponda + simulador/tests.

## 4. CONTRACT EVIDENCE V2 — ESTADO REAL

### 4.1 Motor / tablas / reglas — CERRADO

Tablas:
- `contract_evidence_v2_current`
- `contract_evidence_v2_snapshots`
- `contract_evidence_v2_changes`

Fix previo en `cp_contract_evidence_v2_hf6.py`:
- metadata de recolección no se interpreta como conflicto financiero;
- identidad canónica sólo reutiliza ticker/market/settlement cuando son unánimes;
- conflicto crítico => fail-closed;
- nunca autoactiva.

Regresiones previas: 7/7 PASS.

Última matriz runtime confirmada antes de los fixes de ingesta recientes:
- identidades con evidencia: 447
- target inmediato excluyendo FCI/FCI_EXTERIOR/OPCIONES: 444
- conflictos: 0
- `READY_PAPER_CANDIDATE`: 0/444
- quick wins <=2 faltantes: 0

**Importante:** `0/444` es la última matriz runtime confirmada; NO debe tratarse como recomputación posterior a los fixes de ingesta de 22:27-22:29Z. Hay que recomputar antes de afirmar el nuevo número.

### 4.2 Normalización/importación PPI — FIX VERIFICADO

Commits verificados en rama canónica:
- `6c143af1031b9e31cc40d45c14a826dbb962e827` — `fix(contract): preserve explicit PPI canonical evidence fields`
- `f41a9e9f041b9b951327dac17a624581a89f906f` — `fix(contract): merge same-source endpoint evidence before snapshot`
- `0295bf0099008cdc870ac02d126a75a3b0489d88` — pruebas de aliases explícitos y merge fail-closed
- `82bc6bad4c1059c4628b1c48616950bbe413c73d` — CI específica

El normalizador ahora conserva campos explícitos PPI sin inventar semántica:
- `fee_schedule` desde componentes explícitos de comisión/derechos;
- alias canónicos directos para renta fija como `maturity_date`, `coupon_terms`, `amortization_terms`;
- NO deriva `quantity_step`, `price_tick`, `conversion_ratio`, payment currency ni otras semánticas ausentes.

El importer ahora:
- agrupa/mergea evidencia de varios endpoints de la misma fuente por identidad antes del snapshot;
- conserva metadata/provenance;
- si dos endpoints de la misma captura discrepan en un campo contractual, registra conflicto y falla cerrado para esa identidad;
- no cambia eligibility y no autoactiva.

Workflow `RC6 Contract Evidence ingestion fix CI 2026-09-13`, run `34786919097`, job `103803958822`: **SUCCESS**.
- syntax PASS
- focused fail-closed regressions PASS
- safety invariant PASS
- regresiones focalizadas: 3/3 PASS

### 4.3 Datos autenticados ya observados

PPI authenticated XHR aporta ampliamente:
- instrument_id
- market
- currency
- componentes de comisión/derechos
- price_decimal_places
- quantity_decimal_places

`DatosTecnicos` puede aportar en renta fija:
- ISIN
- lámina mínima
- vencimiento
- intereses
- amortización
- otros términos técnicos explícitos

No inferir step/tick sólo desde decimales.

### 4.4 Captura contractual / reutilización

Stack existente a reutilizar:
- `rc6_trusted_browser_contract_collector.py`
- `rc6_contract_capture_importer.py`
- `rc6_ppi_contract_normalizer.py`

Collector:
- trusted session; no usuario/password/OTP en el flujo esperado;
- sólo GET/HEAD/OPTIONS después de validar sesión;
- bloquea non-read;
- `real_orders_sent=0`;
- targets incluyen `InstrumentosOperables`, `CaucionesOperables`, `ConfiguracionOperatoriaSimplificada`, `DatosTecnicos`.

Las 97 capturas trusted antiguas conocidas no alcanzan por sí solas para el importer actual porque no contienen todos los endpoint_kinds/row_keys/jobs requeridos.

**Pendiente real aquí:** verificar si una captura autenticada más reciente y reutilizable quedó persistida con esos endpoints. Si existe, reimportar con el importer corregido; si no existe, relanzar sólo la captura contractual estrecha. NO repetir la prueba API read-only ni la ingesta histórica.

## 5. CAUCIONES — ESTADO REAL VERIFICADO

### 5.1 Inventario runtime — CERRADO

Run `34773204696`:
- 10/10 CAUCIONES observadas y `AVAILABLE`
- `can_simulate=1`: 0/10
- market BYMA
- settlement observado `INMEDIATA`
- PESOS: PESOS1, PESOS2, PESOS7, PESOS30, PESOS120
- DÓLAR: DOLAR1, DOLAR2, DOLAR7, DOLAR30, DOLAR120

Contract Evidence de Cauciones en la última lectura runtime:
- 2 filas agregadas
- PPI_AUTHENTICATED_WEB: 1
- PPI_AUTHENTICATED_XHR: 1
- aún no equivalen a contrato completo por identidad

### 5.2 PPI API one-shot read-only — CERRADO, NO REPETIR

Workflow `RC6 Cauciones PPI API shape read-only 2026-09-13`, run `34773357093`, job `103766883647`: **SUCCESS**.

Confirmado:
- login_calls=1
- HTTP permitidos=48
- HTTP bloqueados=0
- sin order/budget/cancel
- sin DB write ni service restart
- type CAUCIONES
- BYMA
- settlements declarados: INMEDIATA, A-24HS, A-48HS, A-72HS
- quantity types: DINERO, PAPELES, CANTIDAD-TOTAL
- operación explícita `COLOCAR-CAUCION`
- 10/10 identidades devueltas
- `nominalInPrice=1` en las 10
- monedas explícitas observadas
- shapes `current`, `book`, `intraday` confirmados

Domingo:
- `book.date` sentinel `0001-01-01T00:00:00-03:00`
- current con fechas de ruedas anteriores
- intraday vacío

Esta ejecución prueba transporte/schema y datos reales retornados por PPI, pero NO dinámica fresca de rueda.

### 5.3 Semántica raw — REGLA VIGENTE

No mapear automáticamente:
- `current.price -> TNA`
- `volume/quantity -> available_principal`
- `bids/offers -> lado colocador`

hasta tener semántica explícita validada upstream.

### 5.4 Cash-sweep PAPER — YA EXISTE

No construir un segundo sweeper.

Módulos existentes:
- `df_caucion_end_of_day_sweep_hf6.py`
- `di_caucion_cash_sweep_runtime_hf6.py`
- `bt_caucion_paper.py`
- `ca_caucion_allocator.py`

El runtime:
- exige `ObligationSnapshot.complete=True`
- falla cerrado si no hay snapshot completo
- exige fee budget exacto
- relee caja con `for_execution=True`
- allocator revalida cash/risk/depth/fees/idempotency
- `CASH_SWEEP_ORDER_ROUTING_ALLOWED=False`

### 5.5 Adapter + bridge — YA EXISTEN, NO SON PENDIENTE DE CONSTRUCCIÓN

`rc6_caucion_offer_adapter.py`:
- convierte sólo snapshot canónico explícitamente VALIDATED a `CaucionOffer` PAPER;
- rechaza campos raw ambiguos como `price`, `volume`, `quantity`, `bids`, `offers`, `current`, `book`;
- exige `COLOCAR-CAUCION`, side COLOCADORA, BYMA, INMEDIATA, provenance, freshness y semantic_proof;
- no importa red, broker, DB ni órdenes.

`rc6_caucion_paper_bridge.py`:
- exige gate `CAUCION_FRESH_DATA_AGENT_GREEN`;
- exige `contract_status=READY_PAPER_CANDIDATE`;
- exige `real_order_capability=False`;
- exige `ObligationSnapshot`;
- delega al sweep PAPER existente;
- mantiene `routing_allowed=False` y `real_order_capability=False`.

Tests existentes `tests/test_rc6_caucion_paper_bridge.py` cubren gate rojo, snapshot raw ambiguo, duplicados, obligación ausente/incompleta y routing PAPER-only.

### 5.6 Schedule/cutoff — IMPLEMENTADO Y VERSIONADO

No reconstruir este componente.

`rc6_caucion_schedule_sources.py` fue agregado en commit `20c3f8206c883cc787d218e4c3dc24e76041d019`.

Evidencia congelada/versionada:
- BYMA Comunicado 19016 (2026-09-01)
- PPI Support `Horarios de Mercado` actualizado 2025-07-28
- ventana congelada para Contado Inmediato/Cauciones: 10:30-17:00
- evidencia separada para ARS y USD_MEP
- calendario no certificado como abierto => fail-closed

Commits relacionados posteriores incluyen tests de schedule por moneda y prueba CI.

### 5.7 Controlador único de ciclo — YA EXISTE

`rc6_caucion_cycle_controller.py`:
- agrega schedule por moneda + fresh-data agent + readiness persistida + política de oportunidad;
- exige evidencia de schedule independiente para todas las monedas presentes;
- persiste specialized readiness;
- si readiness no está GREEN => HOLD;
- si falta opportunity policy o liquidity deadline => HOLD;
- `real_order_capability=False`.

La CI de Cauciones incluye este controlador y chequea que los módulos nuevos no importen red ni superficies de orden.

### 5.8 Lo que falta REALMENTE para cerrar Cauciones

No volver a listar adapter, bridge, sweeper, schedule source o cycle controller como “a construir”.

Pendientes verificables:
1. confirmar productor runtime real de snapshots canónicos de las 10 identidades con semántica explícita validada;
2. confirmar de dónde sale `ObligationSnapshot` completo en runtime, con provenance/as_of, y si realmente se produce hoy;
3. completar por evidencia explícita cualquier campo contractual aún ausente: `principal_min`, `principal_step`, day-count, fee payment/schedule/costos exactos y otros requeridos por el gate;
4. demostrar durante rueda activa current/book/intraday frescos y la semántica colocadora/TNA/depth sin inferencias;
5. recomputar gate de las 10 identidades con las evidencias nuevas;
6. demostrar E2E PAPER/HOLD con `CAUCION_FRESH_DATA_AGENT_GREEN`, nunca real.

## 6. OBSERVER / CAPACIDAD

Workflow read-only `RC6 observer capacity read-only proof 2026-09-13`, run `34781768296`: SUCCESS.

Snapshot conocido:
- CPU ~10.26%
- memoria 241.9 MiB / 961.5 MiB ~25.16%
- PIDs 13
- `PAPER_ACTIVE_SYMBOL_LIMIT=20`
- intervalo observer 60s
- call budget PPI 2s
- intraday scan 180s
- batch 24

Últimos 300 ciclos auditados:
- selected_count 20 constante
- mean processing ~36.4s
- p95 ~50.2s
- max ~67.7s

Cuello de botella dominante: PPI/network serial current+book, no CPU/RAM.

Arquitectura acordada:
- HOT/CRITICAL: posiciones abiertas + todas las 10 CAUCIONES + foco líquido/señal reciente
- ACTIVE: líquido/operable 1-3 min
- BROAD: long tail 5-15 min, pantalla barata y promoción
- no sacrificar posiciones abiertas ni contract freshness por broad discovery

Safe experiment futuro en PAPER: 20 -> 30 sólo con telemetría; no saltar a 60 sin evidencia.

## 7. 18 PRODUCERS / TIMERS PAUSADOS

Estado: ⛔ siguen pausados por decisión intencional durante ingesta.

Antes de reactivar, auditar uno por uno:
- propósito
- source
- frecuencia
- universo
- settlement
- dedupe
- stale/error behavior
- overlap/duplication
- calendar
- delta/365d
- restart/resume
- observabilidad/Telegram
- writer vs read-only
- branch/image/version
- impacto readiness/freshness

Clasificar cada uno como:
- ACTIVE_EXPECTED
- SAFE_TO_ENABLE
- KEEP_PAUSED
- OBSOLETE
- DUPLICATE
- NEEDS_FIX

No bulk-reactivate.

## 8. SEMÁFORO ACTUAL

- 🟢 continuidad/checkpoint canónico
- 🟢 PAPER / real orders 0
- 🟢 writers históricos detenidos
- 🟢 histórico PPI masivo cerrado
- 🟢 ACCIONES histórico 55/55
- 🟡 CEDEAR histórico 184/191
- 🔴 CEDEAR7 HISTORY_GAP confirmado
- 🟢 dashboard READY_PAPER_SPOT 246 = 55 ACCIONES + 191 CEDEARS
- 🟢 Contract Evidence v2 engine/conflict handling
- 🟢 normalización/importación PPI corregida
- 🟢 Contract Evidence ingestion fix CI `34786919097`
- 🟡 matriz strict READY: última runtime confirmada 0/444; falta recomputar post-fix
- 🟢 CAUCIONES inventory 10/10
- 🟢 CAUCIONES PPI API read-only `34773357093`
- 🟢 operación `COLOCAR-CAUCION` confirmada
- 🟢 cash-sweep PAPER existente
- 🟢 adapter canónico existente
- 🟢 bridge PAPER existente
- 🟢 schedule/cutoff versionado por moneda existente
- 🟢 cycle controller existente
- 🟡 evidencia contractual por identidad completa: no demostrada todavía
- 🟡 productor runtime de snapshot canónico: verificar
- 🟡 productor runtime `ObligationSnapshot`: verificar
- 🔴 dinámica fresca de Cauciones: requiere rueda activa
- 🔴 READY end-to-end Cauciones: no demostrado todavía
- ⛔ 18 producers/timers pausados hasta auditoría

## 9. NO HACER

- no órdenes reales
- no salir de PRODUCTION_PAPER
- no rerun masivo PPI API/Web
- no segundo writer histórico
- no borrar/resetear state.sqlite3/tasks
- no blind retry
- no inventar contratos, steps, ticks, ratios, TNA o profundidad
- no considerar OHLCV nulo como histórico
- no usar snapshot domingo como dinámica fresca
- no construir segundo cash-sweep
- no volver a construir adapter/bridge/schedule/controller ya existentes
- no reactivar los 18 jobs/timers en bloque
- no ejecutar el viejo deploy postclose que recrea observer durante esta fase
- no llamar READY final a `READY_PAPER_SPOT` visual

## 10. ÚLTIMO PASO CONFIRMADO

1. Se verificó HEAD `82bc6bad4c1059c4628b1c48616950bbe413c73d`.
2. Se verificó que el read-only PPI de Cauciones `34773357093` ya se ejecutó con datos y no debe repetirse.
3. Se verificó adapter `rc6_caucion_offer_adapter.py` existente y fail-closed.
4. Se verificó bridge `rc6_caucion_paper_bridge.py` existente y PAPER-only.
5. Se verificó schedule/cutoff versionado `rc6_caucion_schedule_sources.py` y evidencia por moneda.
6. Se verificó `rc6_caucion_cycle_controller.py` existente.
7. Se verificaron fixes de Contract Evidence de normalización/importer y CI `34786919097` SUCCESS.
8. Se corrigió el diagnóstico anterior: NO construir adapter/bridge/schedule/controller; verificar productores y evidencia runtime real.
9. Seguridad intacta: real orders 0, routing real bloqueado.

## 11. SIGUIENTE ACCIÓN EXACTA

Orden obligatorio, verificando antes de crear nada:

1. **Captura contractual:** localizar/confirmar si existe una captura autenticada reciente persistida con `InstrumentosOperables`, `DatosTecnicos` y endpoints especializados en formato importable. Si existe, reutilizarla; si no, sólo entonces ejecutar captura estrecha read-only.
2. **Reimport + recompute:** ejecutar/importar con los fixes actuales y recomputar la matriz strict READY. Reportar número exacto por familia y razones faltantes; no asumir mejora sin corrida.
3. **Cauciones runtime producers:** localizar call sites/productores reales de snapshots canónicos y `ObligationSnapshot`; verificar provenance, `as_of`, completeness y wiring al controller/bridge.
4. **Auditoría 18 jobs/timers:** inventario y clasificación uno por uno antes de cualquier enable.
5. **Lunes rueda activa:** adquirir evidencia fresca, probar stale->HOLD->fresh recovery, `CAUCION_FRESH_DATA_AGENT_GREEN` y E2E PAPER/HOLD; nunca orden real.
6. Actualizar este checkpoint tras cada hito con commit/run/SHA verificable.

---

**Regla para nuevo chat:** leer este archivo primero. No repetir histórico masivo, read-only Cauciones, adapter/bridge, schedule ni controller. Primero verificar capturas/producers/runtime y sólo después crear o ejecutar lo que realmente falte.

---

## 4.5 UPDATE DE CONTINUIDAD — CAPTURA API RC6 2026-09-14

Una sola recaptura contractual estrecha, run GitHub Actions `34799337006`, job `103838639363`, commit del workflow `12ccddc96090e944d550eda83a823a59711801cf`. El collector desplegado tiene SHA-256 `51acc0e14a40afc2fcf7a391b33e983f30ccb078db82d01d04b7d34b63bd0a40`. Se reutilizó la sesión trusted RC6 y el guard siguió abortando todos los métodos distintos de GET/HEAD/OPTIONS.

La captura autenticada persistida está en `/opt/porota-trading/data/contract_evidence/rc6_trusted/contract_point1_20260914T023138Z.json`. Auditoría read-only del archivo: run `34800062452`, commit de resultado `a44fa47a2db93254c0aeed7ae9aa9b9b417493a9`, sin imprimir valores de instrumentos ni leer/escribir la DB.

Resultados:
- PAPER/safety preflight y postflight verdes; `AUTHENTICATED_TRUSTED_DEVICE`; `REAL_ORDERS_SENT=0`; montos y precios no completados; `DB_IMPORT_EXECUTED=NO`; `SERVICE_RESTARTED=NO`.
- `ROUTES_COUNT=10`; búsqueda API abierta en `/Operar/Bonos` y `/Operar/Ons`. Cauciones llegó a la ruta, pero su control de búsqueda terminó en `SEARCH_ERROR:TimeoutError`.
- La API devolvió 2 respuestas `InstrumentosOperables`: 2.628 filas de Bonos y 2.931 de ON. Campos explícitos: ID/ticker, proveedor, moneda, operabilidad de subasta, comisión/derechos y decimales de cantidad/precio. `quantity_step` y `price_tick` no están informados: NO derivarlos de decimales.
- `CaucionesOperables`: 120 filas con descripción, días y precisiones decimales; no aparecen moneda, lado, tasa anual, disponibilidad, mínimo/paso de principal, base de días ni modelo de costos requeridos por el gate.
- Dos respuestas adicionales sólo contienen schema de `ConfiguracionOperatoriaSimplificada`; no hubo respuesta `DatosTecnicos`.
- Se interceptaron y abortaron 47 POST. La lectura de sus categorías mostró sólo telemetría/soporte (`Zendesk`, `Refiner`, `Amplitude`, `Clarity` y `/api/logger`); no habilitar POST.
- `STRUCTURALLY_IMPORTABLE=NO`, `REUSABLE_TARGET=NO`. No importar esta captura.

**Alcance corregido por el usuario:** procesar sólo campos realmente entregados por API y sólo las 444 identidades del target inmediato. Las 5.559 filas de las dos listas son evidencia de las respuestas API existentes, no el universo a importar. No repetir esas listas ni ninguna ingesta histórica; el procesamiento siguiente debe filtrarlas contra las 444 identidades y persistir únicamente campos API explícitos.

**Siguiente paso autorizado:** determinar y capturar de forma estrecha el GET autenticado `DatosTecnicos` para las identidades de Bonos/ON del target 444, sin abrir controles de orden. Reutilizar la evidencia API de Cauciones ya existente (`34773357093`) en vez de repetir su probe. Recalcular la matriz strict READY sólo después de tener evidencia importable; el último `0/444` sigue siendo el último valor confirmado, no una recomputación posterior a esta captura.

Esta recaptura no completa por sí sola contratos READY ni cambia elegibilidad.
