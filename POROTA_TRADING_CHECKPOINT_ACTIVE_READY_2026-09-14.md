# POROTA TRADING — CHECKPOINT ACTIVE / READY 2026-09-14

**Estado:** ACTIVO / CANÓNICO PARA CONTINUIDAD  
**Fecha:** 2026-09-13  
**Objetivo:** maximizar instrumentos seguros en `READY_PAPER` para la rueda del lunes 2026-09-14 sin relajar contratos, identidad, settlement, calendarios, riesgo, costos ni procedencia.

## 0. REGLA DE CONTINUIDAD OBLIGATORIA

Este archivo es el ÚNICO punto canónico de reanudación. Si el chat/proceso se corta, el siguiente debe leerlo primero y continuar desde `ÚLTIMO PASO CONFIRMADO` y `SIGUIENTE ACCIÓN EXACTA`. No recomenzar desde checkpoints anteriores ni repetir pruebas cerradas sin nueva evidencia.

Después de cada hito material actualizar este archivo con evidencia, semáforo, último paso y siguiente acción.

## 1. REPO / RAMA / SAFETY

- Repo: `mbalbo2023/Porota-trading`
- Rama canónica: `ops/rc6-ppi-web-residual-ready-20260913`
- HEAD de trabajo al lanzar la última tanda: `09c5686a6e18f613e29247d1b574ac77e5ad66ed`
- Universo histórico PPI masivo: `1960`
- `candidate_universe`: `911` filas observadas
- Modo obligatorio: `PRODUCTION_PAPER`
- Órdenes reales: `0`
- FCI y OPCIONES: excluidos REVERSIBLEMENTE del target del 2026-09-14; no borrar código/datos/histórico.
- 18 producers/timers: mantener pausados hasta gate integral.

Último safety snapshot read-only, run `34771504731`:
- `porota-ppi-web-residual-rc6.service`: inactive/dead, Result=success, MainPID=0
- `porota-ppi-fullfamily-history-rc6.service`: inactive/dead, Result=success, MainPID=0
- procesos writers históricos detectados: 0
- observer DB `quick_check=ok`
- `can_simulate=1 AND AVAILABLE`: `246`
  - ACCIONES: `55`
  - CEDEARS: `191`

## 2. DOS INGESTAS DISTINTAS — NO CONFUNDIR

### A. HISTÓRICO DE PRECIOS / VELAS

Sirve al motor de decisión. La ingesta masiva PPI API ya cerró y la corrida Web residual también cerró. No repetir ninguna completa. Reconciliar stores y adquirir sólo residuales verdaderos y dirigidos.

### B. CONTRATOS / METADATOS OPERATIVOS

Sirve para saber CÓMO operar correctamente un instrumento: identidad, mercado, moneda, settlement real, min/step/lote, precisión/tick, nominal, lámina mínima, ISIN, vencimiento, cupón/amortización, ratio CEDEAR, multiplicadores/margen de futuros, cauciones, costos, etc.

Precedencia por campo:
1. PPI API estructurada válida.
2. PPI Web/XHR autenticada como complemento.
3. IOL sólo residual final después de reconciliar PPI, salvo decisión explícita.

Histórico completo != contrato completo != READY end-to-end.

## 3. HISTÓRICO PPI — ESTADO CERRADO

PPI API full-family: corrida masiva cerrada sobre `1960`; no rerun completo.

PPI Web residual run `PPI-WEB-RESIDUAL-20260913-001`:
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

Reconciliación de todos los stores:
- cubiertos: `239/246 = 97,15%`
- ACCIONES `55/55`
- CEDEARS `184/191`
- residual real: 7 CEDEAR

Los 7:
`CRWVC`, `CVSC`, `MRVLC`, `VIVTC`, `VIVTD`, `VRTXC`, `VXXC`.

### RCA de los 7 — NUEVO HALLAZGO CONFIRMADO

Run `34771504731`, job `cedear-seven-rca`, success.

Los 7 son instrumentos vigentes en catálogo/API, están en `candidate_universe` como `can_simulate=1`, `AVAILABLE`, `READY_PAPER_SPOT`, tienen market snapshots y actividad PAPER. No son simples aliases ni símbolos muertos.

En los ledgers PPI históricos aparecen repetidamente con `provider_rows=0`, `valid_rows=0`, `EMPTY_OR_INVALID`; en `ppi_history_ingest_tasks` quedaron `DONE_EMPTY` con provider=0. Por lo tanto son gaps genuinos del history provider PPI API actual.

Próxima regla: antes de IOL, reconciliar estos 7 contra el state/capturas de la corrida PPI Web cerrada. Sólo si PPI Web tampoco aporta histórico, pasan a residual externo final.

## 5. RCA CERRADO DE 4 ON

`MRCGC`: provider180/dropped_old180/rows_365d0  
`MRCGD`: 339/339/0  
`MRCLC`: 279/279/0  
`MRCPO`: 480/480/0

PPI sí devolvió filas pero todas estaban fuera de 365 días. No hacer retry ciego.

## 6. DASHBOARD / READY ACTUAL

Flujo visual localizado:
`bg_paper_dashboard.instruments_page -> _family_ux_table -> _family_ux_snapshot -> catalog_family_coverage.ready_paper_count`.

`ready_paper_count` cuenta `capability == READY_PAPER_SPOT`.

Observado:
- ACCIONES `55/55`
- CEDEAR `191/191`

Esto explica el dashboard pero NO equivale a autorización end-to-end.

## 7. CONTRACT EVIDENCE V2

Tablas runtime:
- `contract_evidence_v2_current`
- `contract_evidence_v2_snapshots`
- `contract_evidence_v2_changes`

Identidades actuales con evidencia: `447`.

Se corrigió `cp_contract_evidence_v2_hf6.py` para:
- no confundir metadata de recolección con conflicto financiero;
- reutilizar `ticker/market/settlement` de la identidad canónica cuando es unánime;
- fail-closed si record key o payload discrepan;
- nunca autoactivar.

Regresiones: `7/7 PASS`.

### MATRIZ CONTRACTUAL POST-FIX — NUEVO HALLAZGO

Run `34771504731`, job `contract-postfix-matrix`, success. Se usó el evaluador real corregido `family_readiness_state`, no merge raw.

- identidades v2: `447`
- identidades target tras excluir FCI/FCI_EXTERIOR/OPCIONES actuales: `444`
- conflictos detectados: `0`
- `READY_PAPER_CANDIDATE` contractualmente completos: `0`
- quick wins con <=2 campos faltantes: `0`

El falso faltante de `ticker/market/settlement` desapareció, confirmando el fix. Los faltantes restantes son reales según el gate contractual actual.

Por familia:
- ACCIONES 55: faltan en las 55 `cost_model`, `price_precision`, `quantity_min`, `quantity_step`; además currency1/instrument_id1.
- CEDEARS 192: faltan en las 192 `conversion_ratio`, `cost_model`, `price_precision`, `quantity_min`, `quantity_step`; además currency1/instrument_id1.
- BONOS 43: faltan en las 43 `amortization_terms`, `cost_model`, `coupon_terms`, `isin`, `lamina_minima`, `maturity`, `nominal_value`, `price_precision`, `price_unit_nominals`, `quantity_min`, `quantity_step`; además currency1/instrument_id1.
- LETRAS 18: faltan en las 18 `cost_model`, `lamina_minima`, `maturity`, `nominal_value`, `price_precision`, `price_unit_nominals`, `quantity_min`, `quantity_step`; currency2/instrument_id2.
- ON 85: faltan en las 85 `amortization_terms`, `cost_model`, `coupon_terms`, `isin`, `lamina_minima`, `maturity`, `nominal_value`, `payment_currency`, `price_precision`, `price_unit_nominals`, `quantity_min`, `quantity_step`; currency1/instrument_id1.
- FUTUROS 43: faltan masivamente campos especializados (underlying, expiry, multiplier, quantity_step, tick/tick_value, margin, settlement/adjustment rules, trading hours, cost model, etc.).
- CAUCIONES: evidencia incompleta de sus campos dinámicos/especializados.

Conclusión: el cuello de botella principal ya NO es histórico masivo; es poblar Contract Evidence v2 con metadatos contractuales reales.

## 8. COLECTOR CONTRACTUAL — CORRECCIÓN DE DIAGNÓSTICO

El barrido del filesystem vivo había encontrado sólo una referencia WIP a `DatosTecnicos`, por lo que no estaba demostrado un colector DESPLEGADO/VIVO en servidor.

La rama canónica sí contiene código reutilizable. Run `34771504731`, job `contract-collector-map`, success:

- `rc6_trusted_browser_contract_collector.py`
  - targets: `InstrumentosOperables`, `CaucionesOperables`, `ConfiguracionOperatoriaSimplificada`, `SubyacenteOpciones`, `DatosTecnicos`
  - sólo permite GET/HEAD/OPTIONS después de validar sesión
  - bloquea requests no-read
  - no rellena cantidad/precio
  - no tiene imports de órdenes
  - sanitiza respuestas
- `rc6_contract_capture_importer.py`
  - importa `InstrumentosOperables`, `CaucionesOperables`, `DatosTecnicos` a Contract Evidence v2
  - escribe sólo evidence/audit; no cambia eligibility ni activa instrumentos
- `rc6_ppi_contract_normalizer.py`
  - normaliza InstrumentosOperables y DatosTecnicos
  - deliberadamente NO inventa step/tick desde cantidad de decimales
  - DatosTecnicos extrae ISIN, lámina mínima, vencimiento, intereses, amortización, etc., pero mantiene `price_unit_nominals`, order step/tick sin inferir cuando el proveedor no los define explícitamente.

Por lo tanto: NO construir otro colector desde cero. Primero determinar si las capturas existentes ya contienen campos útiles no mapeados y, si no, ejecutar una captura autenticada, estrecha y read-only con este código revisado.

## 9. REGLAS DE CONSOLIDACIÓN

- identidad canónica por familia+ticker+mercado+settlement;
- PPI API prima cuando es válida;
- XHR/Web complementa campo por campo;
- no overwrite silencioso;
- provenance obligatoria;
- conflictos críticos => fail-closed;
- no fuzzy matching de settlement;
- alias explícito `MRCTO -> MRCAC`;
- no datos sintéticos.

Settlement:
- BYMA estándar desde 2024-05-27: T+1 / 24HS
- CI=T+0
- 48HS legado
- usar `PlazosOperables` reales.

## 10. SEMÁFORO ACTUAL

- 🟢 checkpoint continuidad
- 🟢 safety PAPER / real orders 0
- 🟢 writers históricos detenidos
- 🟢 último lote paralelo 4/4 success
- 🟢 246 simulables actuales = 55 Acciones + 191 CEDEAR
- 🟢 histórico Acciones 55/55
- 🟡 histórico CEDEAR 184/191; 7 provider-empty genuinos, falta reconciliar Web
- 🟢 fix identidad/conflictos 7/7 tests
- 🟢 matriz contractual post-fix exacta obtenida
- 🔴 Contract Evidence v2 completo: 0/447 bajo requisitos estrictos actuales
- 🟡 colector contractual: código existe y fue auditado a nivel de seguridad; despliegue/capturas útiles aún por comprobar
- 🟡 BONOS/LETRAS/ON: prioridad contractual
- 🟡 CAUCIONES/FUTUROS: contratos especializados pendientes
- ⚪ FCI/OPCIONES: fuera del target inmediato, reversiblemente
- ⛔ IOL: no iniciar hasta reconciliar PPI Web para residuales reales
- ⛔ 18 producers/timers: pausados
- 🔴 READY end-to-end integral: aún no demostrado

## 11. NO HACER

- no órdenes reales;
- no salir de PRODUCTION_PAPER;
- no rerun masivo PPI API;
- no rerun masivo PPI Web;
- no segundo writer histórico;
- no borrar/resetear state.sqlite3/tasks;
- no blind retry de errores;
- no inventar contratos/steps/ticks;
- no iniciar IOL antes de agotar/reconciliar PPI;
- no reactivar 18 producers/timers;
- no usar `READY_PAPER_SPOT` visual como autorización final.

## 12. ÚLTIMO PASO CONFIRMADO

Workflow `RC6 READY next parallel 2026-09-13`, run `34771504731`, terminó `4/4 success` en modo diagnóstico/read-only para runtime.

Quedó confirmado:
1. safety verde y writers inactivos;
2. 246 can_simulate sin cambios;
3. 7 CEDEAR son gaps provider-empty reales de PPI History API;
4. matriz contractual post-fix = 447 identidades, 0 conflictos, 0 completas bajo requisitos estrictos actuales;
5. los falsos faltantes de ticker/market/settlement quedaron eliminados;
6. el código RC6 ya contiene collector/importer/normalizer reutilizable para XHR/`DatosTecnicos`; no reconstruir desde cero.

## 13. SIGUIENTE ACCIÓN EXACTA

Ejecutar en paralelo:

### CARRIL A — CEDEAR7 vs PPI WEB CERRADA
Consultar read-only `/opt/porota-ingest/ppi-web-residual/state.sqlite3` y capturas/residual para `CRWVC,CVSC,MRVLC,VIVTC,VIVTD,VRTXC,VXXC`. Determinar si Web ya los intentó y su state/provider/valid/error. Sin red. Si Web no los intentó o existe una causa dirigida corregible, evaluar adquisición dirigida; si Web también está vacío, recién entonces residual externo/IOL.

### CARRIL B — GAP DE CAPTURA CONTRACTUAL
Medir por familia y fuente qué keys existen hoy en `evidence_json` y qué campos requeridos faltan. Inspeccionar capturas sanitizadas existentes para saber si el dato ya fue capturado pero no mapeado, o si nunca fue capturado.

### CARRIL C — COLECTOR EXISTENTE
Leer y reutilizar `rc6_trusted_browser_contract_collector.py`, `rc6_contract_capture_importer.py`, `rc6_ppi_contract_normalizer.py`. Diseñar sólo el mínimo cambio necesario. No desplegar navegador masivo. Cualquier captura futura debe ser autenticada, read-only, de rutas concretas y con autoactivación imposible.

### CARRIL D — PRIORIZACIÓN READY
Con la evidencia del carril B, priorizar primero campos comunes que destraben bloques grandes (quantity_min/step, price_precision, cost_model, conversion_ratio y términos de renta fija) sin convertir decimales en step/tick por inferencia.

### CARRIL E — CHECKPOINT
Actualizar este archivo después del próximo hito material.

---

**Regla para nuevo chat:** leer este archivo primero. No recomenzar ingesta histórica masiva ni volver a diagnosticar puntos ya cerrados salvo evidencia nueva.