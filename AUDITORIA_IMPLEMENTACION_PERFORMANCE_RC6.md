# WS_PERF_01 — evidencia económica RC6

WORKSTREAM_ID: WS_PERF_01_RC6_20261003. Fecha: 2026-10-03.
Base: deploy/rc6-pr69-isolated-20260915@da697c6e6c2274579f9e4a112fabc4327475dd35.
Rama: perf/ws-perf-01-rc6-20261003. Ownership/evidencia: issue #452.
PAPER/SHADOW ONLY; real_orders_sent=0; rutas reales vacías. Sin merge ni deploy.

## Hechos verificados

Se leyeron la orden V2, informe, diez hojas XLSX y paquete reproducible. Los 26
hashes del manifiesto del ZIP coinciden; el informe y XLSX independientes son
idénticos a sus copias del paquete. Se ejecutaron los dos auditores adjuntos
offline y se contrastaron resumen, cohortes, conciliación, curvas, operaciones,
fills y ruedas. El hash distinto de la fuente sanitizada no es una discrepancia:
el master original es b407d9c459e015e9be1bf984cb5071532e3776cb7a9db0fe486cbeb33a23370d,
la fuente sanitizada 02627d5ca707ff43d034a54d9bd516d295ce90afc8b2ab5fbda2daa72665c468.

La implementación canónica nueva también reproduce el corte 07/09–02/10:
20 ruedas, 68 posiciones, 151 fills, 10 posiciones con ventas parciales.
ARS: 62 cierres, 11 ganadores, bruto -10954.5809, cargos 23445.66,
neto -34400.2409, acierto 17.741935%, PF 0.1003815553.
USD_MEP: seis cierres, bruto -1.4439, cargos 2.64, neto -4.0839.
No se suman monedas. Los 14 escenarios de targets siguen negativos; las 30
combinaciones de la grilla también. Se reprodujeron los 14 casos post-stop;
ninguno recupera el equilibrio neto en el muestreo del modelo auditado.

El drawdown diario realizado reproducido es -35833.4232 ARS. El descenso
realizado entre cierres individuales es -36367.7944: son cadencias distintas,
ambas expuestas con nombres explícitos. Ninguna constituye equity completa.

Se revalidó HEAD mediante GitHub. El artifact runtime 11277627152 tiene hash
a4bf46ea04860448e349483d8fe96f870efacb10cf376725dd42e63af032c99d y confirma
VALIDATED_RUNTIME, PRODUCTION_PAPER, cero órdenes y PPI Watch intacto para
#449, observado 2026-10-03T15:16:36Z. No se presenta como una observación live
nueva ni como rentabilidad posterior. #450 permanece sin integrar.

## Reconciliación del diagnóstico histórico

- RESUELTO_PREVIO_AL_WS_PERF: warmup Scalping #437 y tolerancia específica
  SQLITE_BUSY/LOCKED #440. Se reutilizan; no se reimplementan.
- PARCIALMENTE_RESUELTO: explicaciones dashboard, snapshots inmutables de
  decisiones, costos y separación monetaria. La cadena completa sigue teniendo
  campos faltantes; registrar evidencia no demuestra edge ni aprendizaje útil.
- SIGUE_ABIERTO: edge neto, calibración fuera de muestra, factibilidad general,
  horizontes/EOD, decisiones rechazadas, latencias y atribución causal.
- NO_VERIFICADO: comisión particular de cuenta, equity completa, alpha,
  ganancias de HOLD, impacto monetario de SQLite y rentabilidad postdeploy.

## Código y autoridad

rc6_performance/costs.py separa gastos explícitos, descuento intradiario,
derechos e IVA. La fricción de puntas/slippage ya incluida en fills nunca se
resta por segunda vez. La conciliación exige identidad, cantidades y dinero.
Los modelos esperados son sensibilidad PAPER; no certifican tarifas de cuenta.
Se consumen los aranceles existentes sin modificar precios o parámetros.

scanner.py separa catálogo, canasta activa y capacidad, con INFEASIBLE explícito.
Para el snapshot histórico: 845 turnos, revisita 118300 s, cero muestras por
ventana, cota ideal 57 y límite conservador entero 54. No recomienda seleccionar
54 instrumentos: el plan consume un ranking punto-en-tiempo suministrado.
No recorta el catálogo ni cambia la estrategia factual.

Reporte de factibilidad histórico (snapshot 01/10 durante EOD, no atómico):
CATALOG/eligible=7614; pool de rotación=7603; foco=8. El foco estimaba 38
muestras/ventana, mientras la rotación estimaba cero. El flag general heredado
`feasible=true` describe el foco y no acredita cobertura del catálogo. El nuevo
reporte expone la rotación INFEASIBLE; throughput ideal de rotación 9/140=
0.064286 observaciones/s; límite conservador 54/7603=0.710246% del pool.
Son cotas ideales: fallos, trades repetidos y freshness reducen cobertura.

shadow.py requiere relojes aware, labels disponibles al corte, origen y ancla
de precio explícitos. Un modelo se congela antes de la decisión evaluada.
Sin movimiento esperado sustentado devuelve NO_VERIFICADO. Un economics gate
SHADOW no adquiere autoridad sobre BUY/HOLD. Todas las variantes reciben el
mismo snapshot aislado y hash; no duplican llamadas a proveedores.

replay.py compara políticas sobre la misma entrada, con estado causal,
precedencia EOD/MaxHold, depth/participation, lote, fills parciales y presupuesto
por libro. No rellena un hueco con precios inventados ni ejecuta overnight.
Stops/targets/trailing/break-even son variantes SHADOW, no parámetros promovidos.
La protección break-even queda armada tras observar el umbral neto; un retroceso
no la desarma. Un gap a través de ese nivel puede cerrar con pérdida neta, y el
replay conserva ese resultado sin presentarlo como garantía de equilibrio.

metrics.py y report.py exponen cohortes por moneda, día, semana, estrategia,
símbolo, familia, hora ART y salida; P&L bruto/costos/neto, PF, expectancy,
colas, MFE/MAE y drawdowns realizados. El funnel distingue evaluaciones de
oportunidades independientes y conserva razones crudas junto a su clasificación.
Clasificar texto heredado no equivale a poseer códigos causales nativos.

Reporte de funnel adjunto (01/10 hasta el corte EOD, consultas no atómicas):
650 evaluaciones, 635 HOLD, 15 BUY; de los BUY, 11 BLOCKED y 4 OPENED_SIMULATED,
aceptación 26.666667%. No son 650 oportunidades independientes. No hay conteos
confiables de UNIVERSE/FEATURES_READY/SIGNAL/EXITED para todas las 20 ruedas,
ni labels de los 11 rechazados: esos tramos permanecen NO_VERIFICADO. En las
últimas dos horas de Scalping hubo 703 HOLD y cero posiciones en ese extracto;
esa evidencia antecede #437 y no describe el warmup corregido.

## Captura y medición de salida

capture.py lee únicamente la DB fuente existente en mode=ro/query_only, exige
WAL y PAPER/0, limita 100 filas por tabla, 150 ms de consulta, 64 KiB por payload
y 1 MiB por batch. Usa una DB separada con cuota 128 MiB y escritor exclusivo.
Comienza en el tail actual; no repite ingestión histórica. Cursor y eventos se
guardan en la misma transacción. Hash inválido bloquea el avance; payload
demasiado grande registra una brecha. Alcanzar cuota detiene captura y deja
diagnóstico explícito, sin borrar datos para aparentar cobertura.

bv_paper_runtime.py agrega un worker de baja prioridad, sin proveedor/broker,
y reporte atómico cada cinco minutos sobre hasta 1000 eventos. El supervisor
recibe telemetría opcional: CONDITION, INTENT_COMMITTED y FINAL_FILL. El primer
fill proviene del ledger. Los probes usan otra DB con busy_timeout=5 ms, no
introducen un writer adicional en la DB de trading. Su fallo no veta salidas.
Reportes distinguen p50/p95/p99/max y relojes faltantes/contradictorios.
No se atribuye un retraso a SQLite sin causalidad adicional.

## Bloqueos y límites pendientes

be_paper_engine.py y bw_daily_risk.py están poseídos por WS-MOTOR-16/#453 y
permanecen READ_ONLY. bf_production_paper_observer.py y dashboard también quedan
READ_ONLY frente a los scopes históricos todavía abiertos. Esta tranche no
reimplementa esos cambios ni modifica admisión, riesgo o rutas.

La evidencia heredada llama captured_at a la recepción de quote; no se convierte
en el reloj real de decisión. SIGNAL/DECISION exactos, SHA/fingerprint completos,
gate factual del scanner y sustitución de todas las fórmulas heredadas de costos
requieren reconciliación posterior de esos componentes. Se entregan APIs/tests
y brechas explícitas, no una falsa declaración de trazabilidad total.

Los replays adjuntos resumen resultados del extractor: el paquete no contiene
la DB íntegra de mercado para repetir de cero primer toque/profundidad. La grilla
17:00 no aísla el stop. Calibración/edge/robustez posterior necesitan observaciones
fuera de muestra con la nueva instrumentación tras un deploy futuro autorizado.
El sábado no proporciona esa validación. Tests GREEN son evidencia de software.

## Reproducción

scripts/rc6_performance_audit.py acepta --master, --replays y --out; usa las 20 ruedas
explícitas de la ventana auditada, no un calendario universal. El input privado
y resultados detallados se conservan como artifacts, nunca en este repositorio.
scripts/rc6_performance_report.py acepta --evidence y --out para exportar el
reporte acotado de la DB de evidencia. La operación normal de captura/reportes
queda automatizada en el worker, sin terminal del usuario.

Validación y estado exacto: HANDOFF_WS_PERF_01.md y PR asociado a #452.

## Protocolo de evaluación posterior

HECHO VERIFICADO: las 20 ruedas son diagnóstico, no muestra de validación.
HIPÓTESIS: un gate neto y una canasta observable pueden mejorar selección;
no se demuestra aquí. INFERENCIA: la fricción absorbió movimiento pequeño,
pero no permite atribuir toda pérdida al scanner o SQLite.
RECOMENDACIÓN: antes de observar resultados nuevos, congelar SHA, fingerprint,
identidades, calendario de rueda, baseline, candidatos, horizontes, reglas de
costos/profundidad, ventanas de entrenamiento y criterio de suficiencia de muestra.
Documentar esa preespecificación en un artifact y separar diseño/evaluación.

Tras un deploy futuro autorizado, cada evaluator recibirá el mismo snapshot y
as_of nativo; labels serán outcomes posteriores, nunca features. El model se
entrena sólo con labels disponibles al corte y queda congelado antes del test.
Reportar por moneda/familia/hora/símbolo con ventanas no solapadas o tratamiento
explícito de dependencia; preservar todos los rechazos y gaps. Comparar neto,
tails, PF, drawdown realizado, turnover/costos y concentración por símbolo,
con sensibilidad preespecificada. No elegir ganador de grilla histórica.
Si falta reloj, profundidad, modelo o muestra, conservar NO_VERIFICADO y mantener
la variante SHADOW. La promoción no forma parte de esta misión.

## Auditoría matemática de la señal canónica

HECHO VERIFICADO por lectura de be_paper_engine.py@ee1b2299: signal_prices
filtra identidad completa, TRADE, timestamp de fuente dentro de ventana y
recepción <= as_of; ordena por trade_at y deduplica ese timestamp. No impone
intervalos equiespaciados ni aporta volumen. decide usa SMA3 y SMA min(8,n):

`momentum = SMA3 / SMAmin(8,n) - 1`

`score = clip(0.5 + 40*momentum - 10*(ask/bid - 1), 0, 1)`

El límite de lectura es max(20, signal_min_samples); la ventana es configurable.
El mínimo de muestras permite construir la señal, pero no demuestra precisión
ni que el horizonte de SMA coincida con MaxHold/TP/EOD. Ejemplo matemático
hipotético: con umbral 0.70, sin spread se exige momentum 0.5%; con spread 1%
se exige 0.75%. No se propone ese umbral ni se afirma que sea el activo.

La microestructura cambia el alcance temporal de cada media y el spread modifica
linealmente el score hasta clipping. NO_VERIFICADO: calibración, estabilidad por
régimen/hora/símbolo y asociación causal con retorno futuro. AUC ejecutado ARS
0.3885918003 es discriminación dentro del subconjunto operado y neto de cargos;
no evalúa candidatos rechazados ni reemplaza forward labels fuera de muestra.
Los 25 flags evidence_hash_valid=true son evidencia declarada por el extractor
fuente; esta misión verifica los 26 hashes del paquete y los hashes completos
de los nuevos snapshots capturados, sin fingir acceso a payloads históricos ausentes.

## Cierre de validación de software

ARTEFACTO_VALIDADO: ee1b22996907f282f77d3c31956104bd52cba858; PR #454; Predeploy 37140546207 success;
2543 tests, cero fallos/errores/skips/xfail y 50 focales nuevos.
Integridad/import closure y seguridad PAPER de imagen GREEN. Detalle/digest
exacto en HANDOFF_WS_PERF_01.md de la rama docs/ws-perf-01-closure-20261003.
WRITE_OWNER RELEASED. No deploy; observabilidad nativa completa/scanner factual
BLOQUEADOS y edge/runtime nuevo NO_VERIFICADO. Los reportes detallados privados
se entregan en el workspace; el artifact Predeploy no contiene las fuentes
financieras adjuntas. La auditoría histórica permanece inalterada.
