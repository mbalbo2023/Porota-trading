# POROTA TRADING — CHECKPOINT ACTIVE / READY 2026-09-14

**Estado:** ACTIVO / CANÓNICO PARA CONTINUIDAD  
**Fecha:** 2026-09-13  
**Objetivo inmediato:** maximizar instrumentos seguros en `READY_PAPER` para la rueda del lunes 2026-09-14 y, en paralelo, elevar **CAUCIONES** a prioridad operativa para estudiar y habilitar de forma segura el uso del efectivo sobrante al cierre de la jornada cuando PPI/mercado lo permitan.

## 0. REGLA DE CONTINUIDAD OBLIGATORIA

Este archivo es el ÚNICO punto canónico de reanudación. Si el chat/proceso se corta, el siguiente debe leerlo primero y continuar desde `ÚLTIMO PASO CONFIRMADO` y `SIGUIENTE ACCIÓN EXACTA`. No reconstruir desde checkpoints viejos, no repetir pruebas ya cerradas sin nueva evidencia y no volver a lanzar ingestiones masivas cerradas.

Después de cada hito material actualizar este archivo con evidencia, semáforo, último paso confirmado y siguiente acción exacta.

## 1. REPO / RAMA / SAFETY

- Repo: `mbalbo2023/Porota-trading`
- Rama canónica: `ops/rc6-ppi-web-residual-ready-20260913`
- HEAD previo a esta actualización: `6c4368bc5f3c2d79257f6bd2ab7cfa64f81c592d`
- Universo histórico PPI masivo: `1960`
- `candidate_universe`: `911` filas observadas
- Modo obligatorio: `PRODUCTION_PAPER`
- Órdenes reales: `0`
- FCI y OPCIONES: excluidos REVERSIBLEMENTE del target inmediato; no borrar código/datos/histórico.
- 18 producers/timers: mantener pausados hasta gate integral.

Último safety snapshot read-only confirmado por workflow `RC6 Cauciones EOD read-only audit 2026-09-13`, run `34773204696`:
- `porota-ppi-web-residual-rc6.service`: inactive/dead, Result=success, MainPID=0
- `porota-ppi-fullfamily-history-rc6.service`: inactive/dead, Result=success, MainPID=0
- procesos writers históricos detectados: 0
- units/timers con nombre `cauc*` o `postclose*`: ninguno listado
- no se inició/reinició ningún servicio
- `can_simulate=1 AND AVAILABLE` global conocido: `246`
  - ACCIONES: `55`
  - CEDEARS: `191`

## 2. DOS INGESTAS DISTINTAS — NO CONFUNDIR

### A. HISTÓRICO DE PRECIOS / VELAS

Sirve al motor de decisión. La ingesta masiva PPI API y la corrida residual PPI Web YA CERRARON. No repetir completas. Sólo reconciliar stores o adquirir residuales verdaderos y dirigidos.

### B. CONTRATOS / METADATOS OPERATIVOS

Sirve para saber CÓMO operar correctamente: identidad, mercado, moneda, settlement real, min/step/lote, tick/precisión, nominal, lámina mínima, ISIN, vencimiento, cupón/amortización, ratio CEDEAR, futuros, cauciones, costos, sesión, etc.

Precedencia por campo:
1. PPI API estructurada válida.
2. PPI Web/XHR autenticada como complemento.
3. IOL sólo residual final después de agotar/reconciliar PPI, salvo decisión explícita.

Histórico completo != contrato completo != READY end-to-end.

Para CAUCIONES, el histórico NO es el dato principal del cash sweep EOD: se necesitan términos contractuales correctos y dinámica fresca al momento de decidir.

## 3. HISTÓRICO PPI — ESTADO CERRADO

PPI API full-family: corrida masiva cerrada sobre `1960`; NO rerun completo.

PPI Web residual `PPI-WEB-RESIDUAL-20260913-001`:
- TOTAL `642/642`
- ERROR `494`
- DONE_PARTIAL `117`
- DONE_EMPTY `31`
- DONE_VALID `0`
- pending/running `0`

Errores Web NO-OPCIONES exactos:
- ON 42
- LICITACIONES 18
- FUTUROS 9
- CAUCIONES 8
- LETRAS 6
- LEBACS 1
- total 84

BONOS: 0 ERROR Web; 5 `DONE_PARTIAL`, 1015 filas válidas de 1220 recibidas.

## 4. HISTÓRICO DE LOS 246 SIMULABLES

Reconciliación de stores:
- cubiertos: `239/246 = 97,15%`
- ACCIONES `55/55`
- CEDEARS `184/191`
- residual real: 7 CEDEAR

CEDEAR7:
`CRWVC`, `CVSC`, `MRVLC`, `VIVTC`, `VIVTD`, `VRTXC`, `VXXC`.

PPI API y PPI Web agotados para esos 7: provider_rows=0 / valid_rows=0. IOL residual read-only también agotado: cinco símbolos 0 barras; `CRWVC`/`VXXC` sólo una barra íntegramente en cero, inválida como OHLCV.

**Conclusión:** CEDEAR7 quedan `HISTORY_GAP`. No inventar datos ni repetir scraping sin nueva evidencia.

## 5. RCA CERRADO DE 4 ON

- `MRCGC`: provider180 / dropped_old180 / rows_365d0
- `MRCGD`: 339 / 339 / 0
- `MRCLC`: 279 / 279 / 0
- `MRCPO`: 480 / 480 / 0

PPI devolvió filas pero todas fuera de 365 días. No hacer retry ciego.

## 6. READY ACTUAL / DASHBOARD

Flujo localizado:
`bg_paper_dashboard.instruments_page -> _family_ux_table -> _family_ux_snapshot -> catalog_family_coverage.ready_paper_count`.

`ready_paper_count` cuenta `capability == READY_PAPER_SPOT`.

Actualmente confirmados:
- ACCIONES `55/55`
- CEDEARS `191/191`
- total dashboard/can_simulate: `246`

Esto NO equivale a autorización end-to-end; faltan gates contractuales/dinámicos/integración.

## 7. CONTRACT EVIDENCE V2

Tablas runtime:
- `contract_evidence_v2_current`
- `contract_evidence_v2_snapshots`
- `contract_evidence_v2_changes`

Identidades actuales con evidencia: `447`.

Fix aplicado en `cp_contract_evidence_v2_hf6.py`:
- metadata de recolección no se confunde con conflicto financiero;
- ticker/market/settlement de identidad canónica sólo se reutilizan si son unánimes;
- discrepancia crítica => fail-closed;
- nunca autoactiva.

Regresiones: `7/7 PASS`.

Matriz post-fix:
- identidades v2: `447`
- target tras excluir FCI/FCI_EXTERIOR/OPCIONES: `444`
- conflictos: `0`
- `READY_PAPER_CANDIDATE` contractualmente completos: `0`
- quick wins <=2 faltantes: `0`

Faltantes principales:
- ACCIONES 55: costos/precisión/min/step.
- CEDEARS 192: ratio + costos/precisión/min/step.
- BONOS 43: términos renta fija + constraints + costos.
- LETRAS 18: lámina/vencimiento/nominal/unidad precio/constraints/costos.
- ON 85: términos renta fija + moneda pago/constraints/costos.
- FUTUROS 43: contrato especializado incompleto.
- CAUCIONES: contrato por identidad + dinámica fresca todavía incompletos.

## 8. DATOS YA CAPTURADOS

PPI authenticated XHR aporta ampliamente:
- instrument_id
- market
- currency
- commission/financial rights
- price_decimal_places
- quantity_decimal_places

No inferir quantity_step/tick desde decimales.

Para renta fija, `DatosTecnicos` puede aportar ISIN, lámina mínima, vencimiento, intereses, amortización y otros términos.

## 9. COLECTOR CONTRACTUAL EXISTENTE

Reutilizar, no crear arquitectura paralela:
- `rc6_trusted_browser_contract_collector.py`
- `rc6_contract_capture_importer.py`
- `rc6_ppi_contract_normalizer.py`

Collector trusted:
- sesión trusted existente; no usuario/password/OTP;
- sólo GET/HEAD/OPTIONS después de validar sesión;
- bloquea non-read;
- no llena cantidad/precio;
- sin imports de órdenes;
- sanitiza evidencia;
- `real_orders_sent=0`;
- targets incluyen `InstrumentosOperables`, `CaucionesOperables`, `ConfiguracionOperatoriaSimplificada`, `DatosTecnicos`.

Importer:
- escribe sólo Contract Evidence/audit;
- NO cambia eligibility ni autoactiva.

Las 97 capturas trusted antiguas no contienen endpoint_kinds/row_keys/jobs útiles para el importer actual; reimportarlas no alcanza.

### CORRECCIÓN IMPORTANTE SOBRE EL CAMINO CAUCIONES EXISTENTE

El código canónico actual `bd_ppi_readonly_guard.py` YA posee descubrimiento oficial de CAUCIONES vía PPI API, separado del collector Web:
- alias interno CAUCION -> contrato oficial PPI `CAUCIONES`;
- plazos por defecto/probados: `1,2,7,30,120` días;
- busca `PESOS{días}` y `DOLAR{días}` con `Name={días}` y market BYMA;
- superficie de mercado read-only: search/current/book/intraday;
- `ProductionMarketReader` no expone métodos de order/budget/cancel.

El workflow histórico `.github/workflows/rc6-cauciones-postclose-deploy-20260907.yml` NO debe ejecutarse ahora: es un deploy transaccional que recrea el observer. No es el cash-sweep PAPER final y contradice la política actual de no reactivar/recrear runtime mientras se audita.

## 10. CAUCIONES — PRIORIDAD ALTA / CASH SWEEP EOD

Premisa funcional: evaluar si el efectivo sobrante realmente libre al final de la rueda puede colocarse en caución cuando sea seguro, líquido y económicamente conveniente.

### 10.1 Inventario runtime exacto — CERRADO

Workflow read-only: `RC6 Cauciones EOD read-only audit 2026-09-13`, run `34773204696`, **3/3 jobs success**.

`candidate_universe`:
- CAUCIONES observadas: `10`
- status `AVAILABLE`: `10/10`
- `can_simulate=1`: `0/10`
- market: `BYMA`
- settlement observado: `INMEDIATA`
- PESOS: `PESOS1`, `PESOS2`, `PESOS7`, `PESOS30`, `PESOS120`
- DÓLAR: `DOLAR1`, `DOLAR2`, `DOLAR7`, `DOLAR30`, `DOLAR120`

`catalog_family_coverage`:
- declared=1
- observed_count=10
- discovery_status=`INSTRUMENTS_OBSERVED`
- ready_paper_count=`0`

`contract_evidence_v2_current` para CAUCIONES:
- filas actuales: `2`
- `PPI_AUTHENTICATED_WEB`: 1
- `PPI_AUTHENTICATED_XHR`: 1
- ambas son evidencia agregada `ticker='*'`, `market='UNKNOWN'`, `settlement='UNKNOWN'`

**Conclusión:** PPI ya nos identifica exactamente 10 contratos candidatos, pero Contract Evidence todavía no posee contrato especializado por identidad y el catálogo los mantiene fail-closed. Esto es correcto hasta completar integración.

### 10.2 Gate especializado existente

`cq_family_contract_rules_hf6.py` exige:

Contrato estático:
- `market`
- `currency`
- `settlement`
- `side`
- `term_days`
- `principal_min`
- `principal_step`
- `day_count_basis`
- `fee_schedule`
- `trading_session`

Dinámica fresca:
- `operable`
- `market_session_state`
- `tna`
- `available_principal`
- `expiry_at`

TTL:
- `tna`: 5 min
- `available_principal`: 5 min
- `market_session_state`: 5 min
- `operable`: 15 min
- `expiry_at`: 15 min

El módulo sólo puede elevar a `READY_PAPER_CANDIDATE`; nunca habilita trading por sí mismo.

### 10.3 Qué falta para el cash sweep EOD

No alcanza con saber que existen `PESOS1` o `DOLAR1`. Para el sobrante EOD deben resolverse en secuencia:
1. **Caja realmente libre y liquidada**, después de reservas para órdenes, costos, garantías y necesidades del día siguiente.
2. **Lado correcto** para colocar fondos (semántica exacta PPI, no asumir nombres).
3. **Plazo elegible** real en ese momento; no asumir 1 día aunque sea el candidato natural.
4. **Capital mínimo y step/múltiplo** reales.
5. **TNA ejecutable y capital disponible** frescos desde market data/book/endpoints estructurados.
6. **Costos/fee schedule** y day-count basis para rendimiento neto.
7. **Trading session/cutoff** y estado de mercado vigente.
8. **Settlement/vencimiento** compatible con la necesidad de caja siguiente.
9. Simulación PAPER específica y decisión `HOLD` vs `CAUCIONAR`; sin órdenes reales.
10. Sólo después de pruebas/gates se considerará integración READY.

## 11. SETTLEMENT / CONSOLIDACIÓN

- identidad canónica por familia+ticker+mercado+settlement;
- PPI API prima si válida;
- Web/XHR complementa campo por campo;
- provenance obligatoria;
- conflicto crítico => fail-closed;
- no fuzzy settlement;
- alias explícito `MRCTO -> MRCAC`;
- no datos sintéticos.

Settlement general:
- BYMA estándar desde 2024-05-27: T+1 / 24HS
- CI=T+0
- 48HS legado
- usar `PlazosOperables` reales.

Para CAUCIONES, el runtime actualmente observa `settlement=INMEDIATA`; conservar ese valor como evidencia específica de la familia y NO reemplazarlo por una regla spot genérica sin validación PPI.

## 12. PLAN PARALELO ACTUAL

### CARRIL A — CAUCIONES EOD (PRIORIDAD ALTA)
A1. Ejecutar snapshot PPI API **one-shot read-only** sobre las 10 cauciones usando el guard existente; capturar sólo forma/campos sanitizados de current/book/intraday y configuración, sin servicio persistente ni órdenes.
A2. En paralelo, captura trusted Web/XHR estrecha de `CaucionesOperables` + `ConfiguracionOperatoriaSimplificada` para términos estáticos que API marketdata no entregue.
A3. Mapear por identidad los campos estáticos al Contract Evidence v2 con provenance; no auto-enable.
A4. Determinar fuente exacta de `tna`, `available_principal`, `operable`, `market_session_state`, `expiry_at` y sus timestamps.
A5. Recalcular `cq_family_contract_rules_hf6.evaluate_family('CAUCIONES', ...)` por cada una de las 10 identidades.
A6. Construir simulador PAPER específico de cash sweep: `cash_free -> plazo/lado -> principal válido -> tasa ejecutable -> costos -> rendimiento neto -> disponibilidad futura -> HOLD/CAUCIONAR`.
A7. Tests de EOD/cutoff/settlement/caja/reserva y fail-closed ante dato stale/ausente.

### CARRIL B — BONOS / LETRAS / ON
1. Captura estrecha `InstrumentosOperables + DatosTecnicos`.
2. Normalizar sólo equivalencias explícitas.
3. Completar términos renta fija y constraints/costos reales.
4. Recalcular READY por familia/instrumento.

### CARRIL C — ACCIONES / CEDEAR
1. Cerrar costos/precision/min/step con evidencia explícita.
2. Obtener conversion_ratio real CEDEAR.
3. Bloquear CEDEAR7 por `HISTORY_GAP` aunque sean `READY_PAPER_SPOT` visuales.

### CARRIL D — FUTUROS
Auditar contrato especializado en paralelo sin desplazar A/B.

### CARRIL E — SAFETY / INTEGRACIÓN
- writers históricos apagados;
- PRODUCTION_PAPER;
- real_orders=0;
- writes/promociones al mismo store serializados;
- 18 producers/timers pausados;
- no ejecutar viejo deploy postclose;
- ningún READY integral sin contrato + dinámica + histórico cuando corresponda + settlement + calendario + simulador/tests.

### CARRIL F — CHECKPOINT
Actualizar este archivo tras cada hito material.

## 13. SEMÁFORO ACTUAL

- 🟢 continuidad/checkpoint
- 🟢 safety PAPER / real orders 0
- 🟢 writers históricos detenidos
- 🟢 246 READY_PAPER_SPOT/dashboard = 55 Acciones + 191 CEDEAR
- 🟢 histórico Acciones 55/55
- 🟡 histórico CEDEAR 184/191
- 🔴 CEDEAR7 `HISTORY_GAP` confirmado PPI API + Web + IOL
- 🟢 Contract Evidence v2 identidad/conflictos fix 7/7
- 🟢 matriz contractual post-fix
- 🔴 READY contractual estricto global actual: 0/447
- 🟡 BONOS/LETRAS/ON: captura/normalización pendiente
- 🟢 CAUCIONES inventario: 10/10 observadas/AVAILABLE
- 🔴 CAUCIONES can_simulate: 0/10
- 🟡 CAUCIONES evidencia: 2 registros agregados, todavía no por identidad
- 🟢 CAUCIONES gate estático/dinámico: definido en código
- 🟢 CAUCIONES PPI API read-only discovery: definido y auditado
- 🔴 CAUCIONES cash-sweep PAPER integrado: todavía no implementado/demostrado
- 🟡 FUTUROS: contrato especializado pendiente
- ⚪ FCI/OPCIONES: fuera del target inmediato, reversible
- ⛔ 18 producers/timers pausados
- 🔴 READY end-to-end integral: aún no demostrado

## 14. NO HACER

- no órdenes reales;
- no salir de PRODUCTION_PAPER;
- no rerun masivo PPI API/Web;
- no segundo writer histórico;
- no borrar/resetear state.sqlite3/tasks;
- no blind retry;
- no inventar contratos/steps/ticks/ratios;
- no considerar OHLCV nulo como histórico;
- no automatizar caución EOD sin gate dinámico + simulador PAPER;
- no reactivar 18 producers/timers;
- no ejecutar `.github/workflows/rc6-cauciones-postclose-deploy-20260907.yml` durante esta fase;
- no usar `READY_PAPER_SPOT` visual como autorización final.

## 15. ÚLTIMO PASO CONFIRMADO

1. CEDEAR7 agotó PPI API + PPI Web + IOL y queda `HISTORY_GAP`.
2. CAUCIONES fue elevada a prioridad alta por el caso de cash sweep EOD.
3. Auditoría read-only run `34773204696` cerró 3/3 success.
4. Se confirmaron 10 cauciones PPI disponibles: PESOS/DOLAR x 1/2/7/30/120 días; 0/10 can_simulate y ready_paper_count=0.
5. Contract Evidence v2 sólo tiene 2 evidencias agregadas de Cauciones, todavía sin identidad operativa completa.
6. El guard API oficial ya puede descubrir cauciones y leer current/book/intraday sin superficie de órdenes.
7. El gate v2 de Cauciones ya define contrato estático y dinámica fresca con TTL.
8. El viejo workflow postclose es un deploy/recreate del observer y NO se ejecutó.
9. Seguridad intacta: writers apagados, ningún unit/timer caución/postclose listado, órdenes reales 0.

## 16. SIGUIENTE ACCIÓN EXACTA

Ejecutar en paralelo sin reactivar servicios persistentes:

- **A1:** one-shot PPI API read-only sobre las 10 cauciones para inventariar campos reales de current/book/intraday/configuración y determinar qué dinámica aporta la API.
- **A2:** captura trusted Web/XHR estrecha de `CaucionesOperables` + `ConfiguracionOperatoriaSimplificada` para completar contrato estático.
- **B:** `InstrumentosOperables + DatosTecnicos` para BONOS/LETRAS/ON.
- **C:** mapeo seguro costos/precision/min/step/ratios para Acciones/CEDEAR.
- **D:** auditoría contractual FUTUROS.
- **E:** safety read-only; no service restart, no writers, no real orders.
- **F:** después de la tanda, recalcular matriz READY por familia y actualizar este checkpoint.

---

**Regla para nuevo chat:** leer este archivo primero. El foco NO es repetir ingesta histórica masiva. El foco es completar contratos/dinámica; CAUCIONES es ahora prioridad alta por el cash sweep EOD y debe permanecer fail-closed hasta demostrar contrato + dinámica + simulador PAPER.