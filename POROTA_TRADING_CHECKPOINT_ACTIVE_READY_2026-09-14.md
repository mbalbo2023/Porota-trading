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
- Último HEAD previo a esta actualización: `ac6e79fab0cd1e56e8e980ade563a0db4fd4dcfe`
- Universo histórico PPI masivo: `1960`
- `candidate_universe`: `911` filas observadas
- Modo obligatorio: `PRODUCTION_PAPER`
- Órdenes reales: `0`
- FCI y OPCIONES: excluidos REVERSIBLEMENTE del target del 2026-09-14; no borrar código/datos/histórico.
- 18 producers/timers: mantener pausados hasta gate integral.

Último safety snapshot read-only confirmado:
- `porota-ppi-web-residual-rc6.service`: inactive/dead, Result=success, MainPID=0
- `porota-ppi-fullfamily-history-rc6.service`: inactive/dead, Result=success, MainPID=0
- procesos writers históricos detectados: 0
- observer DB `quick_check=ok`
- `can_simulate=1 AND AVAILABLE`: `246`
  - ACCIONES: `55`
  - CEDEARS: `191`

## 2. DOS INGESTAS DISTINTAS — NO CONFUNDIR

### A. HISTÓRICO DE PRECIOS / VELAS

Sirve al motor de decisión. La ingesta masiva PPI API ya cerró y la corrida PPI Web residual también cerró. NO repetir ninguna completa. Reconciliar stores y adquirir sólo residuales verdaderos y dirigidos.

### B. CONTRATOS / METADATOS OPERATIVOS

Sirve para saber CÓMO operar correctamente un instrumento: identidad, mercado, moneda, settlement real, min/step/lote, precisión/tick, nominal, lámina mínima, ISIN, vencimiento, cupón/amortización, ratio CEDEAR, multiplicadores/margen de futuros, cauciones, costos, etc.

Precedencia por campo:
1. PPI API estructurada válida.
2. PPI Web/XHR autenticada como complemento.
3. IOL sólo residual final después de reconciliar PPI, salvo decisión explícita.

Histórico completo != contrato completo != READY end-to-end.

## 3. HISTÓRICO PPI — ESTADO CERRADO

PPI API full-family: corrida masiva cerrada sobre `1960`; NO rerun completo.

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
- residual verdadero: 7 CEDEAR

Los 7:
`CRWVC`, `CVSC`, `MRVLC`, `VIVTC`, `VIVTD`, `VRTXC`, `VXXC`.

### RCA PPI API de los 7

Los 7 son vigentes en catálogo/API, están en `candidate_universe` como `can_simulate=1`, `AVAILABLE`, `READY_PAPER_SPOT`, tienen market snapshots y actividad PAPER. No son simples aliases ni símbolos muertos.

En ledgers PPI históricos aparecen repetidamente `provider_rows=0`, `valid_rows=0`, `EMPTY_OR_INVALID`; en `ppi_history_ingest_tasks` quedaron `DONE_EMPTY` con provider=0.

### RECONCILIACIÓN PPI WEB DE LOS 7 — CERRADA

Workflow `RC6 READY reconcile contract gaps 2026-09-13`, run `34771710083`, job `cedear7-web-reconcile`, success.

Los SIETE fueron intentados también por la corrida PPI Web cerrada y los SIETE terminaron:
- `DONE_EMPTY`
- `residual_class=PROVIDER_EMPTY`
- discovery/detail/plazos/history HTTP `200`
- `provider_rows=0`
- `valid_rows=0`
- settlement `A-24HS`

Item/type IDs observados:
- CRWVC item 115088 / type 152
- CVSC item 114014 / type 152
- MRVLC item 114753 / type 152
- VIVTC item 114129 / type 152
- VIVTD item 114130 / type 152
- VRTXC item 115304 / type 152
- VXXC item 115084 / type 152

**Conclusión canónica:** PPI API + PPI Web están agotados/vacíos para el histórico de estos 7. NO repetir scraping PPI. Los 7 quedan habilitados para residual final read-only en IOL según la política acordada.

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
- reutilizar `ticker/market/settlement` de identidad canónica cuando es unánime;
- fail-closed si record key o payload discrepan;
- nunca autoactivar.

Regresiones: `7/7 PASS`.

### MATRIZ CONTRACTUAL POST-FIX

Run `34771504731`, job `contract-postfix-matrix`, success; evaluador real `family_readiness_state`.

- identidades v2: `447`
- identidades target tras excluir FCI/FCI_EXTERIOR/OPCIONES actuales: `444`
- conflictos detectados: `0`
- `READY_PAPER_CANDIDATE` contractualmente completos: `0`
- quick wins con <=2 campos faltantes: `0`

El falso faltante de `ticker/market/settlement` desapareció. Los faltantes restantes son reales según el gate actual.

Por familia:
- ACCIONES 55: faltan en 55 `cost_model`, `price_precision`, `quantity_min`, `quantity_step`; currency1/instrument_id1.
- CEDEARS 192: faltan en 192 `conversion_ratio`, `cost_model`, `price_precision`, `quantity_min`, `quantity_step`; currency1/instrument_id1.
- BONOS 43: faltan en 43 `amortization_terms`, `cost_model`, `coupon_terms`, `isin`, `lamina_minima`, `maturity`, `nominal_value`, `price_precision`, `price_unit_nominals`, `quantity_min`, `quantity_step`; currency1/instrument_id1.
- LETRAS 18: faltan en 18 `cost_model`, `lamina_minima`, `maturity`, `nominal_value`, `price_precision`, `price_unit_nominals`, `quantity_min`, `quantity_step`; currency2/instrument_id2.
- ON 85: faltan en 85 `amortization_terms`, `cost_model`, `coupon_terms`, `isin`, `lamina_minima`, `maturity`, `nominal_value`, `payment_currency`, `price_precision`, `price_unit_nominals`, `quantity_min`, `quantity_step`; currency1/instrument_id1.
- FUTUROS 43: faltan masivamente campos especializados.
- CAUCIONES: evidencia especializada incompleta.

## 8. KEYS REALES YA CAPTURADAS EN CONTRACT EVIDENCE V2

Run `34771710083`, job `contract-key-gap`, success.

### PPI_AUTHENTICATED_XHR

ACCIONES 55:
- instrument_id55
- currency54
- market55
- commission_percent55
- financial_rights_percent55
- price_decimal_places55
- quantity_decimal_places55
- description55

CEDEARS 192:
- instrument_id192
- currency191
- market192
- commission_percent192
- financial_rights_percent192
- price_decimal_places192
- quantity_decimal_places192
- quantity_operated192
- NO conversion_ratio

BONOS 43:
- instrument_id42
- currency42
- market43
- commission_percent43
- price_decimal_places43
- quantity_decimal_places43
- NO términos técnicos de renta fija en v2

LETRAS XHR 16/18:
- instrument_id16
- currency16
- market16
- commission_percent16
- price_decimal_places16
- quantity_decimal_places16

ON XHR 84/85:
- instrument_id84
- currency84
- market84
- commission_percent84
- price_decimal_places84
- quantity_decimal_places84
- NO términos técnicos de renta fija en v2

CAUCIONES XHR:
- evidencia agregada de filas de caución/comisiones, todavía no normalizada a los campos especializados del gate.

**Interpretación:** `instrument_id`, moneda, comisiones y decimales ya están ampliamente capturados. No asumir que decimales = step/tick. Hay que mapear sólo equivalencias semánticamente válidas y capturar lo genuinamente ausente.

## 9. COLECTOR CONTRACTUAL EXISTENTE

La rama canónica contiene código reutilizable:
- `rc6_trusted_browser_contract_collector.py`
- `rc6_contract_capture_importer.py`
- `rc6_ppi_contract_normalizer.py`

Collector:
- targets `InstrumentosOperables`, `CaucionesOperables`, `ConfiguracionOperatoriaSimplificada`, `SubyacenteOpciones`, `DatosTecnicos`
- sólo GET/HEAD/OPTIONS después de validar sesión
- bloquea non-read
- no rellena cantidad/precio
- sin imports de órdenes
- sanitiza respuestas
- no auto-enable

Importer:
- importa InstrumentosOperables/CaucionesOperables/DatosTecnicos a Contract Evidence v2
- sólo evidence/audit
- no cambia eligibility ni activa instrumentos

Normalizer:
- InstrumentosOperables mapea instrument_id, description, market, currency, commission_percent, financial_rights_percent, price_decimal_places, quantity_decimal_places y observaciones de cantidad/precio.
- DatosTecnicos extrae ISIN, lámina mínima, fecha de vencimiento, intereses, amortización y otros términos.
- deliberadamente NO infiere order step/tick desde cantidad de decimales.

### AUDITORÍA DE CAPTURAS EXISTENTES

Run `34771710083`, job `capture-artifact-audit`, success.

Se localizaron `97` capturas históricas `POROTA_RC6_PPI_TRUSTED_CONTRACT_V1`; muchas con `auth_status=AUTHENTICATED_PRIVATE_PAGES_RENDERED` y `real_orders_sent=0`.

Pero en las 97 el formato útil esperado por el collector actual está vacío:
- `endpoint_kinds={}`
- `row_keys={}`
- `jobs={}`

Por lo tanto esas capturas no aportan hoy payload técnico reutilizable de `DatosTecnicos`/`InstrumentosOperables` en la estructura esperada. No alcanza con reimportarlas.

**Corrección de diagnóstico:** existe el código del collector en la rama; lo que aún NO está demostrado es una invocación/despliegue vivo que capture esos endpoints con el formato actual. NO construir otro collector desde cero: primero auditar cómo invocarlo con la sesión trusted existente y ejecutar sólo una captura estrecha/read-only si es seguro.

## 10. MAPEOS POTENCIALES — REQUIEREN VALIDACIÓN, NO ASUMIR

Posibles equivalencias del normalizador que deben confirmarse contra el gate/código antes de escribir:
- `laminaMinima -> lamina_minima`
- `fechaVencimiento -> maturity`
- `intereses -> coupon_terms`
- `amortizacion -> amortization_terms`
- `price_decimal_places -> price_precision` sólo si el contrato interno define precision y no tick
- `commission_percent` + `financial_rights_percent` podrían alimentar `cost_model` sólo si coincide con la convención interna del motor

Prohibido inferir:
- `quantity_min` o `quantity_step` desde `quantity_decimal_places`
- price tick desde precisión decimal sin regla explícita
- `conversion_ratio` CEDEAR si no existe evidencia real
- nominal/order units de renta fija si no están explícitas.

## 11. REGLAS DE CONSOLIDACIÓN / SETTLEMENT

- identidad canónica por familia+ticker+mercado+settlement;
- PPI API prima cuando es válida;
- XHR/Web complementa campo por campo;
- no overwrite silencioso;
- provenance obligatoria;
- conflictos críticos => fail-closed;
- no fuzzy settlement;
- alias explícito `MRCTO -> MRCAC`;
- no datos sintéticos.

Settlement:
- BYMA estándar desde 2024-05-27: T+1 / 24HS
- CI=T+0
- 48HS legado
- usar `PlazosOperables` reales.

## 12. SEMÁFORO ACTUAL

- 🟢 checkpoint continuidad
- 🟢 safety PAPER / real orders 0
- 🟢 writers históricos detenidos
- 🟢 246 simulables actuales = 55 Acciones + 191 CEDEAR
- 🟢 histórico Acciones 55/55
- 🟡 histórico CEDEAR 184/191
- 🟢 RCA PPI API de CEDEAR7 cerrado
- 🟢 RCA PPI Web de CEDEAR7 cerrado
- 🟡 CEDEAR7: PPI agotado; habilitados para residual IOL read-only
- 🟢 fix identidad/conflictos 7/7 tests
- 🟢 matriz contractual post-fix exacta
- 🔴 Contract Evidence v2 completo: 0/447 bajo requisitos estrictos actuales
- 🟢 inventario exacto de keys XHR existentes
- 🟡 collector contractual: código existe; invocación/trusted profile viva pendiente de auditar
- 🔴 97 capturas previas: autenticadas pero sin endpoints estructurados útiles
- 🟡 BONOS/LETRAS/ON: prioridad contractual
- 🟡 CAUCIONES/FUTUROS: contratos especializados pendientes
- ⚪ FCI/OPCIONES: fuera del target inmediato, reversiblemente
- 🟡 IOL: permitido AHORA sólo para los 7 CEDEAR residuales exactos y en modo read-only
- ⛔ 18 producers/timers: pausados
- 🔴 READY end-to-end integral: aún no demostrado

## 13. NO HACER

- no órdenes reales;
- no salir de PRODUCTION_PAPER;
- no rerun masivo PPI API/Web;
- no segundo writer histórico;
- no borrar/resetear state.sqlite3/tasks;
- no blind retry;
- no inventar contratos/steps/ticks;
- no usar IOL fuera del residual exacto sin nueva reconciliación/decisión;
- no reactivar 18 producers/timers;
- no usar `READY_PAPER_SPOT` visual como autorización final.

## 14. ÚLTIMO PASO CONFIRMADO

Workflow `RC6 READY reconcile contract gaps 2026-09-13`, run `34771710083`, terminó `3/3 success`.

Confirmado:
1. los 7 CEDEAR son provider-empty tanto en PPI API como en PPI Web, ambos con evidencia cerrada; PPI queda agotado para esos 7;
2. los 7 pasan legítimamente a residual final IOL read-only; no repetir PPI;
3. Contract Evidence v2 ya contiene instrument_id/currency/comisiones/decimales para grandes bloques, pero no order constraints, ratios CEDEAR ni términos completos de renta fija;
4. 97 capturas trusted previas están autenticadas pero no contienen endpoint_kinds/row_keys/jobs útiles para el importer actual;
5. el collector/importer/normalizer existen en la rama, por lo que no se debe crear una arquitectura paralela.

## 15. SIGUIENTE ACCIÓN EXACTA

Ejecutar en paralelo, sin compartir writers:

### CARRIL A — IOL RESIDUAL FINAL CEDEAR7
- usar sólo endpoints read-only del conector IOL;
- buscar histórico de `CRWVC,CVSC,MRVLC,VIVTC,VIVTD,VRTXC,VXXC`;
- no órdenes ni operaciones;
- documentar cobertura y procedencia;
- si IOL tampoco tiene histórico, mantenerlos bloqueados por history gap en vez de inventar datos.

### CARRIL B — INVOCACIÓN DEL COLLECTOR EXISTENTE
- localizar scripts/workflows que usan `rc6_trusted_browser_contract_collector.py` y el trusted profile/session;
- auditar seguridad e inputs antes de ejecutar navegador;
- si queda seguro, hacer UNA captura representativa estrecha/read-only para comprobar `InstrumentosOperables` + `DatosTecnicos`, no mass scrape.

### CARRIL C — MAPEO SEMÁNTICO CONTRACTUAL
- localizar convención exacta de `price_precision`, `cost_model`, `quantity_min`, `quantity_step`, `conversion_ratio` y términos de renta fija en código/catalog/runtime;
- reutilizar campos existentes sólo donde la equivalencia sea explícita;
- no inferir step/tick desde decimales.

### CARRIL D — SAFETY
- mantener writers históricos apagados, PAPER y orders=0;
- cualquier write/promoción sobre runtime debe quedar serializado y posterior a validación.

### CARRIL E — CHECKPOINT
- actualizar este archivo después del próximo hito material.

---

**Regla para nuevo chat:** leer este archivo primero y continuar desde aquí. No recomenzar ingesta histórica masiva ni repetir PPI Web/API para los 7 CEDEAR.