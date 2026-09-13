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
- HEAD previo a esta actualización: `5e4f7dcfefd2fea81b6fad85720ae793a7291718`
- Universo histórico PPI masivo: `1960`
- `candidate_universe`: `911` filas observadas
- Modo obligatorio: `PRODUCTION_PAPER`
- Órdenes reales: `0`
- FCI y OPCIONES: excluidos REVERSIBLEMENTE del target inmediato; no borrar código/datos/histórico.
- 18 producers/timers: mantener pausados hasta gate integral.

Último safety snapshot confirmado:
- `porota-ppi-web-residual-rc6.service`: inactive/dead, Result=success, MainPID=0
- `porota-ppi-fullfamily-history-rc6.service`: inactive/dead, Result=success, MainPID=0
- procesos writers históricos: `0`
- observer DB `quick_check=ok`
- `can_simulate=1 AND AVAILABLE`: `246`
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

PPI API y PPI Web quedaron agotados para esos 7: provider_rows=0 / valid_rows=0, incluyendo HTTP 200 en Web sin filas.

Residual IOL read-only también agotado:
- cinco símbolos: 0 barras utilizables;
- `CRWVC` y `VXXC`: sólo una barra completamente en cero, inválida como OHLCV.

**Conclusión:** los 7 quedan `HISTORY_GAP`. No inventar datos, no tratarlos como cubiertos y no repetir scraping PPI/IOL sin nueva evidencia.

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

Actualmente visibles/confirmados:
- ACCIONES `55/55`
- CEDEARS `191/191`
- total dashboard/can_simulate actual: `246`

Esto NO equivale a autorización end-to-end; faltan gates contractuales/dinámicos/integración.

## 7. CONTRACT EVIDENCE V2

Tablas runtime:
- `contract_evidence_v2_current`
- `contract_evidence_v2_snapshots`
- `contract_evidence_v2_changes`

Identidades actuales con evidencia: `447`.

Fix ya aplicado en `cp_contract_evidence_v2_hf6.py`:
- metadata de recolección no se confunde con conflicto financiero;
- ticker/market/settlement de identidad canónica se reutilizan sólo si son unánimes;
- cualquier discrepancia crítica => fail-closed;
- nunca autoactiva.

Regresiones: `7/7 PASS`.

Matriz post-fix:
- identidades v2: `447`
- target tras excluir FCI/FCI_EXTERIOR/OPCIONES: `444`
- conflictos: `0`
- `READY_PAPER_CANDIDATE` contractualmente completos: `0`
- quick wins <=2 faltantes: `0`

Faltantes principales por familia:
- ACCIONES 55: cost_model, price_precision, quantity_min, quantity_step.
- CEDEARS 192: conversion_ratio, cost_model, price_precision, quantity_min, quantity_step.
- BONOS 43: términos de renta fija + order constraints + costos.
- LETRAS 18: lámina/vencimiento/nominal/unidad de precio/order constraints/costos.
- ON 85: términos de renta fija + moneda de pago/order constraints/costos.
- FUTUROS 43: contrato especializado incompleto.
- CAUCIONES: evidencia XHR parcial/agregada, todavía sin normalización completa al gate especializado.

## 8. DATOS YA CAPTURADOS

PPI authenticated XHR ya aporta ampliamente:
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

Collector:
- sólo sesión trusted existente; no usuario/password/OTP;
- sólo GET/HEAD/OPTIONS después de validar sesión;
- bloquea non-read;
- no llena cantidad/precio;
- no tiene imports de órdenes;
- sanitiza evidencia;
- `real_orders_sent=0`;
- targets: `InstrumentosOperables`, `CaucionesOperables`, `ConfiguracionOperatoriaSimplificada`, `SubyacenteOpciones`, `DatosTecnicos`.

Importer:
- escribe sólo Contract Evidence/audit;
- NO modifica eligibility ni autoactiva instrumentos.

Las 97 capturas trusted antiguas no contienen endpoint_kinds/row_keys/jobs útiles para el importer actual; no alcanza reimportarlas.

## 10. CAUCIONES — PRIORIDAD ELEVADA / CASH SWEEP EOD

Decisión actual: **CAUCIONES pasa a prioridad alta junto con BONOS/LETRAS/ON** porque una premisa funcional de Porota es evaluar si el efectivo sobrante puede colocarse en caución al final de la jornada cuando resulte operable y seguro.

No tratar caución como una acción/bono común. El gate existente `cq_family_contract_rules_hf6.py` exige explícitamente:

### Contrato estático de CAUCIONES
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

### Condición dinámica de CAUCIONES
- `operable`
- `market_session_state`
- `tna`
- `available_principal`
- `expiry_at`

TTL actual del gate:
- `tna`: 5 min
- `available_principal`: 5 min
- `market_session_state`: 5 min
- `operable`: 15 min
- `expiry_at`: 15 min

**Interpretación operativa:** para decidir un eventual cash sweep EOD no alcanza histórico. Debe conocerse en tiempo real si la caución sigue operable, plazo real disponible, lado correcto, capital mínimo/step, TNA vigente, capital disponible, costos y horario/sesión. Sólo con eso se puede simular rentabilidad neta y decidir si conviene colocar el sobrante.

**No habilitar todavía ninguna caución automática.** Primero capturar/normalizar evidencia, construir simulador PAPER específico y demostrar los gates.

## 11. SETTLEMENT / CONSOLIDACIÓN

- identidad canónica por familia+ticker+mercado+settlement;
- PPI API prima si es válida;
- Web/XHR complementa campo por campo;
- provenance obligatoria;
- conflictos => fail-closed;
- no fuzzy settlement;
- alias explícito `MRCTO -> MRCAC`;
- no datos sintéticos.

Settlement:
- BYMA estándar desde 2024-05-27: T+1 / 24HS
- CI=T+0
- 48HS legado
- usar `PlazosOperables` reales, nunca asumir.

## 12. PLAN PARALELO ACTUAL

### CARRIL A — CAUCIONES EOD (PRIORIDAD ALTA)
1. Auditar `CaucionesOperables` y `ConfiguracionOperatoriaSimplificada` con sesión trusted, read-only.
2. Obtener esquema/filas reales y mapear: side, term_days, principal_min/step, day_count_basis, fee_schedule, trading_session.
3. Identificar endpoint/fuente dinámica de TNA, available_principal, operable, market_session_state, expiry_at.
4. Normalizar a Contract Evidence v2 con provenance, sin auto-enable.
5. Recalcular gate CAUCIONES.
6. Construir/validar simulador PAPER de cash sweep: efectivo sobrante elegible -> plazo/tasa/costos -> rendimiento neto -> decisión HOLD/CAUCIONAR; sin órdenes reales.
7. Validar restricciones EOD: horario real, settlement del efectivo, monto mínimo, disponibilidad y vencimiento.

### CARRIL B — BONOS / LETRAS / ON
1. Captura estrecha `InstrumentosOperables + DatosTecnicos`.
2. Normalizar sólo equivalencias explícitas.
3. Completar términos renta fija y costos/order constraints reales.
4. Recalcular READY por familia/instrumento.

### CARRIL C — ACCIONES / CEDEAR
1. Cerrar cost_model/precision/min/step con evidencia explícita.
2. Obtener conversion_ratio real para CEDEAR.
3. Mantener bloqueados los 7 `HISTORY_GAP` aunque sean `READY_PAPER_SPOT` visuales.

### CARRIL D — FUTUROS
Auditar contrato especializado en paralelo, pero no sacrificar los carriles A/B de mayor impacto inmediato.

### CARRIL E — SAFETY / INTEGRACIÓN
- writers históricos apagados;
- PRODUCTION_PAPER;
- real_orders=0;
- writes/promociones al mismo store serializados;
- 18 producers/timers pausados;
- ningún READY integral sin contrato + dinámica + histórico cuando corresponda + settlement + calendario + simulador/tests.

### CARRIL F — CHECKPOINT
Actualizar este archivo en cada hito material.

## 13. SEMÁFORO ACTUAL

- 🟢 continuidad/checkpoint
- 🟢 safety PAPER / real orders 0
- 🟢 writers históricos detenidos
- 🟢 246 READY_PAPER_SPOT/dashboard = 55 Acciones + 191 CEDEAR
- 🟢 histórico Acciones 55/55
- 🟡 histórico CEDEAR 184/191
- 🔴 CEDEAR7: `HISTORY_GAP` confirmado tras PPI API + PPI Web + IOL
- 🟢 Contract Evidence v2 identidad/conflictos fix 7/7
- 🟢 matriz contractual post-fix disponible
- 🔴 READY contractual estricto actual: 0/447
- 🟡 BONOS/LETRAS/ON: captura/normalización contractual pendiente
- 🟡 CAUCIONES: PRIORIDAD ALTA; collector y gate existen, evidencia especializada/dinámica incompleta
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
- no considerar barra OHLCV nula como histórico;
- no automatizar caución EOD sin gate dinámico y simulador PAPER;
- no reactivar 18 producers/timers;
- no usar `READY_PAPER_SPOT` visual como autorización final.

## 15. ÚLTIMO PASO CONFIRMADO

1. PPI API + PPI Web + IOL quedaron agotados para el histórico de CEDEAR7; los 7 quedan `HISTORY_GAP`.
2. Se confirmó que el collector/importer/normalizer contractual existe y puede capturar `CaucionesOperables`/`DatosTecnicos` bajo guardas read-only.
3. El gate familiar existente ya modela CAUCIONES separando contrato estático de dinámica fresca.
4. Se eleva CAUCIONES a prioridad alta por el objetivo funcional de cash sweep EOD.
5. No se ha habilitado caución automática, no se modificó eligibility y no se enviaron órdenes.

## 16. SIGUIENTE ACCIÓN EXACTA

Ejecutar en paralelo:

- **A:** auditoría/captura read-only estrecha de `CaucionesOperables` + `ConfiguracionOperatoriaSimplificada` y descubrimiento de la fuente dinámica TNA/disponibilidad/expiry, sin operaciones.
- **B:** captura read-only `InstrumentosOperables + DatosTecnicos` para BONOS/LETRAS/ON.
- **C:** mapeo semántico seguro de costos/precision/min/step/ratios ya presentes o capturables.
- **D:** auditoría contractual FUTUROS.
- **E:** safety watcher read-only; writes al mismo store sólo serializados después de validar evidencia.
- **F:** al terminar la tanda, recalcular matriz READY por familia y actualizar este checkpoint.

---

**Regla para nuevo chat:** leer este archivo primero. El foco actual NO es repetir ingesta histórica masiva; es completar contratos/dinámica y elevar CAUCIONES para el caso de uso EOD sin relajar seguridad.