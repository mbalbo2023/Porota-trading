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
| ACCIONES | 167 | 122 `BUY_SIMULATED` acumulados | Señal y admisión genuinas OPEN; scalping reciente sin `BUY_CANDIDATE` |
| CEDEARS | 1.278 | 13 `BUY_SIMULATED` acumulados | Igual; además mercado subyacente y relación CEDEAR verificables |
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

Para BONOS/LETRAS/OBLIGACIONES/OPCIONES sigue faltando una tesis direccional
registrada y medida fuera de muestra; `STRATEGY_NOT_VALIDATED` permanece como
gate explícito. FUTUROS conserva motor DLR pero 207 identidades estaban pausadas;
ETFS cero candidatos READY; CAUCIONES conserva su treasury especializado.
Un test sintético demuestra seguridad y semántica de caja, no una oportunidad
real ni un fill en la rueda OPEN. Predeploy/artefacto/despliegue/runtime de
este fix: **NO_VERIFICADO**.
