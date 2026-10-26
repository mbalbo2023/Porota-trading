# RC6 A79 — identidad, estrategia y admisión PAPER por familia

Fecha: 2026-10-26 UTC. Base de código `b183a9e8b9b9028d647390c006a8512cb0dc9f52`.
Scope WRITE_OWNER: A79, readiness y contratos de entrada por familia. No DEPLOY_OWNER,
no Predeploy, no despliegue. PAPER/SHADOW solamente; `real_orders_sent=0`; PPI Watch intacto.

## Causa y corrección

Tres capas de evidencia publicaban `paper_auto_enabled=True` al recibir solamente
un estado de contrato/fuentes `READY_PAPER*`: `rc6_family_readiness.evaluate`,
`cp_contract_evidence_v2_hf6.family_readiness_state` y
`scripts/rc6_full_universe_evidence.build`. Ninguna evalúa una señal direccional,
costos ejecutables, riesgo y admisión atómica. Por eso el nombre/estado daba un
verde de apertura injustificado, en especial en familias cuyo motor genérico
devuelve `STRATEGY_NOT_VALIDATED`.

La corrección separa `paper_contract_ready` de `paper_auto_enabled`, que permanece
falso en informes de fuentes/contratos. El estado agregado `READY_CONTRACT_ONLY`
no promete una compra PAPER. La reconciliación de readiness exige identidad
exacta de cinco campos: familia, ticker, mercado, moneda y plazo; una fila
complementaria sin identidad, de otra moneda o duplicada no promociona al
instrumento del catálogo. ON se normaliza a OBLIGACIONES, igual que el motor.
Las equivalencias publicadas BCBA/BYMA y T1/A-24HS sí se canonizan antes de
comparar, sin inventar campos faltantes.
Esto no cambia las rutas de órdenes ni fabrica una señal.

## Evidencia factual previa al cambio

Snapshot read-only del runtime CLOSED, 2026-10-26 00:22 UTC, [Action
40249154637](https://github.com/mbalbo2023/Porota-trading/actions/runs/40249154637).
Son recuentos puntuales del catálogo READY y del libro acumulado, no un
certificado de datos frescos durante la próxima rueda.

| Familia | Catálogo READY | Asiento PAPER constatado | Bloqueo restante para nueva apertura automática |
| --- | ---: | --- | --- |
| ACCIONES | 167 | 122 `BUY_SIMULATED` acumulados | Ruta spot acreditada históricamente; reconfirmar rueda nueva. Scalping HF5 es otra estrategia y no tuvo `BUY_CANDIDATE` reciente |
| CEDEARS | 1.278 | 13 `BUY_SIMULATED` acumulados | Ruta spot acreditada históricamente; reconfirmar rueda nueva y guard de mercado subyacente. Ratio sólo si la tesis lo necesita |
| ETFS | 0 | ninguno | Descubrimiento y contrato PPI exactos; no se puede abrir sin candidato |
| BONOS | 2.429 | ninguno | Contrato nominal exacto para la especie elegida y estrategia direccional/OOS, costos y fills |
| LETRAS | 34 | ninguno | Descuento, vencimiento, unidades, señal/OOS y costos |
| OBLIGACIONES | 3.624 | ninguno | Identidad/flujo de caja, crédito, señal/OOS y costos; otras 1.427 pausadas |
| OPCIONES | 1.329 | ninguno | Contrato/estructura y tesis del subyacente, señal/OOS; otras 1.719 pausadas |
| FUTUROS | 0 | ninguno | 207 pausados por contrato/margen; motor DLR dedicado sujeto a A3, costos y admisión |
| CAUCIONES | 5 | 6 colocaciones PAPER especializadas acumuladas | Nueva oferta/libro, obligaciones, efectivo y ventana de cierre verificables |
| FCI | 1.057 | ninguno; lifecycle vacío | La policy PAPER por importe ya existe; falta decisión/allocator conectado a caja y admisión, NAV/cutoff/eventos verificados |

READY del catálogo no demuestra señal. El ledger `scalping_candidates` de los
últimos diez días contiene 5.819 HOLD ACCIONES y 4.861 HOLD CEDEARS, cero
`BUY_CANDIDATE`. Hubo una candidatura CEDEARS el 30/09, anterior a esa ventana.
Las diez aperturas spot recientes con claves y gates auditadas son de ACCIONES
(9) y CEDEARS (1); no extrapolarlas a las otras familias.

La lectura RO también enlazó esas diez compras de la ventana 16–26/10 con
`decision_key`, gate, hash, identidad, snapshot atómico y relojes. Las diez
guardan vector `NATIVE_EXACT_VECTOR`; ocho marcan `temporal_ready=True` y dos
del 16/10 no conservan ese resultado explícito. Esto valida funcionamiento
PAPER spot histórico de ambas familias con ese límite, no una compra de GGAL o
AAPL específica ni una señal/fill de la próxima rueda. El diagnóstico HF5 de
scalping de abajo es un camino distinto: sus HOLD no revocan los BUY spot.

Diagnóstico adicional read-only [Action 40250769811](https://github.com/mbalbo2023/Porota-trading/actions/runs/40250769811),
2026-10-26 00:44 UTC, `MARKET_CLOSED`/PAPER/0 y cero consultas al proveedor:
los 10.680 HOLD de scalping de diez días se particionan exactamente en 4.898
`PPI_VOLUME_CONTRACT_UNVERIFIED` (1.578 ACCIONES, 3.320 CEDEARS), 3.469
`REJECTED_MUTABLE_CLOSED_POINTS` (2.950 ACCIONES, 519 CEDEARS) y 2.313
`EMPTY_INTRADAY_PAYLOAD` (1.291 ACCIONES, 1.022 CEDEARS). El motor exige
unidad/acumulación del volumen PPI con provenance y rechaza revisiones de
puntos ya cerrados. Un payload vacío no proporciona una señal. Ninguno de estos
estados se arregla abriendo un flag o usando el reloj de captura como cotización.
Para resolver por instrumento hay que probar la semántica del endpoint y la
estabilidad de sus puntos durante una rueda OPEN; sólo entonces validar una
reconciliación de volumen/velas y recalentar 15 muestras nativas.

De 62 gates spot de esa ventana, ACCIONES registró 15 bloqueos por concentración
financiera, 15 por cantidad ejecutable agotada por caja y 7 por marcas de riesgo
viejas; CEDEARS 5 por `SECTOR_UNMAPPED_BINDING`. Son límites y evidencia
faltante distintos de un defecto de estrategia; no se desactivan globalmente.

## Revalidación de rutas

`be_paper_engine.PaperBroker.decide` admite la señal spot genérica sólo en
ACCIONES, CEDEARS y ETFS. BONOS, LETRAS, OBLIGACIONES y OPCIONES quedan en
`STRATEGY_NOT_VALIDATED` incluso con catálogo READY, por diseño explícito;
su observador calcula métricas no direccionales. FUTUROS usa `_on_future_quote`
y contrato A3 exacto, pero los candidatos observados estaban pausados.
CAUCIONES usa `di_caucion_cash_sweep_runtime_hf6` y el allocator PAPER; una
colocación no es `BUY_SIMULATED` de spot. FCI tiene transición aislada
`SUBSCRIBE_REQUESTED` y policy interna `INTERNAL_RISK_BUDGET_BY_AMOUNT`, pero
sin puente de decisión/admisión en el runtime observado.

En el runtime spot existente, `bf_production_paper_observer.run` consulta
`current+book` a PPI sólo en OPEN, persiste la observación y rechaza relojes
stale antes de llamar a `PaperBroker.on_quote`. `decide` usa precios de eventos
nativos distintos, verifica cobertura temporal, compara medias cortas/largas y
spread, y deja BUY o HOLD trazable. El candidato BUY atraviesa preopen,
contrato exacto, economía, caja, riesgo concurrente y profundidad; un fill
aceptado escribe posición, `BUY_SIMULATED` y snapshot de admisión bajo la misma
transacción. Los diez enlaces recientes del recibo A78 muestran que este
camino efectivamente produjo compras PAPER en ACCIONES/CEDEARS. El censo IOL
de domingo no interrogó ese libro PPI y su `bids=[]` no es evidencia de una
falla del scanner. La ventaja estadística OOS de esa señal sigue separada de
la capacidad histórica de simular una compra.

No hay base factual para habilitar automáticamente las otras rutas
por nombre de familia. Para cada instrumento se exige contrato/identidad,
datos causales, estrategia definida y evaluada fuera de muestra, costos,
riesgo, admisión y asiento atómico; la próxima rueda OPEN debe demostrar los
eventos reales. A77 (#743 DRAFT) aborda historia/A3 en scope separado y
tampoco es runtime instalado. No se declara GO.

## Pruebas y límites

Pruebas focales de readiness, fuente, diez familias, options/futures/cauciones,
embudo, benchmark PPI y orquestador SHADOW ejecutadas con `ppi-client==1.3.0`
aislado en entorno de test: PASS. Se verificó que sin el SDK el benchmark
simulado falla cerrado con `PPI_READ_ERROR`/capacidad cero tanto en base como
en A79; no es una regresión de scalping. Pruebas de no cruce de familia,
moneda, plazo, ticker sin identidad y duplicado exacto: PASS.

Estado: corrección de falsos verdes **VALIDADA EN CÓDIGO/TEST**; publicación,
Predeploy y runtime de A79 **NO_VERIFICADO**. El snapshot anterior no verifica
una colocación en la rueda siguiente. E09/E10 no ejecutados.

## A79 continuación — caja FCI y cierre de bypass directo

Se encontró un bloqueo adicional reproducible: `FamilyPaperExecutor.subscribe_fund`
persiste `SUBSCRIBE_REQUESTED` por importe, pero `PaperBroker._cash` omitía por
completo los eventos FCI. Una integración automática futura podía reservar FCI
y simultáneamente gastar esa misma caja en spot/cauciones/futuros. Ahora la caja
PAPER descuenta una única vez la suscripción, no considera `NAV_APPLIED` como
efectivo y suma un rescate sólo al evento explícito `REDEEMED`. El corte as-of
es por instante UTC y moneda; un retroceso de reloj bloquea nuevas ejecuciones.
Se agregó índice de familia/lifecycle para la proyección sin cambiar datos
históricos ni inferir unidades de participación o rentabilidad.

Una nueva suscripción FCI contra la DB efectiva de runtime se comprueba dentro
de `BEGIN IMMEDIATE`: candidato exacto AVAILABLE/can_simulate, capability
`READY_PAPER_FCI_SUBSCRIPTION`, contrato `PaperFundTerms` idéntico al catálogo,
callback de admisión, caja suficiente y recibo global GO del release. Una
repetición idéntica del evento retorna idempotente sin otra reserva; una
colisión de ID se rechaza por el fingerprint existente. Ensayos aislados cubren
NO_GO, falta de guardas, insuficiencia de caja, candidato revocado, cambio de
términos, reintento, frontera temporal y otra moneda; la suite focal de
futuros/preopen/FCI y la batería de broker/familias se ejecutaron en local.
Este cambio no conecta el disparador automático FCI, no acredita NAV/cutoff
fresco, no prueba la política económica ni genera `BUY_SIMULATED`.

Una segunda pasada de autoauditoría halló un bypass de interfaz: un llamador
podía iniciar `SUBSCRIBE` o `SUBSCRIBE_REQUESTED` directamente mediante la
primitiva genérica `apply_paper_event` contra la DB runtime y así omitir los
guards del ejecutor. El fix posterior bloquea ambas transiciones iniciales
en la DB efectiva salvo cuando las emite el ejecutor bajo su transacción
cerrada. Una prueba negativa confirma cero eventos tras ambos intentos y
preserva el retry idempotente legítimo. El callback de caja sigue siendo
autoridad de código confiable provista por el broker; no es una API de
seguridad frente a código arbitrario que modifique el proceso. La aceptación
OPEN del allocator y sus efectos aún no existe.

Para BONOS/LETRAS/OBLIGACIONES/OPCIONES sigue faltando una tesis direccional
registrada y medida fuera de muestra; `STRATEGY_NOT_VALIDATED` permanece como
gate explícito. FUTUROS conserva motor DLR pero 207 identidades estaban pausadas;
ETFS cero candidatos READY; CAUCIONES conserva su treasury especializado.
Un test sintético demuestra seguridad y semántica de caja, no una oportunidad
real ni un fill en la rueda OPEN. Predeploy/artefacto/despliegue/runtime de
este fix: **NO_VERIFICADO**.

## Autoauditoría crítica A79 — retiro de hipótesis no sustentadas

La continuación que había añadido un evaluador de cinco supuestas estrategias
en el corte SHADOW **se retira del PR #744 mediante un commit posterior**. No
superó la prueba adversaria. El evaluador aceptaba como si fueran pruebas
causales un `model_frozen_at` y una procedencia declarados por el propio input;
los importes de salida, flujos, límites de crédito, oportunidad y pérdida de
estrés también provenían del mismo input sin productor independiente. Alterar
sólo `scenario_exit_bid` dio `POSITIVE_HYPOTHESIS` con resultados arbitrarios
para BONOS/ON. Una CALL con objetivo 101, spot 100 y strike 110 devolvió
hipótesis positiva usando una punta de salida 3 declarada; FCI dio positivo
con rescate al día seis y horizonte de evaluación al día cinco. Todos los
ejemplos fueron fixtures, no mercados ni rendimientos observados. La prueba
105 PASS validaba reglas programadas, no pronóstico, OOS ni compra simulada.
Una opción aún puede conservar valor temporal si el subyacente está por debajo
del strike antes del vencimiento: ese caso adversario no demuestra que la
prima deba valer cero. Demuestra que el código no derivaba ni justificaba la
punta futura 3 a partir de volatilidad, tiempo, ejercicio o un libro futuro.

La integración recorría todas las especies SHADOW en cada corte, aunque el
worker no entrega `family_strategy_research` en producción: añadía trabajo a
la ruta caliente sin posibilidad de señal. Se eliminaron el módulo, el
despacho y esos tests sintéticos del candidato. Esta corrección deja intacto
el bloqueo `STRATEGY_NOT_VALIDATED` y no cambia órdenes. La tesis económica
por especie permanece **NO_VERIFICADO / BLOQUEADO para entrada PAPER**.

### Campos: presencia en código, productor, persistencia y consumo

El esquema `contract_evidence_v2_snapshots` guarda JSON de evidencia y
`contract_evidence_v2_current` sólo apunta al último snapshot por identidad
exacta/fuente. El censo RO [40249154637](https://github.com/mbalbo2023/Porota-trading/actions/runs/40249154637)
contó 23.344 filas `current`, **sin contar claves JSON ni frescura por campo**.
Por lo tanto 23.344 no significa 23.344 duraciones, flujos o lotes utilizables.
Las tablas de catálogo guardan `metadata_json`; snapshots de mercado guardan
cotización/contrato y puntos intradiarios conservan clocks. El reporte SHADOW
consume sólo filas exactas y campos fechados; una estructura SQL no demuestra
que haya un valor en la base instalada para una especie concreta.

| Campo necesario/alegado | Fuente y ruta observadas en código | Persistencia/uso realmente demostrado | Dictamen |
| --- | --- | --- | --- |
| Identidad, moneda, plazo, precio y libro | PPI catálogo/market snapshots/points; IOL/BYMA complementan sólo por clave exacta; precios `last/bid/ask` en cascada validada | Catálogo y snapshots se leen de SQLite; 9.923 READY exactos y 23.344 evidencias current en corte CLOSED; no prueba de libro fresco OPEN por especie | Parcial, por identidad; OPEN NO_VERIFICADO |
| VN, precio por nominal, mínimo, múltiplo y vencimiento | `bond_technical` PPI conserva `nominalesEnPrecio`, `laminaMinima`, `multiploMinimo`, `fechaVencimiento`; IOL `_fixed_contract` verifica unit/lot/simulación de un nominal y persiste `fixed_income_evidence` | El catálogo/evidence JSON puede almacenar esos términos; la cuenta READY prueba admisión de contrato para un subconjunto, no cobertura homogénea ni tarifa broker | Parcial; medir valor y reloj por ticker |
| Cupón, amortización, flujos y unidad | PPI conserva `intereses`/`amortizacion`; IOL expone analítica por lote y simulación por un nominal | En el producto b183 sólo madurez, dirty, valor técnico y moneda llegan al colector. El cambio A82 de este Draft reconcilia fecha, moneda, monto, lote y clocks, y lleva hasta SHADOW una referencia IOL de un nominal sin autoridad contractual ni entrada; la instalación/runtime siguen NO_VERIFICADO | Parcial EN_GITHUB en #744; no usar cobro supuesto como BUY |
| TIR, duration y curva | PPI técnico trae `tir`/`modifiedDuration`; IOL API tiene analítica y validador offline los exige; SHADOW reconoce alias de yield/duration y `curve` | IOL collector no persiste duration de la respuesta analítica en su fila; no se halló un productor de curva fechada por emisor/moneda/plazo para A79; 23.344 current no certifican esas claves | TIR/duration parcial en PPI; curva/modelo de tasas NO_VERIFICADO |
| Emisor, riesgo de crédito y exposición por emisor | PPI técnico `emisor`; no se halló productor de `credit_review=CURRENT`, probabilidad de incumplimiento, límite o exposición ligado a ledger | Un string de emisor no es una evaluación crediticia ni un límite de riesgo respaldado | NO_VERIFICADO |
| Opción: subyacente, CALL/PUT, strike, vencimiento, lote, IV/griegas | IOL `get_options_chain` persiste evidence/contrato en JSON para serie estándar no ajustada de ACCIONES; PPI libro y catálogo son autoridad operativa; SHADOW selecciona estructura | 1.329 opciones READY y 1.719 pausadas por contrato en censo; no se midió presencia de cada clave/libro por las 1.329, ni se modeló ejercicio/ajustes/exit por serie | Parcial estructural; tesis/salida/OOS NO_VERIFICADO |
| FCI: NAV, fecha, cutoff, rescate y caja | Contrato `cq_family_contract_rules_hf6` distingue términos/eventos; SHADOW acepta `nav/nav_date/subscription_status/redemption_term/cutoff_time`; IOL `get_fci_funds` colecta inventario y `rc6_broker_parity_evidence` sólo liga identidad, descripción y operable | No se verificó snapshot instalado con NAV/cutoff/plazo vinculados y frescos para un fondo; `paper_equity_by_currency` no es, por sí solo, una reserva atómica de efectivo | NO_VERIFICADO por fondo; FCI guard de caja en #744 aún no desplegado |
| Pronóstico, modelo congelado, costos ejecutables, slippage y OOS | `family_strategy_research` era una clave opcional inventada por A79 sin productor; comisiones PAPER son policy de simulación | No hay registro de features/labels causales, calibración, holdout y tarifas reales de cuenta para las cinco tesis; ni ledger de salidas por sus ciclos propios | NO_VERIFICADO; cero autoridad de entrada |

Esta matriz inicial era una **auditoría de adaptadores y un censo agregado
RO**. Se completó después una consulta JSON por clave a la base instalada
para siete testigos: [Action 40256594412](https://github.com/mbalbo2023/Porota-trading/actions/runs/40256594412),
[recibo saneado #732](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6406507750),
26/10/2026 01:56:04Z, SQLite `mode=ro`/`query_only=ON`, PAPER/0 CLOSED,
0 solicitudes al proveedor y 0 acceso PPI Watch. Un primer probe
[40256468132](https://github.com/mbalbo2023/Porota-trading/actions/runs/40256468132)
omitió YMCJO al buscar `OBLIGACIONES` donde la tabla guarda `ON`; su
recibo se marcó parcial y el probe corregido leyó ambas etiquetas.

| Testigo | Catálogo instalado exacto | `contract_evidence_v2_current` por fuente | Qué acredita |
| --- | --- | --- | --- |
| GGAL ACCIONES | ARS/BYMA/A-24HS AVAILABLE `READY_PAPER_SPOT`; multiplicador/mínimo/step presentes | PPI estructurada con identidad; regla derivada con multiplier/step | Contrato exacto en DB; este censo no consultó `market_snapshots` ni el ledger por símbolo |
| AAPL CEDEARS | Igual, con READY_PAPER_SPOT | PPI estructurada y regla derivada | Contrato exacto; el stale `UNKNOWN` histórico es fila distinta; este censo no leyó fill por símbolo |
| AL30 BONOS | ARS/BYMA/A-24HS AVAILABLE `READY_PAPER_SPOT`; multiplier/mínimo/step presentes | IOL 14/10 con multiplier y maturity; PPI estructurada 23/10 sólo identidad en claves censadas; XHR viejo con moneda serializada no canónica | No hay calendario de ocho flujos/USD ni duration/curva en el `current` inspeccionado |
| D30N6 LETRAS | ARS/BYMA/A-24HS AVAILABLE `READY_PAPER_SPOT`; multiplier/mínimo/step | PPI estructurada con identidad y regla derivada, **sin vencimiento/flujo** en claves censadas | READY de contrato de apertura, no valor de cobro ni estrategia al vencimiento |
| YMCJO ON | Catálogo `OBLIGACIONES` ARS/BYMA/A-24HS AVAILABLE `READY_PAPER_SPOT` | Tabla usa familia **ON**: IOL 14/10 con multiplier/maturity, PPI estructurada 21/10 con identidad; otras fuentes históricas | El cero del primer probe era falso negativo; no persiste aquí el calendario USD de 14 flujos consultable en IOL |
| GFGC5400NO OPCIONES | AVAILABLE pero capability **`CONTRACT_EVIDENCE_REVIEW_REQUIRED`**, no READY; strike/right/underlying/expiry/multiplier en JSON | Regla derivada 25/10 y IOL 23/10 con parte del contrato; PPI sólo identidad | Contrato en revisión; book IOL actual null, ningún BUY |
| IOLCAMA FCI | `OBSERVED_SHADOW` / `NEEDS_FUND_SETTLEMENT`; sólo identidad en claves censadas | **0** filas current ligadas a la clave consultada | Inventario IOL operable no demuestra clase/NAV/cutoff/rescate o PAPER admission |

El probe contó nombres de claves con valores no vacíos hasta profundidad tres
en los JSON actuales; no inspeccionó valores financieros, semántica, hashes de
documento, `market_snapshots.contract_json`, revisiones históricas ni cache
IOL fuera de la DB. Su `observed_at` es el reloj del snapshot, no siempre
el reloj nativo de cada campo. Por tanto "ausente en estas claves current" no
significa "inexistente en toda fuente o toda tabla". El complemento IOL
consultado más abajo demuestra varios valores externos que **no atravesaron
el collector hasta este JSON**. Antes de cualquier entrada PAPER todavía
hay que resolver la unión exacta y el reloj nativo por campo, luego medir el
libro y la fuente en OPEN.

### Condiciones de una estrategia apta para prueba PAPER

Por instrumento y familia: hipótesis económica y regla de selección
predefinidas con entradas observadas causalmente; unidad monetaria/nominal y
cashflows validados; reloj de publicación y calendario correctos; costo de
entrada/salida, spread, slippage, riesgo, capital reservado y límites externos
al propio forecast; simulación de salida que respete cupón/vencimiento,
ejercicio/ajuste o rescate; dataset PIT sellado, walk-forward con holdout,
sensibilidad y fallos; finalmente observación OPEN, admisión y asiento PAPER
atómico auditables. Ninguno de estos criterios se satisface por contar
`READY_PAPER`, añadir columnas ni obtener un test sintético positivo. Esta
secuencia es un contrato de aceptación propuesto, no una predicción rentable.

### Casos prácticos: consultas complementarias y unión PPI pendiente

Corte de consultas **read-only** al conector IOL 26/10/2026 01:42Z
(25/10 22:42 ART, mercado cerrado). Los resultados de IOL sirven para
demostrar qué expone ese proveedor **ahora**; no prueban que el worker haya
persistido esos valores en SQLite ni la identidad PPI exacta del lunes. La
matriz histórica de 20 testigos/222 campos de
`docs/WS_MOTOR_11_FIELD_SOURCE_MATRIX.md` corresponde al 28–29/09 y tampoco
representa la base actual. Se distingue el precio del viernes de la respuesta
obtenida hoy; ningún libro vacío se promueve a spread ejecutable.

| Familia / testigo | Observación concreta de PPI/IOL | Datos y camino que faltan para entrada PAPER nueva |
| --- | --- | --- |
| ACCIONES / GGAL | PPI ya tiene clave exacta y contrato spot READY. IOL complementario muestra último 6.235 ARS de viernes 17:00:03 ART, `bids=[]`, `asks=[]` al consultar domingo; esto no mide el libro PPI en OPEN. Nueve BUY spot recientes de ACCIONES constan en PAPER, sin atribución aquí a GGAL | El scanner PPI **sí** consume current+book y decide/llena spot en rueda; para afirmar una compra de GGAL el lunes hay que observar su señal, gates y fill específicos. CLOSED no genera una decisión HOLD nueva |
| CEDEARS / AAPL | PPI tiene identidad/contrato spot READY. IOL complementario mostró último 28.660 ARS viernes y libro vacío domingo; no mide libro PPI OPEN. Hubo un BUY spot CEDEAR en la ventana reciente y 13 acumulados, no atribuibles aquí a AAPL | Ruta spot histórica operativa, con guard de subyacente US; reconfirmar reloj/libro/señal/fill del lunes por símbolo. Ratio/CCL sólo si la tesis específica los usa |
| ETFS / sin testigo PPI READY | Censo instalado devolvió **0** candidatos ETF simulables | Primero encontrar una clave PPI exacta y contrato; no extrapolar desde AAPL ni inventar ticker |
| BONOS / AL30 | IOL: ARS T1, lote 100, quote 85.960 ARS por lote = 859,6 por nominal, `bids/asks=[]` y precio del viernes; analítica con duration modificada ~1,68 y 8 cashflows. Simulación de 1 nominal: inversión 859,6 ARS, cobros denominados **USD**, `payment_currency=USD` | Identidad PPI exacta ya existe en DB; falta dato técnico/quote OPEN y matching temporal. IOL no persiste los 8 flujos en el collector operativo. Para carry no sumar ARS y USD sin conversión explícita; para un fill spot en ARS esos flujos no son requisito. Estrategia especializada/OOS/salida aún sin validar |
| LETRAS / D30N6 | IOL info: letra ARS T1, lote 100; la descripción dice vinculada USD cero cupón 30/11/26. Analítica `not_found`, simulación por nominal `not_found`, quote `INVALID_ARGUMENT`; PPI identidad exacta aparece en DB actual aunque faltaba en matriz septiembre | Documento nativo de pago y fórmula/moneda no presentes en las claves current censadas; no atribuir flujo único ni valor facial sin dato. Otra letra puede diferir |
| OBLIGACIONES / YMCJO | IOL ON ARS T1 lote 100, quote 159.330 por lote / 1.593,3 por nominal, viernes 17:00:27 y libro vacío; analítica 14 flujos y duration modificada ~4,45. Simulación de 1 nominal: compra 1.593,3 ARS y `payment_currency=USD` | Identidad PPI exacta existe en DB; faltan libro OPEN y flujo normalizado para tesis de cobros, riesgo de emisor/capital, OOS/lifecycle. No confundir ausencia de esos campos en dos JSON current con ausencia en IOL |
| OPCIONES / GFGC5400NO | Cadena IOL de GGAL: CALL strike 5.400, vencimiento 20/11 15:30 ART. Devuelve `spot_price=0`, bid/ask/IV/griegas `null`; IOL GGAL spot último 6.235 del viernes y libro vacío. El normalizador sólo forma contrato estándar sin ajustes verificados | Clave PPI por serie, lote/moneda y ejercicio exactos, libro y subyacente OPEN, regla de salida/OOS; no interpretar spot cero ni precio de opción nulo como cotización |
| FUTUROS / DLR-DIC26 (testigo histórico) | Matriz PPI septiembre tenía identidad, A3 términos parciales; censo octubre: 207 futuros pausados, 0 READY. A77 #743 aún Draft | Contrato/margen/tick/settlement exactos por vencimiento, historia V2 y señal DLR, recibo/punta/admisión, sin activar los tres timers A3 |
| CAUCIONES / ARS colocadora 1 día | IOL responde mínimo 100.000 ARS y vencimiento 27/10, pero `rate=null` en este corte, sin oferta/profundidad; seis colocaciones PAPER previas en runtime para familia | Oferta y tasa nativas frescas y principal disponible en ventana EOD, riesgo/caja/fee, nueva adjudicación simulada; no transformar `null` en 0 o tasa del viernes |
| FCI / IOLCAMA | IOL inventario renta fija pesos: ARS y `operable=true` sin fecha nativa, NAV, clase PPI exacta, cutoff o rescate en respuesta; testigo septiembre no ligaba la clase a PPI | Unión PPI exacta, reglamento y evento NAV/cutoff/rescate por fondo, caja y admisión atómica; el guard A79 sigue Draft y no genera allocator |

**Caso AL30, cálculo dimensional antes de cualquier estrategia:**
la observación IOL presenta la compra de 1 nominal en ARS 859,6 y el
simulador da `amount_invested=0.557815...` en USD con `payment_currency=USD`.
El `applied_exchange_rate=1` de esa respuesta no concilia por sí solo ambos
importes. La analítica reporta precios clean/dirty por 100 en otra base; por
eso un algoritmo que multiplique `ask × nominales × 0,01` y sume cupones
USD a caja ARS calcula una magnitud sin sentido. Además la respuesta de
analítica fue calculada el domingo con última cotización del viernes. El
umbral correcto aquí es `NO_VERIFICADO`, no un BUY ni una TIR propia.

**Caso YMCJO:** el mismo choque ARS de compra/USD de flujos se repite y hay
riesgo crediticio/emisor que no se deduce de conocer el nombre YPF. La
duration de IOL se puede consultar, pero no constituye un límite de riesgo
autorizado ni una curva de crédito observada. Ninguna de estas consultas
tocó cuenta, saldo, validación ni ruta de orden.

Semáforo para el próximo candidato: corrección del falso verde y protección
de caja FCI **EN_GITHUB DRAFT / runtime NO_VERIFICADO**; A77 historia/A3/PPI
en #743 aparte **DRAFT**; estrategias nuevas, retención 365d, OOS, UI/voz
52/52 y desempeño de workers OPEN **NO_VERIFICADO** o **BLOQUEADO** según
la matriz #732. La autorización posterior del operador permite preparar
Predeploy/Deploy de un candidato coherente que supere sus gates exactos; no
convierte hipótesis, datos CLOSED ni estos dos Draft en aceptación financiera.

## A82 — teoría comprobable para cerrar el ciclo especializado (25/10 ART)

### Hechos y prueba adversaria

- El producto b183 observa diez familias, pero su `PaperBroker.decide` sólo
  hereda señal spot para ACCIONES/CEDEARS/ETFS. El runtime A78 acredita 135 BUY
  históricos en las dos primeras, seis cauciones con lifecycle propio y cero
  BUY de las otras ocho. Eso demuestra una brecha de autoridad/estrategia, no
  que el broker carezca de precios ni que forzar ocho BUY sea correcto.
- IOL leyó el domingo AL30 y YMCJO: analítica con 8 y 14 fechas de pago por
  lote 100, respectivamente, y simulación de **un nominal** con moneda de
  cobro USD. Las tablas se reconcilian dimensionalmente en el probe A82, pero
  la analítica tenía cálculo 21:00 ART y la simulación 23:50 ART: distancia
  aproximada 10.228/10.201 segundos. `next_coupon_date` de AL30 en esa
  respuesta apunta a 2021 pese al corte 2026. Ese campo NO se propaga.
  Mismo proveedor y cálculo dominical no certifican términos de emisor ni
  punta negociable el lunes.
- En #744, `rc6_iol_family_reference._fixed_flow_reference` comprueba un
  nominal, lote, período, fechas, componentes, residual, total, moneda y
  relojes separados; falla cerrado ante inconsistencias. Persiste únicamente
  calendario normalizado y provenance en el cache IOL, con importe USD por
  nominal sin sumarlo a caja ARS. `rc6_shadow_runtime.families` lo consume como
  observación fechada (máximo 24 h desde el cálculo **más antiguo**), nunca
  como autorización. El reloj de captura se toma al acabar las respuestas;
  `collect()` antes sólo marcaba el inicio del ciclo. Prueba local: 119 casos
  focales PASS, incluidos conflictos de unidad, reloj, moneda y horizonte;
  dos respuestas reales CLOSED reconciliaron 8/14 flujos sin señal ni BUY.

### Hipótesis por familia: protocolo de ensayo, ninguna declarada rentable

| Familia | Hipótesis de decisión que debe registrarse antes de calibrar | Unión causal y ciclo PAPER exigidos | Estado ahora |
| --- | --- | --- | --- |
| ACCIONES | Baseline spot ya produce candidatos; comparar retorno neto contra no operar y contra alternativa de misma sesión, con costes y cierres reales | PPI trades distintos+book OPEN, preopen, riesgo, fill al ask y salida al bid; medir OOS, no reutilizar HOLD de scalping HF5 | Fill histórico VALIDADO_RUNTIME, ventaja OOS NO_VERIFICADO |
| CEDEARS | Mismo baseline local; una tesis de desvío contra subyacente requeriría ratio, sesión US y FX de modalidad correcta | PPI clave exacta ARS/USD y book; ratio Caja, subyacente/FX sólo si esa tesis los usa; mismo ledger spot | Fill histórico VALIDADO_RUNTIME, tesis adicional NO_VERIFICADO |
| ETFS | Baseline spot como hipótesis sólo si hay ETF local con clave PPI READY; no confundirlo con CEDEAR de ETF | Descubrimiento/clase/ratio exactos, book y costes; mismo ledger spot sólo tras readiness | Cero READY instalado, BLOQUEADO |
| BONOS | Comparar precio de salida ARS al bid con entrada ARS al ask dentro de un horizonte; carry USD requiere escenarios FX separados y términos del emisor | PPI libro/nominal, IOL referencia de flujos, versión de emisor, cupones y amortizaciones en ledger por moneda, salida y costes | Flujos IOL A82 observacionales; señal/OOS y issuer NO_VERIFICADO |
| LETRAS | Si contrato demuestra cero cupón y moneda única, ensayar descuento al vencimiento; una letra indexada exige fórmula y fijación publicadas | Clave PPI, vencimiento, facial/fórmula por serie, libro OPEN, cashflow y salida. D30N6 no tiene analítica/simulación IOL disponible | BLOQUEADO por términos exactos de testigo y OOS |
| OBLIGACIONES | Comparar precio y flujos con instrumentos del mismo riesgo/duración/moneda bajo límites de emisor definidos externamente | Alias ON/OBLIGACIONES exacto, PPI/IOL y documento de emisor, capital pendiente, pagos, exposición y salida; nunca inferir crédito desde ticker | YMCJO 14 flujos IOL de referencia; crédito/OOS NO_VERIFICADO |
| OPCIONES | Tesis del subyacente y regla de prima/volatilidad definida ex ante; limitar long a prima+costes | Serie exacta, lote/strike/ajustes, book de opción+subyacente sincronizados, expiración/ejercicio BYMA, mark-to-bid y eventos de salida | Contrato GFGC5400NO en revisión, libro CLOSED nulo, BLOQUEADO |
| FUTUROS | Tesis DLR específica, no trasplante de MEP/CCL ni momentum spot | A3 identidad/multiplicador/tick/vencimiento/margen, book, ajuste diario, variación, cierre y límite de exposición en lifecycle existente #453 | 207 pausados/0 READY; A77 y rueda OPEN BLOQUEADO |
| CAUCIONES | Tesorería: elegir oferta neta realizable por plazo sobre caja no comprometida; no predicción direccional | Tasa colocadora/lado/volumen frescos, principal, vencimiento, comisiones y reserva atómica, evento MATURED | Seis ciclos PAPER históricos; oferta de próxima rueda NO_VERIFICADO |
| FCI | Asignación de liquidez con horizonte de rescate y reglamento; no extrapolar NAV de cierre para ejecución previa | Clase exacta, NAV publicado, cutoff, plazo, suscripción/rescate y caja reservada; A79 cierra bypass de reserva pero aún no conecta allocator | Inventario IOL sin NAV/cutoff/clase PPI exacta; BLOQUEADO |

Para spot, el cash inicial es `ask × cantidad × multiplicador + costos`; el
valor liquidable de salida usa **bid** y costes de salida. En renta fija,
cupones/rescates son movimientos en su moneda contractual: ningún PnL neto
mezcla ARS y USD sin un escenario FX explícito, fechado y por modalidad. En
futuro, variación diaria es `(precio_ajuste_nuevo − anterior) × cantidad ×
multiplicador` y margen reservado permanece separado de PnL. En opción larga,
la pérdida máxima en la compra es prima más costes; antes de vencer su valor
se marca al bid, no a un payoff intrínseco inventado. En FCI, NAV publicado
no libera caja: sólo evento de rescate efectivamente liquidado. Estas son
identidades contables/hipótesis de modelado, no una ventaja de mercado.

### Implementación y prueba de falsación

1. Sellar por campo `family,ticker,market,currency,settlement`, fuente,
   unidad, `source_at`, `received_at`, revisión de contrato y hash. PPI manda
   identidad; complementos nunca la sobreescriben. La unión exige
   `source_at ≤ received_at ≤ decision_at` y documento válido para la fecha.
2. Registrar un `strategy_id` versionado, hipótesis, features disponibles en
   ese instante, universo y parámetros **antes** de mirar el holdout. Un
   evaluador especializado devuelve `HOLD` con código causal si falta unidad,
   libro, salida, contrato o riesgo; `READY` de catálogo no emite BUY.
3. Replay por fecha de disponibilidad real, con etiquetas sólo después del
   horizonte, bloques temporales y embargo suficiente para no compartir
   resultados de una misma posición entre train/test. Registrar todas las
   variantes intentadas y comparar costo neto, drawdown, capital, cobertura,
   sensibilidad al spread/slippage y benchmark de misma moneda/familia. PBO
   y Sharpe deflactado son diagnósticos de selección múltiple, no garantía de
   beneficio futuro. Referencias metodológicas primarias:
   [Bailey et al., PBO](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
   y [Bailey & López de Prado, DSR](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
4. Si el holdout carece de muestra/puntas/PIT suficiente o el neto no supera
   costos bajo estrés de supuestos, conservar `STRATEGY_NOT_VALIDATED`.
   Sólo una estrategia aceptada por evidencia puede crear candidato; aun así
   debe pasar preopen, contrato, riesgo, caja, mercado OPEN y asiento PAPER
   atómico con trazabilidad de decisión, admisión, fill y salida.

La [norma BYMA de opciones](https://www.byma.com.ar/productos/productos-financieros/opciones)
fija lotes y ejercicio, mientras la [CNV sobre FCI](https://www.argentina.gob.ar/cnv/proteccion-al-publico-inversor/preguntas-frecuentes-cnv)
sitúa NAV/reglamento/valor de cuotaparte en el ciclo del fondo. Esas reglas
se consultan por serie/clase vigente antes de parametrizar un ejecutor. Los
umbrales numéricos de ventaja/riesgo y el tamaño mínimo de muestra no están
estimados con datos PIT suficientes: quedan deliberadamente NO_VERIFICADO.
