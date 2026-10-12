# RC6: aceptación financiera y revisión independiente del core

Estado: código verificado offline; evidencia externa de cuenta y master exacto
de veinte sesiones `EXTERNAL_NO_VERIFICADO`. `EDGE_NO_DEMOSTRADO`. Sin tuning,
sin nuevas familias/shorts, sin ruta de orden real y sin modificación del host.
La matriz por campo está en [field_authority_matrix.md](field_authority_matrix.md).

El [consolidado JSON](consolidated_finance_evidence.json) liga U09/U10/U22/U23,
AUD-468-19, el lector financiero U18/I04, R19/R74/R75/R76/R78 y el contrato de costos a RCA,
paths, nodos pytest sin multiplicar parámetros y receipts. Conserva los66 IDs
C-01…C-66 del original, su EXPECTED/OBSERVED/PATH/TEST/GAP/VERDICT y referencias
nativas por capa. Un nodo referenciado no es un receipt de ejecución ni prueba
por sí solo de toda la expectativa original; los gaps externos/edge se mantienen.

## Reproducción independiente RED → GREEN

El probe [rc6_convergence_finance_probe.py](../../../tests/probes/rc6_convergence_finance_probe.py)
conserva los inputs financieros originales de #469 y llama al broker/ledger
reales con bases temporales. Prohíbe conexiones socket y registra intentos.

| ID | RED exacto c27dfd963c4fe83465c0f2105347e974fbbe6356 | GREEN integrado de trabajo | Invariante probado |
| --- | --- | --- | --- |
| U09 | OPEN1579.3158 y CLOSE1579.6840; ventaja artificial ARS368.20 por contrato respecto de tick0.5 | OPEN1579.5 y CLOSE1579.5; error0 | Fills BUY/SELL discretizados adversamente; books secuenciales, sin BBO simultáneo cruzado |
| U10 | 9 variantes posteriores de MARK/SETTLEMENT/CLOSE a +1/+100/+499µs alteran snapshot del corte | 27 variantes ±1/100/499/500µs y empate pasan | Cash/collateral/PnL/activos no reciben contribución posterior; igualdad inclusiva explícita, offsets UTC/ART y restart |
| U22 | Riesgo al mismo cut cae1500100→0 después de CLOSE posterior | Se conserva1500100 | Capacidad reconstruye ACTIVE histórico, aunque la fila actual sea CLOSED |
| U23 | Retry mismo event_id con monto1000→2000 y hora distinta aceptado | Colisión rechazada; retry equivalente válido | Una sola mutación durable; fingerprints de payload/identidad/clocks/contrato, carreras multiproceso en tests |

[red_c27dfd9.json](red_c27dfd9.json) corresponde a `git archive` del SHA exacto,
tree `bf3cf193434641aa89e4c746b26c77aec5d1d2b2`, no a la rama mutable.
[green_integrated_worktree.json](green_integrated_worktree.json) registra HEAD
389a51fca7e920f04f9d12df89e9dddad2084303 y hashes por archivo, pero está marcado
`WORKTREE_NOT_FROZEN`: incluye cambios todavía sin commit en ese instante y
**no acredita el artefacto final**. Ambos usan el mismo probe SHA256
e014a6a7a2e6a7e3e4a4913575780b97a1c15237c4755546534e0b2083497e6e;
ambos registran network_attempts=0, real_orders_sent=0 y real_routes_used=[].

El integrador debe repetir el probe y suites nativas sobre su SHA/tree finales
congelados, conservar configuración/digest/schema/cut/seed/env y comprobar
hashes antes/después. Un test verde en otro SHA o sobre un worktree mutable no
sustituye ese receipt. El rerun final queda expresamente pendiente del freeze
del integrador; no se autoacepta una certificación externa pendiente.

## Mapeo de código y controles preservados

| Requisito | API/camino | Evidencia nativa |
| --- | --- | --- |
| U09 y §7.5 precios | InstrumentContract.price/executable_fill, exact standard_dlr_terms, bridge tick provenance | test_rc6_convergence_finance: grilla, medio tick, precisión oficial1510.123456, close1511, variación10123.456 y net10800; source/rule ausente bloquea |
| U10 | utc_microseconds/rc6_instant_us; future_cash_effect y future_positions(as_of,exclusive,lifecycle_ids) | Cut±µs, UTC/ART, orden total persistido, exclusión, DST fold0/fold1, book futuro/corrupto failclosed |
| U22 | future_risk_snapshot y portfolio_capacity al cut | Capacidad previa idéntica tras terminal posterior, reserva conservadora separada de loss/fees |
| U23 | intent_fingerprint y paper_request_v1 | Cambios de amount/currency/time/detail/identity/contracts rechazados; equivalencia Decimal/offsets; retry implícito postexpiry; carrera real multiproceso |
| #453 preservado | Snapshot de contrato raw validado antes de defaults; restart; OPEN/MARK/SETTLEMENT/CLOSE/EXPIRY existentes | Suites nativas future_paper_lifecycle/future_programming_complete/future_contract_policy/final_shared_risk; fixture legacy digest sin rewrite |
| §7.5 costos | VersionedPaperCostContract, FeeTier, paper_cost_contract/binding_error; policy_sha256 persistido | Componentes desconocidos cierran, minima/tiers/rebate, clocks/moneda/FX/rule separados, costos Decimal combinados una vez por fill |
| U18/I04 | future_positions ID SQL-filter, columnas explícitas, JSON32KiB SQL CASE y cursor de eventos | Trace real sin SELECT*, límites position/event/mark adversos failclosed; proyección sólo página solicitada, sin materializar historia completa |
| AUD19 atómico | open_future(commit_evidence=callable(connection,result)) en mismo BEGIN IMMEDIATE | Error de almacenamiento revierte OPEN/MARGIN/posición/evidence; retry legítimo no recaptura; snapshot/hash firstcapture |

113 casos focales pasan en el worktree financiero en 736795c1. Son variantes
de fronteras y preservación, no 113 escenarios económicos independientes.
El [receipt focal](finance_focused_receipt.json) y
[JUnit focal](finance_focused_tests.xml) registran source/file hashes,
configuración/digest, schema, fixture/seed/cut y entorno; hashes iguales antes
y después del run. El [receipt de revisión core](core_finance_review_receipt.json)
y [JUnit de revisión](core_finance_review_tests.xml) registran10/10 a
2026-10-05T02:16:23Z en root HEAD86269ae3be401613d0d276c731ce1bda5ce0b4f0,
WORKTREE_NOT_FROZEN y sin cambio de los archivos revisados durante el run.
El JUnit histórico integrado [integrated_native_tests.xml](integrated_native_tests.xml)
registra167 casos/0fail a 2026-10-05T01:46:01Z, antes de los últimos followups;
su source mutable no fue congelado y tampoco es prueba final. Los receipts
posteriores etiquetan el source exacto y cualquier dirty state explícitamente.

## Revisión independiente BE/CF/BV/BW

La revisión fue READ_ONLY: el integrador editó core. Se reprodujeron con
callers reales y DBs temporales tres guards faltantes, que el integrador
corrigió conservando los inputs adversos:

1. `_close_future` directo aceptaba bid1480.123 off-grid y bid1480/ask1479
   cruzado. El fill adverse redondeado ocultaba el book inválido. Ahora exige
   validez de BBO y grilla QUOTE antes de ejecutar; book válido sigue cerrando.
2. Cambio de FEE_ACCIONES_COMISION de0.006→0.010 al segundo guard económico
   dentro del lock: fill84.60 y diagnóstico que correspondería136.67. El
   candidato ahora fija fingerprint de costos y rechaza cambio antes del
   commit; no mezcla diagnóstico, costo y candidate_stop_risk de policies
   distintas. Se prueba SHADOW y BINDING, además del control positivo estático.
3. Quote.time_error con America/New_York01:30 fold0 y book01:30 fold1 aceptaba
   una fuente1h futura. La comparación por UTC microsegundos distingue ambos
   instantes, también para receipt stale; fuente/recepción frescas pasan.

Se reprodujo además una colisión de evidencia en la API pública: tras cerrar
una posición, otro fill con el mismo native_decision_key se aceptaba y
`INSERT OR IGNORE` dejaba sólo el snapshot del primer paper_id. El integrador
agregó el guard atómico de key ya comprometido para spot y futuros. Dos
regresiones adicionales conservan el snapshot original y cero nueva posición
ante reuse; una key nueva con el mismo book válido sigue abriendo y captura
su propio paper_id. La suite de revisión ahora tiene12 casos; el receipt10
anterior se conserva como evidencia de esa fase, nunca como rerun final.

Los callers directos sin lineage conservan sus relojes nativos ausentes y
declaran inputs de señal no proporcionados; no inventan una señal o vector.
El recibo ATOMIC_PAPER_ADMISSION registra admission_at bajo lock y
entry_fill_recorded_at/captured_at real después del intent/inserts, con
entry_fill_committed_at=null explícito. La captura NATIVE_DECISION posterior
al COMMIT preserva signal/decision/intent/commit originales y liga el recibo
mediante financial_admission_snapshot_key/sha256. Ambos viven en el mismo
store canónico, sin duplicar decisiones ni trades. FUT usa el callback
atómico con rollback integral ante error. La suite de revisión financiera
actual tiene12 controles; el receipt anterior de10 queda como corte histórico.

La corrección del consumer lab valida hash/key/pid, quote exacta, vector
congelado y clocks de ambas fases. Source/received/known/available del vector
quedan<=signal; la policy debe ser conocida/efectiva<=admission. Captured puede
ser posterior a signal; recorded debe ser<=commit<=worker cut. Las features
mutables no reemplazan policy/style/multipliers del snapshot. Admission sola
no registra una entrada. Un budget1 puede recibir las fases en ticks distintos
y reintentar sin doble conteo. El checkpoint lab.v2 invalida v1 al tail actual
sin backfill. Legacy sin vector conserva LEGACY_VECTOR_UNAVAILABLE explícito.

El [receipt de fases](lab_phase_review_receipt.json) y su
[JUnit](lab_phase_review_tests.xml) registran59/59 a 2026-10-05T02:55:42Z,
root d5b2a0a7e871333411807052dc2bcfd55c7f5327 con overlay único de lab propio,
hashes antes/después iguales, network_attempts=[] y real_orders_sent=0.
Incluye27 casos nuevos de lab,12 de revisión financiera,19 controles previos
y un caller prospectivo existente;
son variantes/controles mecánicos, no59 muestras de edge. La prueba original
de UX test_native_worker_reader_and_all_ui_datasets_share_default_v2_cut
pasó de forma independiente en su worktree integrado: seis datasets reales
no vacíos. Ese run y el overlay son intermedios, no el artefacto final congelado.

R75 prueba duplicate fill con todos los hashes públicos y pin regenerados;
R76 prueba fill1µs antes entry con rehash completo. Se rechazan semánticamente,
preservando bytes de la fuente. Los dos nuevos nodos más duplicate raw fill y
entry1µs antes cutoff existentes pasan4/4 en root integrado READ_ONLY; el
[JUnit](historical_semantic_review_tests.xml) tampoco certifica el master real.

La revisión por fuente del draft de cierre detectó tres referencias que
no ejercitaban su ataque específico. Se agregaron guardas en los mismos
tests financieros, sin modificar producción ni relajar aserciones existentes:

| Caso | Ataque nativo y control | Límite conservado |
| --- | --- | --- |
| R18/R19 | Broker rechaza contrato ausente y multiplier2000 antes de OPEN; lifecycle directo también rechaza. Una posición válida alterada a multiplier2000 con raw snapshot SHA recalculado y dimensión de fila coherente falla en restore/risk/CLOSE, sin nuevas filas financieras; restaurar la autoridad original conserva exactamente su caja | Serie/cuenta real y custodia de la fuente siguen EXTERNAL_NO_VERIFICADO |
| R74 | Costo ledger, cantidad opening y costo fill inconsistentes, con todos los hashes públicos y el pin recalculados, reciben LEDGER_COST_RECONCILIATION u OPENING_QUANTITY_MISMATCH; el paquete original válido conserva net3 ARS | QA contable sintético; no tarifa de cuenta autenticada |
| R78 | Relabel coordinado ARS→USD_MEP y manifest coherente: el pin original recibe MANIFEST_DIGEST. Un pin nuevo permite RECOMPUTED, conservando el commitment opaco sin autenticarlo | TRUST_LIMIT explícito: source_authentication permanece EXTERNAL_EVIDENCE_PENDING; no prueba FX, moneda real ni edge |

El [receipt de procedencia inválida](closure_preservation_red_7175d956_receipt.json)
y su [JUnit original](closure_preservation_red_7175d956_tests.xml) se conservan
con HARNESS_MIXED_CHECKOUT_INVALID_FOR_PRODUCTIVE_CLAIMS. Inicialmente registró
dos fallos U08 y se atribuyeron prematuramente a bootstrap productivo. La
comprobación independiente del integrador pasó ambos sin cambios de código.
La [reproducción de imports](closure_mixed_checkout_proof.txt) identificó el
error: al colectar ambos tests propios, su helper histórico antepuso ownroot
a sys.path y el BM importado tardíamente vino del checkout financiero antiguo,
mientras BE ya estaba cargado del core integrado. BM old blob a82bba59 difiere
del root30f5153e. Verificar sólo módulos principales no validaba esa ejecución
como evidencia del árbol integrado. El diagnóstico quedó autocorregido;
no hubo bug productivo de schema ni se necesitó añadir DDL o cambiar fixtures.

El [receipt nativo corregido](closure_native_green_98153a8f_receipt.json) y su
[JUnit](closure_native_green_98153a8f_tests.xml) registran19/19 sin
fallos/errores/skips, sobre git archive del commit exacto
98153a8fdf2856f533bfc82d0aedb72879b16e9e y tree
349b514dfd893d057f9ccc90cdfd9dc32ffd3845. Los1246 archivos de la copia son
iguales antes/después; los37 módulos del repositorio cargados pertenecen
exclusivamente a ella, incluido BM correcto, con cero módulos de otro
checkout y cero intentos de red. Este commit es intermedio y se etiqueta
EXACT_ARCHIVE_INTERMEDIATE_COMMIT_NOT_FINAL_FROZEN_RELEASE_ARTIFACT:
no sustituye el rerun final congelado pendiente.

Los parámetros R74 prueban tres inconsistencias del mismo control; no se
cuentan como escenarios económicos independientes. El índice de receipts
excluye su propio JSON para evitar una autoafirmación circular; el manifest
separado liga el consolidado a bytes/SHA256.

La [revisión independiente de persistencia](persistence_independent_review.md)
retiene otro RED→GREEN sobre SIGKILL antes de ACK1, archive2 y retry1: la cadena
permanece en2 y el ACK antiguo se regenera sólo con prueba de pertenencia.

La suite incluye FUT U08 en ambas colisiones: USD_MEP/A3 o ARS/OTHER más nuevos
por1µs que el book ARS/A3 que cruza stop, después de restart. El supervisor
recupera sólo el book exacto y produce un cierre. Profundidad insuficiente no
crea terminal/fill futuro; una fotografía posterior completa permite salir.
El alcance actual futuro exige cantidad completa; no se afirma soporte de
parciales FUT. Spot conserva parciales/consumo de profundidad y prorrateo
monetario existentes: cc_spot_liquidity.available/book valida BBO bajo lock.

El control positivo spot comprueba costo de entrada y salida una vez, el
snapshot/hash de la misma transacción y gross−entryfee−exitfee=net. T+1 queda
PENDING_CONFIRMATION sin hora inventada; policy apagada conserva caja pendiente.
Con flag PAPER explícito se libera sólo al empezar el día posterior al día
hábil esperado, probando frontera−1µs e igualdad. No se etiqueta como
acreditación observada de PPI; otras monedas no reciben ese cash.

Main declara DISTINCT_SOURCE_EVENTS y lookback-limit90min, con span real; no
se le impone un horizonte90min ficticio. Scalping declara eventos, minspan14min,
gap máximo120s y límite45min; quince eventos en28s quedan HOLD. Estos límites
son contratos de hipótesis PAPER, no evidencia OOS ni tuning de rentabilidad.
Promoción enlaza key/hash del snapshot nativo inmutable y su vector;
_economic_admission recalcula el diagnóstico compartido, sin passed forzado.
BW usa cortes futuros reconstruidos; BV mantiene el reloj de exits aunque
fallen probes/children, con estado observable. Las suites del integrador
conservan las regresiones de estos caminos y requieren rerun final congelado.

## Veinte sesiones: bloqueo externo y cohortes separadas

| Cohorte recibida | Evidencia declarada | Estado admisible |
| --- | --- | --- |
| Cola histórica #468 | 77 cierres,19 fechas,80 snapshots | Extracto separado; no master completo ni veinte sesiones certificadas |
| Agregado #469 | 68 posiciones,151 fills,20 ruedas declaradas | No se suman ni sustituyen los counts de #468; EXTERNAL_NO_VERIFICADO hasta manifest/source/digest exactos |
| Tests/probe de esta convergencia | Books y bases sintéticos, clocks explícitos | QA de software únicamente; no rueda de mercado, cuenta broker ni muestra OOS |

Para cerrar el master externo falta un paquete autenticado y retenido por un
tercero con lista exacta de sesiones A3/BYMA y timezone, inclusión/cutoff, IDs
pseudonimizados de posiciones/fills/parciales, open survivors, contratos y
series, books/raw/recepciones, cash inicial/final por moneda, collateral/reservas,
variation cash, fees/FX/settlement y equity. El digest debe corresponder al
mismo conjunto y a un inventario coherente main/WAL/SHM. Las cantidades
parciales deben conservarse. Futuros de vencimientos distintos no se concatenan
sin roll preregistrado. ARS/USD/USD_MEP/USD_CCL se reportan separados.

Gross−costs=net comprueba aritmética del ledger, no conciliación de cuenta
completa ni edge. No-trade nominal0 se mantiene como referencia nominal; cash
o caución neta, inflación, costo de oportunidad y total return requieren sus
datos/convenciones y no se calculan por falta de evidencia. No se modificaron
score, Stop/TP/EOD/MaxHold ni se invirtió una señal usando cohortes conocidas.
La evaluación futura de edge necesita contrato provider, costos completos,
latencia/liquidez y holdout o purged walk-forward preregistrados por moneda.

## Auto-revisión final del link financiero

El link compara además economics/financial_contract congelados, para impedir
que un recibo con vector idéntico y costo/policy distinto se reutilice como
evidencia compatible. Un multiplier explícito0/False no recibe default1, y
quote JSON no-mapping produce rechazo preciso. Los49 casos de lab/replay
focales pasan en overlay READ_ONLY del root integrado;
[JUnit followup](lab_cost_link_followup_tests.xml). Este followup sigue siendo
intermedio: no se atribuye source freeze ni autenticación externa.
