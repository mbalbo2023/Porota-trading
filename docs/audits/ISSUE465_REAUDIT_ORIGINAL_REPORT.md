# Auditoría adversarial independiente — POROTA TRADING RC6, Issue #464 / PR #463

Fecha de corte: 2026-10-04 UTC. Modo: READ_ONLY, PAPER/SHADOW. Repositorio: mbalbo2023/Porota-trading. Dictamen: **GO_TO_FIX_AND_REAUDIT**.

## 1. Executive verdict (una página)

El candidato #463 está abierto y draft, no integrado. Revalidé contra GitHub la base productiva da697c6e6c2274579f9e4a112fabc4327475dd35, HEAD caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423 y tree f1712a72cb49435c4403145bb38d98c4006f8f07. El run nativo 37175265248/job 111356467877 terminó exitoso, reportó 3020 tests sin fallos/skips y construyó una sola imagen. El artifact 11293625514 está registrado con digest de ZIP sha256:c2b4a12d53245fe6a8e57ebf179358249f1db2834fe9afbc8823582a192df767, 411094537 bytes. **Verifiqué metadatos y log, no rehasheé los 411 MB ni ejecuté la imagen**. Tampoco existe una observación de runtime del candidato: permanece sin deploy. Las declaraciones de los autores y su matriz son pistas, no prueba independiente de beneficio o seguridad en producción.

Hallazgo material reproducido: en modo dinámico aprobado, OPENED_CRITICAL y EXIT_CRITICAL comparten la reserva. Cinco solicitudes book del scanner de posiciones abiertas agotan cinco slots; la solicitud EXIT_CRITICAL del lector de salidas queda denegada durante la ventana. El scanner, además, descarta su propia ruta de supervisión si el último negocio está stale aunque haya consumido el book. No es un caso hipotético de un test verde: ejecuté el algoritmo real de SQLite en un directorio efímero sin conexión al proveedor. Este es **P1 antes de habilitar la capacidad dinámica o aprobar el candidato como seguro para despliegue**, condicionado a esa activación. No he demostrado pérdida real ni orden real.

Un segundo defecto de cobertura: el worker Scalping memoriza Instrument not found por request hasta reinicio de proceso, sin TTL ni reset al cambiar de sesión. Una respuesta que deje de ser válida puede excluir prospectivamente el instrumento durante días: **P2**. La instrumentación SHADOW y las rutas PAPER tienen guardas útiles, pero no reemplazan la prueba con datos de rueda, la medición de capacidad OPEN o la conciliación de las 20 ruedas. El informe previo afirma 68 posiciones, 151 fills y ARS -34.400,2409 netos, pero el master granular está excluido del repositorio; sólo pude contrastar su aritmética agregada. No hay edge neto ni calibración OOS demostrados.

No encontré una ruta de envío real en el código de los componentes examinados: la fachada PPI permite GET de mercado y POST de autenticación, y el broker simula en ledger. Eso respalda una **conclusión de código**; real_orders_sent=0 y real routes NOT_CALLED del candidato en un host quedan **NO_VERIFICADO** hasta una validación runtime autorizada. PPI Watch no se tocó. No hubo merge, deploy, SSH, DB productiva ni llamadas de mercado en esta auditoría.

## 2. Alcance, jerarquía y evidencia

Contrato leído: Issue #458 completo y A–N, #460 completo, #462 completo y O–V, #464 completo; PR #463, final matrix humana/máquina y comentarios de attestation; antecedentes #453, #455, #456, #457, #459, #461, AGENTS.md y políticas RC6. Se inspeccionaron los 101 paths modificados en HEAD y los callers críticos; se compararon ocho archivos sensibles con la base productiva. Las declaraciones de ownership de #453/#459/#461/#463 son consistentes con los PR abiertos observados, pero la ausencia de otro escritor fuera de GitHub no es certificable.

Fuentes canónicas: [Issue #464](https://github.com/mbalbo2023/Porota-trading/issues/464), [PR #463](https://github.com/mbalbo2023/Porota-trading/pull/463), [commit exacto](https://github.com/mbalbo2023/Porota-trading/commit/caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423), [run](https://github.com/mbalbo2023/Porota-trading/actions/runs/37175265248), [artifact](https://github.com/mbalbo2023/Porota-trading/actions/runs/37175265248/artifacts/11293625514). Toda referencia archivo:línea de aquí en adelante apunta al tree exacto de #463, salvo indicación BASE. PASS designa un invariante verificable en código/test revisado; no equivale a runtime validado. PARTIAL identifica implementación presente con un borde o evidencia insuficiente; FAIL identifica contraejemplo al requisito; NO_VERIFICADO exige datos/artefacto/runtime no disponibles. Los tests del run no se vuelven “independientes” por haber terminado GREEN.

Ejecuté dos pruebas offline directas con módulos del HEAD, stdlib, reloj inyectado y SQLite temporal: colisión de prioridades PPI y precedencia de fuente. No ejecuté pytest: el entorno local carece de pytest y la copia de inspección no constituye un checkout completo. Las pruebas nativas del run y sus logs se examinaron como evidencia secundaria, no se repitieron. No se hizo benchmarking del proveedor con mercado cerrado ni se cargó DB privada.

## 3. Requirement traceability matrix independiente

Abreviaturas de test: B=test_rc6_ppi_global_budget.py; W=test_rc6_shadow_runtime_wiring.py; O=test_rc6_dynamic_orchestrator.py; C=test_rc6_capacity_promotion.py; F=test_rc6_final_family_source_policy.py; R=test_rc6_final_shared_risk.py; E=test_rc6_shadow_entry_signals.py; U=test_rc6_shadow_operational_funnel.py; FP=test_rc6_future_programming_complete.py. “Run” es únicamente el log 37175265248; “ausente” significa que no hay observación live del candidato. Se agrupan subcláusulas con un mismo contrato de comportamiento, sin heredar veredictos de la matriz del desarrollador.

| Requisito | Fuente | Código y caller real | Test examinado | Evidencia runtime | Veredicto | Severidad |
| --- | --- | --- | --- | --- | --- | --- |
| PAPER, ruta real cerrada | #458, #460, #462, #464 | bd_ppi_readonly_guard.py:22–45,203–291; bv_paper_runtime.py:298–367 | B:211–221, W:196–218 | Ausente candidato | PARTIAL: barrera de código; host pendiente | NO_VERIFICADO |
| Catálogo READY separado de deep capacity | #458 obj. 2, A, N | rc6_dynamic_universe/orchestrator.py:63–82,236–289; worker.py:262–264 | O, W | Ausente | PASS código | — |
| Discovery transversal honesto, sin BUY | #458 A–B | live.py:30–68; tradeability.py:73–83; orchestrator.py:137–166 | W:78–126,258–295 | Cobertura OPEN ausente | PARTIAL | NO_VERIFICADO |
| Foco fijo sólo baseline; preopen frozen | #458 C | cf_intraday_scalping.py:25,219–229; worker.py:235–252; tradeability.py:194–303 | W:55–76,142–148 | OFF; ninguna rueda candidato | PARTIAL: default aún fijo por contrato OFF | NO_VERIFICADO |
| Tradeability, señal, economics y risk separados | #458 D,F | orchestrator.py:263–289; stages.py:49–85; be_paper_engine.py:1040–1066 | O,W | Ausente | PASS separación; eficacia NO_VERIFICADO | — |
| Score no es probabilidad; calibración OOS | #458 E | entry_signals.py:228–256,509–529; metrics.py:15–24 | E | Labels OOS ausentes | PARTIAL | NO_VERIFICADO |
| Salidas alternativas no alteran factual | #458 G; #460 F | rc6_shadow_runtime/lab.py; worker.py:317–324; rc6_performance/replay.py | W, tests de performance | Ausente | PASS cableado SHADOW | — |
| Cadencia diferenciada por motores/familias | #458 H, K | orchestrator.py:15–39; routing.py; families.py:647–699 | O,F | Achieved OPEN ausente | PARTIAL | NO_VERIFICADO |
| CEDEAR: ratio, US session, CCL, clocks | #458 I | tradeability.py:173–191; families.py:529–557 | F | Fuente específica no certificada | PARTIAL | NO_VERIFICADO |
| Opciones: selección estructural | #458 J | families.py:558–616,673–694 | F:167+ | Book/OI/assignment live ausentes | PARTIAL | NO_VERIFICADO |
| Coverage/revisit y códigos causales | #458 L–M | orchestrator.py:235–326; funnel.py:507–563 | O,U | Métricas de rueda ausentes | PARTIAL | NO_VERIFICADO |
| Benchmark endpoint/global 20–100 | #458 obj. 1; #460 G | benchmark.py; capacity.py:24–114; promoción:83–118 | tests de benchmark | OPEN ausente | NO_VERIFICADO | EXTERNAL |
| Capacidad no sumada por motor | #458 obj. 6; #462 Q | live.py:91–107; rc6_ppi_global_budget.py:210–274 | B:59–70, O:342+ | OFF actualmente | PARTIAL: no garantiza salida | P1 |
| Prioridad de salida aun con abiertas | #462 Q; #464 §12 | budget.py:243–263; scanner:1836–1865; exit reader:172–226 | B:38–46 no cubre competencia critical/critical | OFF actualmente | **FAIL** bajo APPROVED_DYNAMIC | **P1** |
| Policy gate OFF/SHADOW/APPROVED | #462 O | promotion.py:47–69,121–170,218–295; cf:232–255; bf:983–1023 | C, tests de factual callers | OFF en ops/policy/rc6-dynamic-capacity-v1.json | PASS código; activación OPEN NO_VERIFICADO | — |
| Sin cuotas de familia en HOT | #462 P | orchestrator.py:193–226,329–369 | O,C | Ausente | PASS código | — |
| Entry signal lab registrado, mismo snapshot | #462 R | worker.py:319–320; entry_signals.py:198–225,228–256,297–441 | E | Labels OOS ausentes | PASS cableado, PARTIAL inferencia | NO_VERIFICADO |
| Diez familias con política explícita | #462 S; #460 E | families.py:487–699; routing.py | F | Fuentes especializadas live ausentes | PASS routing; PARTIAL profundidad económica | — |
| PPI > IOL > BYMA, identidad exacta | #462 T | source_authority.py:13–40,69–162; families.py:487–529,687–699 | F; probe local | Ausente | PASS resolución SHADOW; fuente factual NO_VERIFICADO | — |
| Funnel prospectivo, etapas, moneda | #462 U | worker.py:321–324; funnel.py:461–563 | U | Empieza en tail; rueda ausente | PARTIAL | NO_VERIFICADO |
| Sin código nuevo después de benchmark | #462 V | promotion.py:121–170,274–319; cf:232–255; bf:983–1023 | C, factual callers | Requiere aprobación/redeploy futuro | PARTIAL: path existe, prioridad falla | P1 |
| Preopen/session sin backfill | #460 B, #462 | worker.py:232–269; co_market_sessions_hf6.py | W:142–148,221–226 | Lunes futuro | PASS fail-closed; readiness NO_VERIFICADO | — |
| Discovery y WARM/HOT prospectivos | #460 C–D | live.py:48–90; orchestrator.py:107–232 | W:78–126 | Fuentes OPEN ausentes | PARTIAL | NO_VERIFICADO |
| FUTUROS exact DLR PAPER | #462 §3–5; #460 E | rc6_ppi_future_contract_policy.py:20–45,62–114; be_paper_engine.py:1299–1515 | FP, R | Ningún DLR candidato live | PARTIAL | NO_VERIFICADO |
| FUTUROS ledger/variation/risks | #462 blockers A–D | rc6_paper_family_lifecycle.py:154–435,610–809; bw_daily_risk.py:139–245 | FP,R | Ausente | PASS coherencia de código; live NO_VERIFICADO | — |
| SHADOW worker realmente hijo | #460 A,H | bv_paper_runtime.py:298–367; worker.py:211–339 | W:196–218 | No deploy candidato | PASS wiring de código | — |
| Labs económicos/exit + funnel local | #460 F,I; #462 U | worker.py:311–324; stages.py:14–88 | W,E,U | No rueda | PASS cableado, eficacia NO_VERIFICADO | — |
| Reinicio/DB ro/archivos separados | #460 H; #464 §14 | worker.py:69–112,211–339; persistence.py:12–111 | W:150–193 | Sin stress host | PARTIAL | P2 |
| Build once, tree freeze, import closure | #462 y #464 §17 | workflows predeploy/promote; scripts/porota_validate_deploy_artifact.py:109–153 | Run + tests de deploy | ZIP no rehash auditor | PARTIAL | NO_VERIFICADO |
| Regresión dashboard/caución/observer | #464 §25 | diff BASE→HEAD; di_caucion... idéntico, bg_paper_dashboard.py sólo newline; observer modificado | suite nativa | No runtime candidato | PARTIAL | NO_VERIFICADO |

## 4. Findings register P0–P3

**F-01 — P1, reproducido: la reserva de EXIT_CRITICAL es consumible por OPENED_CRITICAL.** El policy arma la reserva bajo EXIT_CRITICAL (rc6_ppi_global_budget.py:37–58). acquire suma ambos en used_by[EXIT_CRITICAL], trata rank 0 y 1 sin prioridades previas (243–258) y deniega cuando se agota book (259–263). Callers: scanner bf_production_paper_observer.py:1836–1851 usa OPENED_CRITICAL para current/book; lector bv_paper_runtime.py:193–198 usa EXIT_CRITICAL. Probe independiente sin red: window 30s, book limit 5, reserva EXIT_CRITICAL book 5, global 15; cinco book OPENED_CRITICAL admitidos/usados y el sexto EXIT_CRITICAL denegado PPI_BUDGET_EXHAUSTED (requested=6, allowed=5, used=5, dropped=1). Esto puede ser más grave cuando el scanner obtiene book pero saltea broker por last trade stale (bf:1858–1865), mientras el lector necesita sólo book para una salida. Bajo policy OFF no se crea budget nuevo (budget.py:479–496); el peligro es futuro, al aprobar dinámica. El test B:38–46 prueba discovery frente a salida, nunca abierta frente a salida. **Corrección exigida a otro owner:** cupo indelegable de salida o preempción/fairness medible, caso simultáneo scanner/exit/scalping con stale trade y 5 abiertas, más prueba multi-proceso. No se aplicó fix.

**F-02 — P2, probado por flujo de control: blacklist Intraday sin vencimiento.** cf_intraday_scalping.py:596 crea unsupported_requests una sola vez fuera del loop; 650–652 omite requests; 689–695 agrega al recibir Instrument not found. No hay vaciado por fecha, cambio de catálogo ni respuesta posterior, porque la identidad omitida ya no se consulta. persist_payload sí reinicia el contrato por sesión (266–270), lo que no reinicia ese set. Impacto: ceguera de cobertura para la misma tupla ticker/tipo/settlement tras corrección de vendor o nueva rueda hasta reinicio. No es fallo global del proveedor ni todas las identidades. Prueba faltante: error Instrument not found, reloj/rueda siguiente y posterior respuesta válida sin reiniciar worker; TTL y reason code de reintento. Sin live no cuantifico frecuencia.

**F-03 — P2, observabilidad/reinicio: publicación SHADOW no es una transacción de tres archivos.** worker.py:327–338 escribe latest, luego checkpoint, luego status; persistence.py:73–111 hace rename y fsync atómico por archivo, no commit del conjunto. Un kill o disco lleno entre writes deja latest de t, checkpoint/status de t−1; el digest de cada archivo puede ser válido individualmente. El siguiente tick reutiliza checkpoint anterior y puede reconstruir, pero un consumidor entre medias ve cortes mezclados; status.report_digest permite cotejo sólo si el consumidor lo exige. Prueba actual W:55–76 valida reinicio limpio, no kill entre las tres escrituras. Se requiere generación/manifest committed único o validación de corte idéntico en lector, con fault injection en cada frontera. No implica escritura a DB trading.

**F-04 — P3, telemetría engañosa pero aislada: source_audit vacío.** live.py:110 invoca audit_sources() sin los snapshots recibidos; sources.py:323–335 deja snapshots vacío. source_reports adyacentes sí contienen observaciones y provenance, por lo cual no infiero pérdida de datos ni BUY incorrecto. El consumidor debe mostrar los reportes reales o enlazarlos desde source_audit. Test de wiring con fuentes no vacías debe verificar ese campo.

**F-05 — P3, retención operativa finita.** persistence.py:87–94 limita 512 archivos y 128 MiB; worker.py:235–252 crea preopen inmutable por sesión. No se ve política de exportación/archivo/rotación de esos freezes; eventualmente el SHADOW falla cerrado. Necesita runbook de retención sin borrar evidencia auditada y alerta previa. No se midió tiempo hasta cuota.

No hallé P0 demostrado. Los fallos o resultados de proveedor, fills, hardware, DB productiva y pérdida financiera real permanecen sin verificar. No clasifiqué una mera falta de datos futuros como bug de código.

## 5. Auditoría financiera y trader

**Reconstrucción histórica limitada.** AUDITORIA_IMPLEMENTACION_PERFORMANCE_RC6.md:10–29 declara una auditoría previa de 20 ruedas 2026-09-07 a 2026-10-02, 68 posiciones, 151 fills, 10 posiciones con salidas parciales. ARS: 62 cierres, 11 winners, gross -10.954,5809, costos 23.445,66, net -34.400,2409, win rate 17,741935%, PF 0,1003815553. USD_MEP: 6 cierres, gross -1,4439, costos 2,64, net -4,0839. Recalculé sólo gross menos costos y los promedios: ARS por cierre gross -176,6868, costos 378,1558, net -554,8426; USD_MEP net -0,68065 por cierre. No sumé ARS con USD_MEP. **El master privado original, fills, snapshots de libros y 26 hashes del ZIP no están en el repo inspeccionado**, así que cantidades, parcialidades, gross por fill, fees reales, profit factor, drawdown y motivos STOP/EOD/MAX_HOLD no son reproducción independiente. Los 14 targets/30 perfiles negativos son una declaración del documento, no resultado del auditor. metrics.py:92–137 y costs.py:105–142 muestran cómo se rechazaría duplicate/identity/quantity/money mismatch, pero el input privado no se suministró.

**Economía.** El baseline factual usa score acotado 0–1 basado en momentum y spread (be_paper_engine.py:1040–1066); no es probabilidad calibrada. El antecedente de AUC ARS ~0,389 en #458 E es alerta descriptiva de muestra seleccionada, no autoriza invertir signo ni elegir threshold. Scalping usa score similar y gate binding: observed_range de 15 minutos previos > dos fees aproximados + spread + 0,0004 + margen 0,005 (cf_intraday_scalping.py:392–426); esto evita algunas entradas evidentemente caras, pero rango pasado no es pronóstico de movimiento **posterior** y no prueba que target 2% sea alcanzable antes de salida. El broker revalida candidate y book 120s (cf:519–564), protege caja/profundidad/riesgo; no transforma el filtro en edge. El modelo de comisiones por cuenta se declara NO_VERIFICADO (costs.py:45–102). En FUTUROS la economía fija no exacta se registra como SHADOW y BINDING veta aperturas (be_paper_engine.py:1327–1329,1591–1602); el PAPER SHADOW puede abrir bajo costos estimados, no es un producto rentable certificado.

**Sensibilidad de costos, sobre los agregados alegados, no un backtest nuevo.** Con gross fijo y costos +25%/+50%/+100%, el net ARS sería respectivamente -40.261,6559 / -46.123,0709 / -57.845,9009; USD_MEP -4,7439 / -5,4039 / -6,7239. Para llegar a cero en esa misma muestra haría falta mejorar gross ARS en 34.400,2409 y USD_MEP en 4,0839, además de oportunidad de caja y riesgos; no es un target por precio. El movimiento de break-even por instrumento es (comisión + derechos + IVA + spread + slippage de ambos lados) / notional ejecutable, con multiplicador y rebate correctos; sin notionales y puntas p50/p95 granulares no calculo bps ni capacidad de tamaño. La sensibilidad no predice las nuevas ruedas.

**No-trade y contrafactual.** HOLD CASH nominal en las 20 ruedas habría dado P&L de trading 0 en cada moneda frente a net negativo alegado; el costo de oportunidad de caución y buy-and-hold no se pudo calcular sin saldos, tasas, reinversión y precios. Los rechazados/HOLD carecen de labels/fills ejecutables, por lo que no hay comparación same-snapshot entre baseline, tradeability-only, señales alternativas, stop/target dinámico, trailing, break-even, MaxHold, EOD, latencia, spreads, fill probability y parcialidades. entry_signals.py:297–441 prohíbe outcomes como inputs, compara variantes preregistradas; replay.py modela profundidad/partials; funnel.py parte del tail y conserva dos sesiones. Eso es infraestructura causal, no un resultado contrafáctico OOS. Reportar AUC/hit/expectancy por familia/identidad/hora/régimen requiere labels futuros, denominadores de decisiones independientes y agrupación por sesión para evitar leakage y repeated observations. MFE/MAE observado tampoco garantiza un fill.

**Juicio cuantitativo.** No hay evidencia de expectativa neta positiva, robustez a costos, calibración, significancia estadística ni alpha. 62/6 cierres separados y 20 ruedas son pequeños y seleccionados; el cambio de ranking/score/salidas sobre esa muestra causaría data snooping. El rango de pérdidas de cola, equity completa, drawdown mark-to-market y supervivencia de abiertas son NO_VERIFICADO. No corresponde vender “menos pérdida” como edge.



## 6. Auditoría de Scalping y universo

El worker real sólo se inicia con PAPER_SCALPING_MODE ACTIVE_OBSERVE o ACTIVE_PAPER (bv_paper_runtime.py:362–365); en OFF no hay worker. En modo de capacidad dinámica OFF conserva select_batch y su foco de ocho, límite de lote 40, intervalo nominal 180s y segunda pasada por batch (cf_intraday_scalping.py:24–42,203–255,601–649). Para ~1.200 identidades y ocho focos, la cota ideal de dos pasadas sin latencia es aproximadamente 2 × ceil((1200−8)/32) × 180s = 13.680s, 228 minutos; las 15 muestras distintas exigidas por el gate en 45 minutos no se obtienen para un cold sólo con esa rotación. Esto es cálculo ideal, no latencia medida. No se deben prometer cobertura real ni oportunidades nuevas por el mero 24→40. Bajo APPROVED_DYNAMIC, cf:232–255 consume selección con identidad completa, y 665–685 da prioridades opened/HOT/WARM/DISCOVERY; el diseño está cableado, pero no se puede medir revisita alcanzada ni HOT efectivo con OFF y sin rueda OPEN.

El planner SHADOW deduplica muestras por timestamp nativo Intraday PPI y no cuenta una recepción repetida como nueva muestra (orchestrator.py:107–152); requiere book y señal de volumen confirmada para HOT (187–192). Las anomalías contemporáneas se calculan con eventos disponibles y la promoción no autoriza BUY en DISCOVERY/WARM (live.py:51–68; orchestrator.py:153–166,263–289; cf:683–685). El riesgo de sample starvation persiste si el único radar es una rotación con tiempo de revisita superior a ventana de warmup; fuentes complementarias no son una garantía de snapshot transversal en tiempo real. La anomalía puede surgir fuera del preopen, pero su detección prospectiva real y latencia permanecen NO_VERIFICADO.

Los relojes fuente/recepción/último punto/último book se validan en cf:333–358,392–400,519–534. El endpoint del worker es reader.intraday, no una ruta inventada (673–676). 404 por instrumento es scoped en la clasificación (bd_ppi_readonly_guard.py:114–145) y no dispara breaker global (budget.py:289–311); F-02 demuestra que la caché del worker sí puede dejar ciego ese request con el tiempo. La entrada ACTIVE_PAPER revalida candidez, tiempo, libro y broker (cf:485–568), pero la señal económica usa movimiento retrospectivo; en el informe financiero no se le atribuye ganancia futura. OPENED_CRITICAL del scanner y exit reader están separados como callers, pero F-01 rompe su garantía de atención bajo límite apretado.

La selección no recorta el catálogo READY: el planner preserva rows/identidades (orchestrator.py:63–82,236–326). Tradeability separa volumen SHARE/UNIT, turnover en la misma moneda, spread/depth/freshness, grupos por familia/mercado/moneda y curvas de concentración medidas (tradeability.py:136–170,194–320). No hallé regla Pareto 80/20 que admita órdenes. En cambio, una observación “TRADEABLE” en SHADOW es una selección de atención, no una señal ni un contrato negociable. La documentación comercial debe distinguir READY, strategy eligible, observable, tradeable, HOT, señal, economía, riesgo y PAPER.

## 7. Auditoría por familia

La cobertura de routing de las diez familias es real como reporte OBSERVE_ONLY en rc6_shadow_runtime/families.py:647–699; **no equivale a diez estrategias comercializables**. family_reports se llama en cada tick canónico (worker.py:311–316). La propiedad entry_authority es false para estos handlers. Se distingue el lifecycle factual especializado donde existe.

| Familia | Ruta/contrato inspeccionado | Hallazgo y evidencia faltante |
| --- | --- | --- |
| ACCIONES | Equity spot factual be_paper_engine.py:1679+; ranking tradeability, book/depth/costs; scanner bf:1828–1870. | Admisión PAPER y salidas supervisadas. Edge, volumen ejecutable y benchmark por horas/símbolo sin datos OPEN/OOS. |
| CEDEARS | Infra equity con features ratio, subyacente US, sesión, CCL/divergencia como evidencia separada (tradeability.py:173–191; families.py:529–557). | Campos opcionales no pueden heredar clock del quote; corporate actions, ratio efectivo y FX de ejecución no certificados. No tratar USD/ARS como un mismo cash ledger. |
| ETFS | Ruta equity spot si identidad y contrato READY; family report OBSERVE_ONLY (routing.py, families.py:487–529). | No se verificó semántica PPI específica de cada ETF, valor cuota, creaciones/rescates ni liquidez por especie. No afirmar equivalencia universal a acción. |
| BONOS | Handler de renta fija, prioridad OBSERVE_ONLY por vencimiento, yield/duration/paridad/carry/cashflows cuando existen (families.py:413–481,558–572). | Precio nominal/cash, unidades por lote, interés corrido, settlement, TIR y tarifas por instrumento carecen de evidencia live completa; sin BUY por fallback momentum. |
| LETRAS | Mismo handler específico OBSERVE_ONLY, sin cupo Scalping (families.py:413–481,647–699). | Convención de precio/descuento y devengamiento no certificados por serie. |
| OBLIGACIONES | Handler renta fija OBSERVE_ONLY y fuente campo a campo (families.py:413–481). | Cronograma/moneda/carry/corporate events y profundidad ejecutable NO_VERIFICADO; no señal equity. |
| OPCIONES | Filtro por subyacente/serie/vencimiento/moneyness/book (families.py:558–616,673–694); contrato de apertura en be_paper_engine.py:1702–1706. | OI, IV/Greeks, ejercicio/asignación y spread en series reales no certificados; selection no prueba lifecycle rentable. |
| FUTUROS | Factual especializado DLR 2026, market A3, ARS, INMEDIATA, multiplicador 1000, quantity step 1, reserva PAPER 100% notional (rc6_ppi_future_contract_policy.py:20–114; lifecycle.py:610–628). Clock llama supervise_futures y exit reader incluye abiertas (bv:141–226). | Sólo DLR estándar 2026, LONG PAPER. Comisiones fijas/broker margin y settlement real NO_VERIFICADO. Un book stale/no depth detiene mark/close, deja abierta hasta dato ejecutable (be:1470–1577,1671–1677); requiere alertas de carry/outage. |
| CAUCIONES | Tesorería factual conservada; di_caucion_cash_sweep_runtime_hf6.py byte-idéntico a base. Shadow observa caja/tasa/plazo/vencimiento por eventos (families.py:618–634). | Free settled cash, arancel individual, tasas y opportunity cost future no reproducidos. No scanner momentum. |
| FCI | Handler NAV/publicación/suscripción/rescate OBSERVE_ONLY y event-driven (families.py:636–645). | NAV efectivo, cutoff, rescate/liquidez y lifecycle de órdenes no certificados; no BUY ni scanner intradía. |

FUTUROS no usa el fallback spot para reserva/caja: lifecycle.py:154–252 abre atómicamente, mark 254–346 aplica variación, close 348–435 libera; future_risk_snapshot 728–809 suma variation y unrealized sin sumar una misma variación dos veces según fórmula inspeccionada. El contrato de salida se restaura desde snapshot digest-checked (641–656); DailyRisk integra las posiciones y trata mark stale como estado que impide admisión (bw_daily_risk.py:139–245), y mark_equity separa monedas (be_paper_engine.py:2159–2184). Esta es una revisión algebraica y de flujo, no conciliación de DB runtime ni de contrato de broker. La sesión DLR policy usa 10:00–15:00 ART y sólo discrimina weekday/expiración (rc6_ppi_future_contract_policy.py:117–158); la cobertura de feriados A3 se debe comprobar contra fuente de mercado y books OPEN, no inferir de “weekday”.

## 8. Autoridad de datos y source attack

source_authority.py:13–40 ordena PPI explícito > IOL > scraper BYMA > otro; 69–162 exige evento nativo y receipt distintos para datos dinámicos, invalida stale, agrupa por source-path y reporta conflictos en lugar de sobrescribir el campo primario. Probe offline a 14:00Z: PPI book 100 e IOL 120, ambos frescos → value=100, review_required=true, entry_authority=false; PPI ausente → IOL=120 observado pero sin autoridad de entrada; PPI stale → IOL=120, todavía sin entrada. La prueba confirma ese resolver puro; no prueba exactitud del collector vendor. families.py:125–193 añade unidades/clock por campo y 286–332 exige book coherente y price basis compatible para tradeable; un conflicto lo rebaja. Identidades ambiguas con un request PPI de tres elementos se excluyen en cf:213–218,250–255, no se resuelven con last-write-wins.

El SHADOW usa caches existentes y no llama BYMA API ni inventa IOL MCP interactivo; source_reports conservan procedencia pero F-04 muestra que source_audit agregado está vacío. Un cache sin evento nativo no puede aportar freshness de quote; evidencia estática puede registrar availability sin reemplazar hora nativa (source_authority.py:102–116). Un complementario válido no debe convertirse por ese hecho en identidad primaria. No pude confirmar completitud/latencia/frecuencia real de PPI, PPI web, IOL ni scraper BYMA en la rueda OPEN. El límite de observación transversal sigue NO_VERIFICADO.

## 9. Riesgo, accounting y ledger

**Caja y exposición.** broker_from_environment fija defaults PAPER: ARS 1.000.000, USD/MEP/CCL separados, risk per trade 0,2%, max positions 5, max position 25%, total exposure 60%, daily loss 2,5%, score threshold 0,62, stop 2%, target 5% (bv_paper_runtime.py:74–98); son defaults de código, no evidencia del entorno productivo ni óptimos financieros. FUTUROS calcula notional con multiplier, costo, stop risk y mínimos por caja, participación, posición y total; repite checks bajo lock al abrir (be_paper_engine.py:1299–1468). Reserva full notional + fee es conservadora PAPER y no corresponde al margen A3 real. future_cash_effect y future_risk_snapshot alimentan _cash, mark_equity, DailyRisk (lifecycle.py:686–809; be:839–878,2159–2184; bw:139–245). Verifiqué separación por currency y tratamiento stale/carry; no hay suma nominal ARS/USD sin FX. Los tests FP/R cubren restart/duplicate/stale, pero sin DB privada no certifico idempotencia operacional bajo crash ni exactitud de los 151 fills históricos.

**Cierres y salidas.** Spot PositionExitSupervisor corre cada 5s y exit reader obtiene libros cada ciclo (bv:141–169,229–290). FUTUROS bajo book stale/incorrecto no marca ni simula salida, lo cual evita inventar liquidez; no convierte una orden de stop en garantía de fill. Durante 429, 5xx o bloqueo de presupuesto, DailyRisk puede impedir aperturas nuevas, pero no puede fabricar un precio de cierre. Hay que medir tiempo de posición abierta sin book ejecutable, alertar EOD/expiry/carry y conservar reconciliación por moneda. La falla F-01 afecta esta liveness precisamente cuando se habilite el budget dinámico.

**No-trade y límites.** Incluso si las pérdidas históricas declaradas fueran exactas, el modelo de stop 2%, target 5% y max-hold 360min no queda justificado por las excursiones accesibles ni por tasa de acierto. El hard stop diario y soft stop son controles PAPER, no límite monetario máximo ante gap, book sin depth, mark stale o outage. Ninguna cifra de pérdida máxima realizada debe venderse como VaR o garantía.

## 10. Wiring/runtime y regresión

Camino factual: bv_paper_runtime.main crea store y lock de reloj, inicia scanner, performance, dynamic_shadow, exit_reader y workers opcionales; run_clock pulsa supervisor/futures/valuation; scanner hace market/session gate, current+book PPI, normaliza y persiste quote, descarta stale, permite decisión/entrada PAPER sólo por su plan/admisión; lector de salidas lee libros de abiertas; PaperBroker y lifecycle/ledger resuelven fills; captura y dashboards leen eventos. Referencias: bv:141–169,172–226,298–367; bf:1828–1870; be:1299–1602; cf:485–568. El worker SHADOW abre source DB mode=ro/query_only, lee preopen previo, congela por sesión, incorpora observaciones post-watermark, llama live.run_shadow y luego family_reports, economic_exit_lab, entry_signal_lab y operational_funnel, escribe evidencia separada; falla cerrado a status/log (worker.py:69–112,211–373). Sus llamadas no son callbacks del broker. El caso sin freeze después de apertura devuelve PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION, no backdatea. La ruta de modo APPROVED_DYNAMIC existe en los selectores factuales y exige report/evidencia/fingerprint/aprobación (promotion.py:121–170,258–319). OFF por defecto conserva el factual 20/40; no confundir SHADOW HOT plan con HOT que ya conduzca una lectura factual.

Comparé directamente la base da697... con HEAD para bv, bf, cf, be, bw, bd, di caución y dashboard. di_caucion_cash_sweep_runtime_hf6.py es byte-idéntico; bg_paper_dashboard.py sólo añade newline. bv agrega los dos hijos de evidencia y FUTUROS/priority de exit reader. cf agrega gates de freshness, snapshot de señal y selector aprobado; bd agrega el budget guard, manteniendo allowlist. bf, be, bw cambian rutas factual/risk; el diff y tests no sustituyen regresión en host. No observé una regresión específica del dashboard o caución por fuente en esos archivos. La independencia de PPI Watch se limita a no haberlo tocado y al workflow de preflight, no a una comparación viva del servicio después del candidato.

## 11. Persistencia, concurrencia, sesiones y recursos

SQLite sidecar global usa archivo distinto, checks de alias/hardlink/symlink y máximo 8 MiB, BEGIN IMMEDIATE con timeout 50ms, lease serial durable 60s, rolling window, breakers por endpoint/global para 429/session y dos 408/5xx (budget.py:86–184,210–319). Un lease abandonado retrasa hasta expiración y falla cerrado. Los tests B:73–140,224–329,387+ prueban múltiples procesos/alias/errores de metadata. No prueban fairness entre OPENED y EXIT (F-01), ciclo real con tres workers, alto throughput, clock drift del host ni p99. Un DB locked puede ocasionar backpressure de lecturas, incluido exit reader, y merece un presupuesto de latencia medido.

EvidenceFiles usa flock exclusivo, JSON digest, gzip, rename+fsync y cuotas por payload/archivo/total (persistence.py:12–111); fuente trading se abre ro/query_only en worker/preopen/families/entry lab. No vi borrado de datos productivos ni escritura del SHADOW a su DB fuente. F-03 describe consistencia interarchivo y F-05 el límite de retención. Worker nice(10) y consultas cortas ayudan, pero el costo de serializar dos telemetrías para hasta 20.000 filas, diez familias, preopen, logs y siete procesos en el host no se midió. 10× catálogo, 5× observaciones, burst, disco lento, CPU y locks son pruebas de carga pendientes, no capacidades observadas. Necesita budget de CPU/memoria/duración y alertas de cuota sin afectar reloj de salidas.

Session_context usa America/Argentina/Buenos_Aires y calendario operativo BYMA para sesión/cutoff anterior (worker.py:30–53); byma_paper_spot_phase decide OPEN/PREOPEN/CLOSED. El caso de sábado a lunes y preopen faltante tiene pruebas W:142–148,221–226. Relojes fuente/receipt nativos y rollback se rechazan. Futuros tienen sesión propia más estrecha, pero feriados A3/US session de CEDEAR, vencimiento, contingencias de calendario y desvío de reloj requieren evidencia externa. NOT_DUE en horario cerrado no es fallo ni prueba de readiness del lunes.

## 12. Seguridad PAPER y cadena de suministro

ProductionMarketReader expone marketdata, no método de orden; ReadOnlyTransportGuard valida HTTPS, host exacto y paths GET de configuración/mercado y POST sólo login/refresh en request y adapter.send, deshabilita redirect y fija timeout (bd_ppi_readonly_guard.py:22–45,203–291,303+). Los tests B:211–221 ejercen rutas Order/Confirm/Budget/Cancel y Account/Movements bloqueadas antes de red; revisé imports/callers de los módulos cambiados y no encontré invoke de órdenes reales. El control es una barrera de aplicación por proceso, no una prueba de que una imagen en host no pueda tener credenciales o paths externos; tampoco se auditaron secretos privados. Seguridad del candidato: **PARTIAL a nivel código, NO_VERIFICADO a nivel host**. No usamos PPI Watch ni proveedor.

Promote workflow tiene trigger push a la rama productiva, concurrencia rc6-unified-paper-deploy con cancel-in-progress:false, exige commit de dos padres y tree igual al segundo padre, busca artifact de predeploy exitoso, verifica candidate SHA/tree y hashes de image tar y bundle antes de transferir (porota-deploy-v2-promote.yml:3–133). El predeploy exacto loguea checkout SHA/tree, build once, 456 archivos runtime presentes, import closure y frozen image ID sha256:108c60a1c6a4191f5c4f070972ea5ee8bc74a176d830380be0dba4b0e57e2521; 3020/3020, 0 failures/errors/skips/xfail, una exclusión histórica del test_a3_primary_readonly_hf6.py reemplazada; artifact digest coincide entre log upload y metadata GitHub. scripts/porota_validate_deploy_artifact.py:109–153 cuenta presencia/imports y genera hash de lo presente; no compara independientemente cada byte del artifact con el Git blob. El build desde checkout exacto y Dockerfile COPY aportan procedencia, pero auditor no pudo descomprimir/rehashear el ZIP de 411 MB ni comprobar su manifest/image config/tar contra bytes. El despliegue futuro no reconstruye imagen en host según workflow, pero **no hubo deploy ni runtime check de este HEAD**. Un merge a product con dos padres y tree idéntico dispararía este workflow por push; no es una operación inocua de documentación. El owner de un despliegue futuro debe controlar ese evento explícitamente.

## 13. Observabilidad y explicabilidad

La cadena diseñada registra catálogo/elegibilidad/tradeability/observación/HOT/WARM/señal/economía/riesgo/PAPER/salida/net con reason codes y clocks en orchestrator.py:235–326, stages.py:49–85, entry_signals.py:509–529 y funnel.py:461–563. El funnel hace bootstrap START_AT_CURRENT_TAIL: no rescata retrospectivamente las 20 ruedas. Conserva dos sesiones de agregados y detalle limitado; identifica evaluaciones nativas vs inputs distintos y declara independent_opportunities NOT_CLAIMED, sample_is_independent false, FX no mezclado. Esto es una mejora verificable de *formato causal*, no prueba de que una rueda real tenga todos los campos. La pausa o truncation de source rows deja gaps en el reporte; no se puede rellenar con inferred trades. F-03 y F-04 condicionan consistencia/publicación de reportes. Después de una futura rueda deben medirse funnel por moneda/familia/hora, reconciliar fills y gaps y demostrar por qué no operó, antes de promocionar claims.



## 14. Buyer / acquisition due diligence: preguntas y respuestas

SUPPORTED significa sustentado para la pregunta exacta, no una certificación del producto entero; PARTIAL significa evidencia limitada; UNSUPPORTED significa que el claim favorable no puede sostenerse. No asigno precio monetario.

| Pregunta institucional | Respuesta y grado |
| --- | --- |
| ¿Qué se compra hoy? | **PARTIAL:** motor PAPER spot/Scalping opcional, FUTUROS DLR especializados, tesorería existente, instrumentación SHADOW y workflows; sólo código/CI del candidato, sin runtime del HEAD. bv_paper_runtime.py:298–367; families.py:647–699. |
| ¿Opera dinero real? | **SUPPORTED para el diseño inspeccionado:** no hay método de orden en ProductionMarketReader y rutas reales se bloquean; **NO_VERIFICADO para host/candidato**. bd_ppi_readonly_guard.py:22–45,203–291. |
| ¿Es un radar de mercado en tiempo real? | **UNSUPPORTED:** coverage y latencias OPEN no medidas; OFF conserva rotación 40/180s con foco fijo; fuentes complementarias no garantizan snapshot. cf_intraday_scalping.py:24–42,203–229. |
| ¿Las diez familias son operables? | **UNSUPPORTED:** diez reportes OBSERVE_ONLY; sólo paths factual específicos de familias admitidas, con evidencia desigual. families.py:647–699. |
| ¿Existe edge neto/OOS? | **UNSUPPORTED:** agregado histórico alegado negativo; OOS sin labels; metrics.py:92–137, AUDITORIA_IMPLEMENTACION_PERFORMANCE_RC6.md:18–29. |
| ¿El score 0,62/0,68 es probabilidad? | **UNSUPPORTED:** fórmula heurística, no calibración OOS. be_paper_engine.py:1040–1066; cf:409–425. |
| ¿Costos de cuenta y liquidez ejecutable están certificados? | **UNSUPPORTED:** FeeModel account_terms NO_VERIFICADO; simulación de books/fills sin cuenta/broker. rc6_performance/costs.py:45–102. |
| ¿No-trade supera al candidato en la muestra alegada? | **PARTIAL:** cero nominal > net negativo alegado por moneda; master granular y opportunity cost ausentes. Documento histórico:18–29. |
| ¿Quién fija identidad y qué ocurre si cae PPI? | **PARTIAL:** PPI primaria; IOL/BYMA complementarios sin entry authority; falla/circuit detiene lecturas nuevas, no reconstruye book. source_authority.py:13–162; budget.py:289–311. |
| ¿Hay vendor lock-in? | **PARTIAL:** factual depende de PPI GET y claves exactas; complementar no sustituye contrato/latencia. bd:22–45; cf:673–676. |
| ¿Se puede reconstruir una decisión? | **PARTIAL:** snapshots hashes/reason/clocks y funnel desde watermark; datos previos, raw master y reporte de rueda candidato faltan. entry_signals.py:198–225; funnel.py:482–562. |
| ¿RTO/RPO contractual? | **UNSUPPORTED:** ChildProcesses reinicia hijos en 30s de cooldown y evidencia con checkpoint, pero no restore probado del host/DB ni objetivos acordados. bv:101–138; persistence.py:73–111. |
| ¿Single points of failure? | **PARTIAL:** PPI, un host, DB trading y sidecar shared, supervisor; serial budget limita concurrencia, no redundancia. bv:141–169; budget.py:86–184. |
| ¿Una abierta puede quedar sin supervisión ejecutable? | **SUPPORTED como posibilidad de diseño:** bajo presupuesto dinámico F-01; también book stale/depth insuficiente no permite cierre PAPER sin fill ficticio. bv:172–226; be:1470–1577. Frecuencia/pérdida real NO_VERIFICADO. |
| ¿Qué pérdida máxima garantiza stop/daily risk? | **UNSUPPORTED:** stops requieren book ejecutable, gaps y outages rompen cualquier cota determinista; bw_daily_risk.py:139–245, be:1470–1515. |
| ¿Cómo se mantiene sin autor? | **PARTIAL:** código dividido en muchos módulos, matrices/tests amplios y runbooks; 101 files cambiados, configuración de múltiples workers/paths, conocimiento de proveedor y cuentas no documentado como SLO institucional. |
| ¿Se puede activar dinámica sin nueva programación? | **PARTIAL:** camino APPROVED con fingerprint/aprobación existe, pero F-01 exige corrección previa; sin benchmark OPEN. promotion.py:121–170,274–319. |
| ¿Hay garantía de artifact exacto desplegable? | **PARTIAL:** SHA/tree/log/artifact metadata y workflow guards; bytes ZIP no rehasheados por auditor, no runtime. porota-deploy-v2-promote.yml:35–133; run 37175265248. |
| ¿Usa BYMA API o edita PPI Watch? | **SUPPORTED para código inspeccionado/acciones del auditor:** BYMA scraper complementario, no API nueva; PPI Watch no tocado. families.py:687–699. Host futuro NO_VERIFICADO. |
| ¿Qué evidencia pediría para valorarlo mejor? | **SUPPORTED como condición de diligencia:** fix F-01/F-02, stress F-03, master/fills de 20 ruedas, OPEN benchmark, OOS preregistrado por cohortes, incident drills, hash ZIP independiente y validación host PAPER; ver bloqueos §17. |

Fortalezas verificables: guard de rutas PPI; identity/clock/source field explícitos; separación SHADOW/factual; notional de FUTUROS conservador; costos por moneda y no double-count de spread en fills; pruebas nativas amplias y build once. Debilidades: edge ausente, datos críticos privados sin auditoría independiente, liveness de exit budget, alta dependencia de un proveedor/host, complejidad de siete procesos y reporting por archivos, operabilidad de otras familias sólo observacional. La incertidumbre exige un descuento cualitativo sustancial respecto de un motor de trading validado y hitos de evidencia independientes antes de pagos por alpha/ejecución; la propiedad de código por sí sola no acredita performance ni SLA.

## 15. Red team — 45 escenarios, resultado y brecha

Cada fila distingue comportamiento **esperado**, path **actual observado en código o probe local**, **test nativo inspeccionado** y **gap**. “No ejecutado” no es GREEN ni FAIL de runtime. Todos los ataques fueron conceptuales o offline; no se llamó PPI ni se alteró runtime. B/W/O/C/F/R/E/U/FP se definieron en §3.

| # | Ataque | Esperado | Path actual / evidencia | Test y gap |
| --- | --- | --- | --- | --- |
| 1 | Quote stale | Rechazar BUY/mark como vigente | be.time_error; cf:397–400; source_authority:111–116 | W/E cubren clocks; sin stale real OPEN |
| 2 | Duplicar timestamp Intraday | No nueva muestra | orchestrator.py:143–150 dedup source_at | W:78–126; p95 live ausente |
| 3 | Unidades NOMINAL vs SHARE | No compararlas | tradeability.py:136–146 descarta NOMINAL | F; contrato real por especie pendiente |
| 4 | ARS/USD mezclados | No sumar ni comparar nominal | metrics.py:114–137; be:2182–2184 | R; raw master no accesible |
| 5 | Sin book | No entrada/fill nuevo | cf:388–400,519–534; be:1470–1488 | W/FP; alerta de salida pendiente |
| 6 | Book crossed | Denegar precio inválido | cf:393–397; be:1340–1342 | pruebas de freshness; sin vendor live |
| 7 | Bid_size cero | No fill/salida simulada | be:1340–1342,1483–1488 | FP; liveness de abierta NO_VERIFICADO |
| 8 | Spread extremo | No candidato | cf:404–423; futures be:1663–1669 | Tests de scalping; stress p95 ausente |
| 9 | Gap por debajo stop | No afirmar stop garantizado | be:1470–1515 exige bid/depth y puede cerrar peor | Replay tests; pérdida cola sin muestra |
| 10 | Flash move | No fabricar snapshots intermedios | orchestrator.py:107–166 sólo eventos causales | W/O; missed discovery sin datos |
| 11 | Halt instrument | No BUY; abierta visible | reader/book falla, supervisor permanece sin precio | W/FP parcial; alertas y RTO pendientes |
| 12 | Límite PREOPEN/OPEN | Freeze antes, no backdate | worker.py:235–269 | W:142–148 PASS offline |
| 13 | Cierre y EOD | No apertura tardía, intent de salida | policy y be:1558–1577; bv:141–169 | FP; salida ejecutable no garantizada |
| 14 | Fin de semana/feriado | No OPEN inventado | session_context BYMA; FUT weekday sólo | W:221–226; feriado A3 NO_VERIFICADO |
| 15 | US holiday CEDEAR | No atribuir underlying fresco | tradeability.py:173–191 clock por feature | F; fuente real US ausente |
| 16 | Expiración futuro | No abrir vencido; cerrar si book | future_policy.py:117–158; be:1558–1577 | FP; sin book EOD puede quedar carry |
| 17 | Expiración opción/assignment | No confundir serie con acción | family options OBSERVE_ONLY; be:1702–1706 | F; lifecycle live no certificado |
| 18 | Contrato futuro ausente | Denegar sin fallback | lifecycle.py:610–628,641–656 | FP, PASS código |
| 19 | Multiplicador DLR errado | Denegar; cash 1000 correcto | future_policy.py:94–107; lifecycle.py:619–628 | FP; contrato externo de serie no corroborado aquí |
| 20 | PPI outage total | Fail closed apertura, no cero inventado | guard/budget breaker; reader status ERROR/DEGRADED | B/W; exit liveness NO_VERIFICADO |
| 21 | Outage parcial IOL | Sólo path afectado | source_authority.py:78–139, family reports | F; cache live ausente |
| 22 | HTTP 429 | Circuit global/endpoint, off-wire | budget.py:289–300 | B:90–103; exit también se bloquea |
| 23 | HTTP 408/5xx | Breaker tras umbral, diagnóstico | budget.py:300–311 | B:105–116; p99 live ausente |
| 24 | JSON vacío/malformado | Retry acotado, no precio cero | bd:93–111,148+; budget report_error | B:119–127,151–167; SDK real no revalidado |
| 25 | Cache source stale | No promover por receipt fresco | source_authority.py:102–116; worker.py:270–282 | W:258–295; p95 de source ausente |
| 26 | Mismatch PPI/IOL | Preservar primario y conflicto | source_authority.py:139–162; probe local 100 vs 120 | F + probe PASS; vendor live pendiente |
| 27 | Alias market/currency | No resolver ambiguo al azar | cf:213–218,250–255; orchestrator:67–72 | F/O; catálogos live pendientes |
| 28 | Instrument not found transitorio | Reintentar tras sesión/cambio | cf:596,650–652,689–695 queda excluido | **F-02 FAIL**; test dedicado falta |
| 29 | Cinco opened consumen book | Salida reservada | budget.py:243–263; probe 5/5, EXIT denied | **F-01 FAIL**; B:38–46 no cubre |
| 30 | Scanner last stale + book fresh | Lector exit mantiene supervisión | bf:1848–1865 skip; exit reader puede denegarse | **F-01**; integración falta |
| 31 | Dos motores y discovery burst | No sumar benchmark dos veces | live.py:91–107; budget.py:210–274 | B:59–70/O:342+; carga OPEN ausente |
| 32 | Retry repetido | Cada send consume slot | bd:265–288; retry_read; budget requests | B:151–167; SDK real pendiente |
| 33 | Clock rollback | No habilitar presupuesto/freeze | budget.py:195–201; worker.py:219–221 | B:130–140; host drift ausente |
| 34 | Policy fingerprint/expiry mismatch | Baseline seguro | promotion.py:121–170; budget.py:222–223 | C/B; activación real ausente |
| 35 | Reinicio durante lease | No duplicar cuota | budget.py:229–234,73–85 test | B PASS offline nativo; fallo de host ausente |
| 36 | DB locked | Backpressure/exit degradado explícito | budget.py:179–184,275–276; bv:270–277 | B:330+, W:150–161; p99 host ausente |
| 37 | Dos workers SHADOW | Uno obtiene lock | persistence.py:27–45 | W:173–193; orquestación host no observada |
| 38 | Disco lleno entre archivos | No declarar corte íntegro | persistence.py:73–111; worker latest/checkpoint/status secuenciales | **F-03**; fault injection falta |
| 39 | Symlink/hardlink salida | No sobrescribir input | persistence.py:15–55; budget.py:139–164 | B:224–311/W:173–193 |
| 40 | Artifact SHA/tree mismatch | Bloquear transferencia | promote.yml:35–133; hashes tar/bundle | Run exacto; ZIP sin rehash independiente |
| 41 | Docker image no corresponde al repo | Detectar closure/hash | validator.py:109–153 presencia/imports, manifest | 456 paths log; byte compare Git→image no propio |
| 42 | Rutas reales POST | Denegar antes de red | bd:22–45,203–291 | B:211–221; candidate host NO_VERIFICADO |
| 43 | Fills parciales/restart | No duplicar ni liberar dos veces | lifecycle.py:154–435; costs.py:105–142 | FP/R y tests cost; DB privada ausente |
| 44 | Futuros stale mark/carry | Bloquear nueva entrada y alertar abierta | rc6_paper_family_lifecycle.py:728–809; be:1533–1577 | FP/R; alertas de EOD host pendientes |
| 45 | 10× catálogo/5× observaciones | No bloquear reloj factual | worker.py:72,222–224,311–339,342–373; nice(10) | W fixtures pequeñas; stress NO_VERIFICADO |

## 16. Qué queda genuinamente NO_VERIFICADO

1. Capacidad PPI durante una rueda OPEN por endpoint/global, p50/p95/p99, useful distinct, 429/session, profundidad concurrente y reserva efectiva con abiertas. El presupuesto sintético no es un benchmark de proveedor.
2. Edge neto OOS, score AUC/calibración, hit rate por cohorte, MFE/MAE ejecutable, mercados/regímenes, selección múltiple, tolerancia a costs/slippage/latency y no-trade con costo de caja.
3. Las 20 ruedas granularmente: master/fills y hashes privados, exposiciones y caja por moneda, partials, corporate actions, EOD/STOP/MAX_HOLD, drawdown equity y alternativas same-snapshot.
4. Rutas reales NOT_CALLED y real_orders_sent=0 **del candidato en un runtime**; código y tests de barrera sí inspeccionados. PPI Watch post-candidato: no hubo cambio por esta auditoría, pero no se hizo lectura host.
5. ZIP de 411 MB, tar/config de imagen, manifiestos internos y bytes de cada runtime file rehasheados por auditor; GitHub metadata/log no reemplaza esa comparación.
6. Calidad/frescura de PPI, IOL, BYMA scraper, CEDEAR US/CCL, fixed income, opciones, FCI/caución, FUTUROS DLR y calendarios/fees de cuenta en condiciones de mercado.
7. Duración ciclo, CPU/memoria/disco, lock contention, drift, restart/DR, status/alertas, book outage, slippage y fills PAPER frente a realidad de mercado.
8. Readiness de preopen de la próxima rueda y ausencia de source gaps; NOT_DUE durante mercado cerrado no es PASS.
9. Independencia del ownership fuera de declaraciones públicas GitHub y ausencia de PR posterior fuera de la búsqueda observada.

## 17. Bloqueos exactos antes de un deploy o promoción de capacidad

**Bloqueos de código/guard/test:**

1. F-01: reservar prioridad de lector de salidas separada de abiertas, demostrar con 5 abiertas, current stale/book fresh, tres workers, multi-proceso y límites más estrictos. Reauditar propuesta, no basta retocar un assertion del test B:38–46.
2. F-02: definir vencimiento/revisión por sesión de la negativa Intraday y caso de reaparición sin reiniciar worker; mantener scope por request y no abrir bypass global.
3. F-03: establecer snapshot atómico lógico para latest/checkpoint/status o lector que exige generación/digests coincidentes; fault injection crash/disco/rollback. P2 material de evidencia que impide certificar una rueda reportada.
4. Reejecutar suite y Predeploy V2 contra **nuevo SHA/tree/artifact** después de fixes; todo hash anterior dejaría de ser candidato. Nuevo review de seguridad/regresión y no hacer merge automático para “probar”.

**Gates externos posteriores, que no son bugs ni permiso actual de deploy:**

5. Facilitar auditoría byte a byte del artifact exacto y su manifest/config/tar con herramientas que acepten 411 MB; verificar correspondencia con SHA y sin secretos.
6. Proveer paquete sanitario reproducible de 20 ruedas más metodología y lineage para recalcular balances/partials/costos por moneda, sin credenciales ni DB productiva. Si no se facilita, el dictamen financiero seguirá NO_VERIFICADO.
7. En mercado OPEN, ejecutar benchmark READ_ONLY autorizado y medir capacidad, sin activar APPROVED automáticamente; luego auditar policy/fingerprint/recomendación y contención real con abiertas. No usar más 20/40/60/80/100 como “límite PPI” antes de medir.
8. Recoger ruedas futuras prospectivas SHADOW y OOS para evaluar señal/costos/alternativas frente a no-trade, conservar parámetros preregistrados y denominadores no duplicados. No requiere código nuevo para registrar, sí evidencia temporal.
9. Antes de cualquier deploy futuro con owner específico, confirmar preopen, SLO, recursos, backups, modo PRODUCTION_PAPER/0, bloqueo de rutas reales y PPI Watch intacto mediante validaciones directas del workflow. Esta auditoría no realizó ni autoriza esa ejecución.

## 18. Preguntas que el desarrollador no puede responder con la evidencia entregada

- ¿Cuántos de los 151 fills y 68 posiciones de la muestra histórica sobreviven a una recomputación externa con master, clocks, contrato, cash y costos de cuenta? Los bytes privados no están aquí.
- ¿Cuántos de los HOLD/rechazos hubieran sido positivos netos con el **mismo snapshot y fill ejecutable**? No hay labels confiables para el universo completo.
- ¿Qué distribución OOS de resultado neto por score/strategy/family/symbol/hour/regime y qué drawdown de equity completa se espera? No hay nuevas ruedas.
- ¿Qué p99 real y capacidad PPI conjunta hay con scanner, Scalping, exit reader, 5 abiertas, retries y burst? No hubo medición OPEN.
- ¿Cuánto tiempo queda una abierta sin book ejecutable con 429/5xx, qué alertas escalan y cuál es el RTO/RPO verificado? Sin incidente drill ni host.
- ¿Qué tarifa específica de cuenta, spread p95 ejecutable, slippage y tasa de fills parciales se aplican a cada familia/tamaño? NO_VERIFICADO.
- ¿Qué fuente provee radar transversal fresco a todas las identidades elegibles con latencia menor que oportunidad intradía? Las caches y políticas no demuestran cobertura.
- ¿Qué bytes exactos del ZIP, image tar/config y runtime instalado son idénticos al HEAD? El log del builder no es un rehash del auditor ni un runtime del candidato.
- ¿Qué feriados A3, cambios contractuales, ratios CEDEAR/corporate actions, OI/Greeks y NAV están efectivamente disponibles y vigentes? Requiere validación externa por instrumento.
- ¿Qué costo de CPU/memoria y locks consume SHADOW en el Droplet con catálogo 10× y observaciones 5×? No hay prueba de carga representativa.

## 19. Claims seguros y claims prohibidos

| Puede afirmar, con alcance explícito | No debe afirmar |
| --- | --- |
| “El código de #463 implementa un motor PAPER y observadores SHADOW; su HEAD/tree están registrados y un Predeploy V2 nativo terminó exitoso”. | “#463 está desplegado, validado runtime o listo para merge/deploy sin correcciones”. |
| “Hay guards de PPI read-only y no se identificó en los paths auditados un método de orden real”. | “El candidato ya demostró real_orders_sent=0 en host” o “imposible cualquier bypass” sin validación runtime independiente. |
| “Diez familias tienen routing/estado explícito, muchas OBSERVE_ONLY; DLR 2026 tiene lifecycle PAPER especializado”. | “Todas las familias se negocian o son rentables”, “margen A3 real 100% notional” o “PPI soporta todas las series”. |
| “La capacidad dinámica se entrega OFF, con un camino de aprobación sintético y un presupuesto compartido”. | “Medimos la capacidad PPI OPEN”, “no hay starvation de salidas” o “HOT cubre mercado realtime”. |
| “La auditoría previa declara net histórico negativo separado por ARS/USD_MEP; la aritmética agregada cierra”. | “Se reprodujeron externamente los 20 días” o “se probó alpha/retorno positivo/net expectancy/OOS”. |
| “El funnel y labs permiten futura evaluación causal y marcan NO_VERIFICADO”. | “El funnel observa todas las oportunidades, fills ejecutables y contrafácticos históricos completos”. |

## 20. Dictamen final

**GO_TO_FIX_AND_REAUDIT.** F-01 es un contraejemplo funcional a la seguridad de prioridad del modo dinámico que #462 O/Q/V exige poder activar sin otra programación. F-02 y F-03 afectan cobertura y consistencia operacional. Los datos financieros e imagen exacta no están disponibles para verificación independiente completa; éstas son limitaciones de evidencia, no una acusación de fallo oculto. El candidato no adquiere DEPLOY_OWNER ni WRITE_OWNER por este informe. No se modificó código, PR, branch, runtime, DB ni PPI Watch; no hubo merge, deploy, llamadas PPI ni órdenes. Cualquier candidato corregido exige nueva auditoría de SHA/tree/run/artifact y luego evidencia OPEN/OOS separada antes de un claim económico.

### Apéndice de reproducción offline mínima

Se importó rc6_ppi_global_budget.GlobalPPIBudget del HEAD en Python stdlib con PYTHONDONTWRITEBYTECODE=1, reloj fijo 2026-10-05T14:00Z y TemporaryDirectory. Policy sintética válida: schema V1, ventana 30s, current/book/intraday 5, global 15, max_parallel_requests 1, EXIT_CRITICAL reserve book 5, expires +1h, lease 60s, máximo 8 MiB. Cinco ciclos acquire/start/finish(book, consumer=SCANNER, priority=OPENED_CRITICAL) devolvieron True. Un acquire(book, consumer=EXIT_READER, priority=EXIT_CRITICAL) devolvió allowed=False, reason=PPI_BUDGET_EXHAUSTED; metrics(book) requested 6 / allowed 5 / used 5 / dropped 1. No se simuló HTTP, fills ni DB productiva. La prueba de fuente llamó resolve_field con PPI=100 e IOL=120 ambos frescos: PPI elegido, conflicto=true, entry_authority=false. Son contraejemplos/revisiones puntuales, no una ejecución de la suite ni una rueda OPEN.
