# Issue #469 — RA-C independiente: trader/quant, finanzas, FUTUROS e histórico

## Dictamen

**NO_GO para aprobar el candidato #466 en este frente.** El objeto congelado
contiene cuatro defectos de severidad P1 reproducidos: el scalper elude el
portón económico BINDING y falsifica su evidencia; los FUTUROS activos quedan
fuera de la reserva global de consultas de salida; los fills DLR violan el tick
oficial e inventan PnL; y los snapshots de riesgo FUTUROS incorporan eventos
posteriores al corte por pérdida de precisión temporal. El exportador histórico
además incumple READ_ONLY estricto sobre fuentes SQLite WAL al crear un sidecar
`-shm` (P2).

La ingeniería defensiva que sí funciona no prueba rentabilidad. El candidato no
presenta evidencia OOS de edge, su score no es una probabilidad y las cifras
históricas reales ordenadas por #469 no estuvieron disponibles. La revisión del
paquete privado de 20 ruedas, sus 68 posiciones/151 fills y sus resultados queda
**EXTERNAL_NO_VERIFICADO**; no se los sustituyó por datos sintéticos ni se los
infirió de attestations anteriores.

## Objeto, autoridades y seguridad

- Candidato: commit `c27dfd963c4fe83465c0f2105347e974fbbe6356`.
- Tree: `bf3cf193434641aa89e4c746b26c77aec5d1d2b2`.
- Autoridades leídas integralmente: issues #469, #458, #460, #462, #464 y
  #465; `ISSUE465_20SESSION_PACKAGE.md`, `ISSUE465_STRESS.md`,
  `ISSUE465_REAUDIT.md` y `ISSUE465_REAUDIT_ORIGINAL_REPORT.md`.
- Modalidad: revisión estática y probes propios sobre bases temporales
  sintéticas; PAPER/SHADOW; `network_calls=0` en probes, `real_orders_sent=0`,
  `real_routes_used=[]`.
- No se usó fuente privada, PPI Watch, credencial, broker ni proveedor. No hubo
  fix, merge, deploy ni mutación del checkout candidato. El `git status` del
  candidato permaneció vacío y el tree siguió siendo el indicado.
- La descarga pública de la guía A3 fue sólo corroboración documental; título,
  página, hash y límite de vigencia están en
  `audit_evidence/finance/A3_PRIMARY_SOURCE.md`.

## Hallazgos bloqueantes

| ID | Severidad | Resultado | Dominio |
|---|---:|---|---|
| RA-C-01 | P1 | Scalping abre aun cuando su portón económico BINDING calcula `passed=false`, y persiste `passed=true` | trading/riesgo/auditoría |
| RA-C-02 | P1 | Una posición FUTUROS `ACTIVE` se cuenta como cero para la reserva global de salida; discovery puede agotar el endpoint | SRE/riesgo |
| RA-C-03 | P1 | Fills DLR se cuantizan a 4 decimales, no al tick A3 de ARS 0,5/USD; un round trip sintético sobrestima el bruto ARS 368,20/contrato | finanzas/mercado |
| RA-C-04 | P1 | `julianday()` incluye settlement/close a `cut+1µs` en el snapshot del cut | contabilidad/riesgo temporal |
| RA-C-05 | P2 | El supuesto lector READ_ONLY crea `source.sqlite-shm` en una fuente WAL transportada sin SHM | tooling/cadena de custodia |

### RA-C-01 — El scalper elude el portón económico BINDING

**Expected.** Toda apertura con `PAPER_ECONOMIC_GATE_MODE=BINDING` debe pasar la
misma evaluación económica canónica que `PaperBroker.on_quote`; el ledger debe
guardar el resultado realmente calculado.

**Observed.** `cf_intraday_scalping.promote_paper_candidate` carga la economía
del candidato, fuerza `passed=true`, `binding=true` y
`execution_enabled=true`, y llama directamente al método privado
`PaperBroker._open`. La ruta evita `PaperBroker.on_quote`, donde sí se ejecuta
`_economic_diagnostics` y se bloquea un resultado negativo.

Contraejemplo con el `PaperStore`/`PaperBroker` reales del candidato:

- GGAL sintético; 16 puntos válidos; acción `BUY_CANDIDATE`; score saturado en
  `1`; rango previo de 15 minutos `3,4912718204%`.
- Target `2%`, stop `0,8%`, RR neto mínimo `1,20`; costo por tramo completo
  `0,7865%`, tramo rebajado `0,0605%`.
- Diagnóstico canónico: recompensa neta/unidad `1,1597094985`, pérdida
  neta/unidad `1,7300110017`, RR `0,6703480483`, break-even win rate
  `59,86776235%`, `passed=false`.
- Resultado de promoción: `OPENED_SIMULATED`, 100 unidades a `103,8208`, costo
  de entrada `81,66`; gate persistido `APPROVE/APPROVE/OPENED_SIMULATED` con
  economía sobrescrita a `passed=true`.
- Con 50/50 entre target y stop, el valor esperado exacto es
  `(1,1597094985 - 1,7300110017)/2 = -0,2851507516` por unidad, o
  `-28,51507516` para la posición. El baseline nominal no-trade es `0`.
- A los 30 minutos exactos, con libro sin cambio, el supervisor cerró por
  `SCALPING_MAX_HOLD_PAPER`: bruto `-9,16`, costos `87,93`, neto `-97,09`
  (`-0,935169%` del nocional de entrada).

**Path/symbol.** `cf_intraday_scalping.py:729-811` evalúa rango/score;
`:857-949` promueve, sobrescribe la economía en `:924-926` y llama `_open` en
`:937-941`. La ruta correcta está en `be_paper_engine.py:1148-1192`.

**Impact.** El modo BINDING no es binding en scalping. Abre operaciones que la
política declarada rechaza y, peor, deja evidencia durable que afirma lo
contrario. Monitoreos que confían en `economics.passed` no pueden detectar la
violación.

**Test gap.** `tests/test_rc6_intraday_freshness.py:202-217` inyecta un broker
mock y sólo exige que `_open` sea llamado. Las pruebas de health detectan un
`passed=false` almacenado, pero este caller almacena `true`.

**Recomendación no implementada.** Prohibir aperturas por `_open` desde callers;
exponer una única operación atómica de admisión/apertura que calcule el portón
canónico bajo la misma configuración y persista el diagnóstico inmutable. Añadir
un caso real de broker BINDING con RR neto inferior al mínimo y exigir cero
posición, gate BLOCKED y diagnóstico no sobrescrito.

### RA-C-02 — FUTUROS no reserva presupuesto global para salir

**Expected.** Cada posición que necesita supervisión de salida debe aumentar la
demanda `EXIT_CRITICAL` antes de admitir discovery/scalping.

**Observed.** `RuntimePPIBudget._opened` ejecuta `status='OPEN'` tanto para
`paper_positions` como para `paper_future_positions`. El schema FUTUROS sólo
permite `ACTIVE` o `CLOSED`; el reader de salida, correctamente, selecciona
`ACTIVE`.

En un ledger temporal con un lifecycle DLR realmente activo:

- posiciones futuras activas: `1`; `_opened()`: `0`;
- demanda/reserva de book calculada: `0/0`;
- tres solicitudes `DISCOVERY` fueron admitidas; la siguiente
  `EXIT_CRITICAL` fue rechazada con `PPI_BUDGET_EXHAUSTED`;
- con conteo correcto, la demanda era `6`, la reserva limitada era `3`,
  discovery recibía `PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE` y las tres lecturas
  de salida eran admitidas.

**Path/symbol.** `rc6_ppi_global_budget.py:840-855`;
`rc6_paper_family_lifecycle.py:480-493,708-725`;
`bv_paper_runtime.collect_exit_books:172-208`.

**Impact.** La coexistencia FUTUROS + discovery/scalping puede dejar una
posición sin book de salida precisamente bajo presión de cuota. Reabre el riesgo
de starvation que la reserva global debía cerrar.

**Test gap.** `tests/test_issue465_budget_adversarial.py:1261-1277,1364-1376`
crea únicamente posiciones spot `OPEN`; no inserta un FUTURO `ACTIVE` y no
prueba la semántica cruzada de estados.

**Recomendación no implementada.** Definir predicados de actividad por familia
o una vista canónica de posiciones supervisables; derivar demanda y exit-reader
de la misma autoridad. Agregar prueba integrada con spot `OPEN`, future `ACTIVE`,
discovery concurrente y reserva persistida/reiniciada.

### RA-C-03 — Precio FUTUROS fuera del tick oficial e invención de PnL

**Autoridad primaria.** El endpoint público A3 sirvió el 4 de octubre de 2026 el
PDF de 8 páginas `Guia de Producto FyO DOLAR [VIGENTE]`, SHA-256
`11c8a2ac9cc2b050bee36c70c8c5f95e8007bc21ec1d6d41984bbdf99ee0c506`.
Su página impresa 4, sección 2.b, fija contrato USD 1.000, cotización y
compensación en ARS y variación mínima ARS 0,5 por USD. El documento no incluye
fecha de publicación; por eso sólo se afirma vigencia al momento de la
recuperación, no vigencia histórica.

**Expected.** Todo precio simulado DLR debe estar en la grilla de ARS 0,5 y el
slippage debe redondearse en dirección adversa.

**Observed.** `InstrumentContract` no tiene `price_tick`. `_open_future` y
`_close_future` aplican slippage y cuantizan a `0.0001`:

- ask válido `1579,0`; compra a `1579,3158`, resto sobre tick `0,3158`; el tick
  ejecutable adverso es `1579,5`. Con multiplicador 1.000, se subestima el costo
  de entrada ARS `184,20` por contrato.
- bid válido `1580,0`; venta a `1579,6840`, resto `0,1840`; el tick ejecutable
  adverso es `1579,5`. Se sobreestima la salida ARS `184,00`.
- El round trip atribuye ARS `368,20` de PnL bruto inexistente por contrato,
  antes de tarifas.

El probe usó la ruta especializada real y tanto open como close fueron
aceptados. No es una discrepancia cosmética: modifica cash, exposición,
variación y métricas de performance.

**Path/symbol.** `bs_instrument_contracts.InstrumentContract:74-149` carece de
tick; `be_paper_engine.PaperBroker._open_future:1358-1361` y
`_close_future:1480-1489` cuantizan a cuatro decimales.

**Test gap.** Las pruebas validan multiplicador, cantidad entera, identidad y
lifecycle, pero no la grilla de precio ni la dirección de rounding en open,
mark, settlement y close.

**Recomendación no implementada.** Incorporar tick con procedencia contractual,
validar books/marks y cuantizar cada lado adversamente sobre esa grilla. Cubrir
límites de medio tick, slippage, settlement, restart y reconciliación de PnL.

### RA-C-04 — Lookahead contable de hasta submilisegundos

**Expected.** Un snapshot inclusivo `as_of=t` jamás puede contener un evento o
mark con timestamp textual `>t`.

**Observed.** SQLite `julianday()` colapsó `t+1µs`, `+100µs` y `+499µs` al mismo
valor que `t`; `+500µs` se representó como aproximadamente `+1,005828ms`.

- Un settlement en `13:00:00.000001-03:00`, consultado a
  `13:00:00.000000-03:00`, cambió realizado `-100→9.900`, exposición
  `1.500.000→1.510.000` y cash `-1.500.100→-1.490.100`. El snapshot devolvió el
  timestamp posterior del mark; marcarlo stale no evitó su efecto monetario.
- Un close en `t+1µs`, consultado en `t`, cambió active count `1→0`, collateral
  `1.500.000→0`, realizado `-100→19.800` y cash `-1.500.100→19.800`.
- Control positivo con tiempos normales: open/settlement/close dio neto exacto
  `19.800`; un executor nuevo repitió el close idempotentemente sin evento extra.

**Path/symbol.** `rc6_paper_family_lifecycle.future_cash_effect:686-705` y
`future_risk_snapshot:728-809`, en particular los filtros
`julianday(occurred_at)<=julianday(?)` y
`julianday(observed_at)<=julianday(?)`.

**Impact.** VaR/capacidad/caja/PnL point-in-time dejan de ser causales en el
borde. Un cierre posterior puede liberar garantía y reconocer ganancia antes de
ocurrir, contaminando decisiones y reportes de auditoría.

**Test gap.** No hay casos `cut±1µs` para events y marks ni una aserción de que
cada timestamp devuelto sea `<=cut` con comparación de datetimes aware.

**Recomendación no implementada.** Usar SQL sólo como pre-filtro grueso y hacer
comparación/orden exactos con datetimes aware antes de sumar; preservar un orden
total determinista. Probar ±1µs, offsets equivalentes, DST/UTC y empate por ID.

### RA-C-05 — El exportador READ_ONLY escribe un sidecar WAL

**Expected.** Una herramienta que declara
`source_access=READ_ONLY/mode=ro/query_only/SELECT_ONLY` y
`runtime_mutation=false` no debe cambiar bytes ni inventario del árbol fuente.

**Observed.** Se creó una base WAL sintética, se transportaron
`source.sqlite` + `source.sqlite-wal` sin SHM y se exportó con la API real. La
operación terminó correctamente, el hash del archivo principal no cambió, pero
SQLite creó junto a la fuente `source.sqlite-shm` de 32.768 bytes.

**Path/symbol.** `rc6_audit_evidence/package.py:149-204` abre
`file:?mode=ro`, activa `query_only` y authorizer SELECT; el manifest declara las
propiedades en `:458-478`.

**Impact.** Viola READ_ONLY estricto y la cadena de custodia de una fuente
forense. En un filesystem verdaderamente read-only puede convertir un caso WAL
válido en fallo operativo; en uno escribible deja una mutación no declarada.

**Test gap.** La prueba existente verifica bytes del DB principal sobre una
fixture no-WAL; no compara inventario/metadata del directorio ni ejecuta sobre
mount sin escritura.

**Recomendación no implementada.** Leer una snapshot materializada y validada
fuera del árbol fuente, o exigir una captura SQLite coherente con todos los
sidecars bajo una estrategia que no escriba. Antes/después deben compararse
archivos, hashes, metadata y ejecución sobre directorio sin permisos de escritura.

## Evaluación trader/quant: edge, costos y baseline

Las cifras siguientes de #464/#465 son **alegadas**, no revalidadas contra la
fuente privada. La aritmética sí es reproducible:

| Moneda | Closes alegados | Bruto | Costos | Neto | Bruto/close | Costo/close | Neto/close | Costos / \|bruto\| |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ARS | 62 | -10.954,5809 | 23.445,66 | -34.400,2409 | -176,6868 | 378,1558 | -554,8426 | 214,02% |
| USD_MEP | 6 | -1,4439 | 2,64 | -4,0839 | -0,24065 | 0,44 | -0,68065 | 182,84% |

- Incluso a costo cero, ambos brutos alegados son negativos. El problema no se
  explica sólo por fees.
- El baseline no-trade nominal `0` supera ambos netos. No se modelaron inflación,
  remuneración del cash ni costo de oportunidad, por lo que `0` no es una curva
  de inversión completa; sí es el contrafactual mínimo pertinente para decidir
  si ejecutar esta señal.
- Con bruto fijo, `net(s)=gross-cost×(1+s)`. Los stresses ARS de +25/+50/+100%
  dan `-40.261,6559/-46.123,0709/-57.845,9009`; USD da
  `-4,7439/-5,4039/-6,7239`. Sólo validan aritmética sobre agregados no
  autenticados; no son backtest, OOS ni distribución de slippage.
- Win rate y profit factor alegados (ARS `17,7419%`, PF `0,1003815553`) son
  incompatibles con una afirmación prudente de edge, pero tampoco se adoptan
  como evidencia primaria sin el paquete.
- El score `0..1` es un clipping lineal de momentum y spread. En el probe el
  valor crudo excedió uno y se saturó. No hay calibración probabilística,
  reliability curve, Brier/log loss, estabilidad temporal ni OOS que autorice
  interpretar `1` como certeza o invertir la señal por un AUC pasado bajo 0,5.
- El filtro `observed_15m_range > required_move` compara recorrido pasado con
  hurdle de costos; no estima la probabilidad futura de tocar target antes que
  stop/EOD/MaxHold.
- TP, SL, EOD y MaxHold son motivos de intención/salida PAPER. Un gap, book
  stale, falta de liquidez o spread mayor puede cambiar el fill. Robustez del
  lifecycle no convierte esos umbrales en payoff garantizado.
- ARS y USD_MEP deben permanecer separados. No hubo tipo de cambio ni autoridad
  para sumar resultados entre monedas.

Conclusión quant: **EDGE_NO_DEMOSTRADO**. No se recomienda sign flip, retuning de
threshold ni optimización sobre estas 20 ruedas; cualquiera sería curve fitting
sin holdout y sin autenticación de la cohorte.

## FUTUROS: controles que sí pasaron y límites

- La semántica PAPER estándar DLR de multiplicador `1000`, moneda ARS, mercado
  A3, plazo inmediato, lote entero y reserva conservadora del 100% del nocional
  fue consistente en el control sintético.
- Un contrato a 1500 requiere ARS 1.500.000. Con defaults de capital ARS
  1.000.000, cap por posición 25% y exposición total 60%, no puede abrir. Es un
  bloqueo conservador/operativo, no un bug ni evidencia de edge.
- La variación diaria y el cierre normal reconciliaron: 1500→1510 realizó
  10.000; 1510→1520 agregó 10.000; menos 100+100 de costos = 19.800. El cierre
  liberó la garantía y el replay fue idempotente.
- BINDING FUTUROS cierra por diseño porque falta un modelo exacto de costos. En
  SHADOW puede usar estimación; eso no valida derechos, clearing, IVA, tarifa de
  cuenta ni margen dinámico.
- Sólo se ejecuta LONG y sólo el patrón DLR estándar 2026 fijado. Horarios
  excepcionales, feriados, halts, márgenes/fees vigentes, identidad PPI y books
  reales siguen externos.
- La guía A3 respalda características generales del contrato, no disponibilidad
  de una serie en PPI ni condiciones de una cuenta. No se extrapoló una serie a
  otra.

## Tooling de 20 ruedas

### Resultado sintético positivo

Con una cohorte de 20 sesiones y una posición cerrada con tres fills (entrada y
dos salidas parciales), el paquete fue byte-determinista aun invirtiendo el orden
de sesiones; conservó parciales; recomputó bruto `6`, costos `3`, neto `3`;
separó monedas; no expuso marcadores crudos de posición/cuenta/token/chat; y no
cambió el hash del archivo DB principal.

### Corrupciones adversariales

| Mutación | Observado |
|---|---|
| Byte de fill sin rehash | `FILE_DIGEST_MISMATCH` |
| Costo alterado y rehashed | `LEDGER_COST_RECONCILIATION` |
| Cantidad de entrada alterada y rehashed | `OPENING_QUANTITY_MISMATCH` |
| Fill ID duplicado y rehashed | `DUPLICATE_OR_INVALID_FILL` |
| Fill `1µs` anterior a la entrada y rehashed | `FILL_CLOCK_ORDER` |
| Count del manifest con pin original | `MANIFEST_DIGEST_MISMATCH` |
| Count del manifest con pin nuevo | `ROW_COUNT_MISMATCH` |
| Límite de posiciones/fills | `POSITION_ROW_BUDGET_EXHAUSTED` / `FILL_ROW_BUDGET_EXHAUSTED` |
| ARS→USD_MEP con pin original | `MANIFEST_DIGEST_MISMATCH` |

Un relabel ARS→USD_MEP coordinado, con hashes públicos regenerados y un nuevo
`expected_manifest_sha256` suministrado por el mismo actor, fue aceptado; el
commitment HMAC opaco de la fila fuente no cambió. Esto no contradice el diseño
documentado: demuestra que el pin recién entregado no autentica la fuente. Se
requiere retención independiente del digest original y reconciliación con el
owner/seed de origen.

### Lo que no puede certificarse

Sin el master privado no puede confirmarse: 20 sesiones reales completas,
68/151 filas, ausencia de duplicados reales, fills parciales reales, clocks de
proveedor, costos y tarifa de cuenta, monedas, corporate actions, integridad de
WAL/snapshot, selección de cohorte, ni los resultados publicados. Estado:
**EXTERNAL_NO_VERIFICADO**.

## Matriz exhaustiva y reproducibilidad

La matriz independiente contiene 66 escenarios con las columnas exigidas
`EXPECTED / OBSERVED / PATH / TEST / GAP / VERDICT` en
`audit_evidence/finance/SCENARIO_MATRIX.md`.

Artefactos propios, todos fuera del checkout congelado:

- `audit_evidence/finance/scalping_economics_probe.py`
- `audit_evidence/finance/futures_point_in_time_probe.py`
- `audit_evidence/finance/historical_package_probe.py`
- `audit_evidence/finance/PROBE_RESULTS.md`
- `audit_evidence/finance/A3_PRIMARY_SOURCE.md`
- `audit_evidence/finance/SCENARIO_MATRIX.md`

Los probes son autocontenidos, usan `tempfile`, datos sintéticos y Decimal, y no
invocan red/rutas reales. Los resultados materiales exactos están resumidos en
`PROBE_RESULTS.md`; la disponibilidad del paquete real no fue simulada.

## Perspectiva de comprador institucional

No compraría ni promovería este build como motor PAPER financieramente fiel sin
cerrar RA-C-01 a RA-C-05 y repetir una auditoría independiente sobre el mismo
SHA corregido. La prioridad no es ajustar señales: primero debe existir una sola
autoridad de admisión económica, salida protegida para toda familia, fills sobre
grilla contractual, contabilidad point-in-time causal y tooling forense que no
toque la fuente. Después, y por separado, debe entregarse la cohorte real
autenticada y evaluarse edge OOS contra no-trade y costos completos por moneda.

Hasta entonces: **PAPER/SHADOW ONLY, `real_orders_sent=0`, NO MERGE, NO DEPLOY**.
