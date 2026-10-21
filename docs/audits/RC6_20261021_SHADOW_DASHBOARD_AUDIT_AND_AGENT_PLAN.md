# POROTA RC6 — auditoría forense funcional y plan de remediación autónoma

**Corte:** 21 de octubre de 2026, aproximadamente 16:50 ART.  
**Tipo de entrega:** READ_ONLY sobre producto/runtime, WRITE_OWNER exclusivamente para este Markdown; NO es implementación, autorización de un nuevo despliegue ni un certificado GREEN.  
**Workstream documental:** WS-RC6-20261021-FORENSIC-AUDIT-DOC. **Rama de documento:** docs/rc6-20261021-functional-audit, base main 5a87538d3816601a4fddf4aaafc975b4ebc674d7.  
**Producto auditado:** deploy/rc6-pr69-isolated-20260915 @ f0c393ca2b5c1560bf57cf776f1e9fcfcbca582f; árbol 08c44cd1c4ee035d1419aa4dd35774802c212a3f.  
**Leyenda de estados:** VALIDADO_RUNTIME sólo para observaciones efectivas y acotadas; NO_VERIFICADO para faltantes; BLOQUEADO para evidencia o requisitos faltantes; EN_GITHUB y ARTEFACTO_VALIDADO no equivalen a runtime.

## 0. Dictamen ejecutivo y límites

**Resultado:** el candidato de PR #683 tiene Predeploy V2 SUCCESS 4481/4481, Deploy V2 SUCCESS y diez generaciones nativas con reserva GREEN; una lectura independiente posterior encontró tres contenedores de imagen idéntica, PAPER/0, SHADOW committed/coherente/fresco y 52 rutas HTTP 200. El **sistema completo NO está certificado GREEN**: no hubo QA visual genuina, no está demostrada la primera rueda completa posterior al último deploy, la capacidad OPEN y preopen siguen NO_VERIFICADO, la cobertura de SHADOW se trunca en la lectura, ARS carece de baseline de riesgo, la historia anual no tiene identidad/bases contractuales completas y hay relojes de fuente STALE. Los checks no autorizan inventar datos para convertir esos estados en verde.

Se identificó además una falla real de Market Watch a las 14:58 ART del 21/10, ANTERIOR al último deploy: clasificación P0 por discrepancia de imagen del servicio crítico y procedencia inválida; el mismo recibo registró silencio de schedules, performance stale y disco por debajo del target. La sonda de las 16:10 ART probó identidad homogénea **en un corte posterior**, pero no existe otra prueba equivalente de la cadencia genuina del monitor. La falla histórica no se borra y tampoco se extrapola al runtime de las 16:10.

**PROHIBIDO:** órdenes reales, mutar o inspeccionar activamente PPI Watch, host stress de 405 minutos, rollback, bypass de guardas, correcciones directas en main/productiva, borrado de DB/WAL/SHADOW/evidencia, exponer secretos o capturas con datos sensibles en repositorio público, crear un segundo scheduler, fabricar operaciones/fills/ingestas. Backups expresamente suspendidos por el operador; no reactivarlos por iniciativa propia.

## 1. Evidencia canónica y trazabilidad

| Capa | Identificador y observación |
|---|---|
| Código GitHub | main 5a87538d3816601a4fddf4aaafc975b4ebc674d7; product f0c393ca2b5c1560bf57cf776f1e9fcfcbca582f. Consulta directa de ref productivo efectuada en esta auditoría. |
| Candidato producto | PR #683, source 122bc4eafe7959579f2223257d8d48f61caf62f5, tree 08c44cd1c4ee035d1419aa4dd35774802c212a3f. |
| Pipeline/artefacto | Predeploy 39651245160 SUCCESS 4481/4481; artifact 12372375291 digest sha256:5292de83c4edb2f1e15b80c14c9685ac29af6891bee5e5c3939f0abd337f2eb0. |
| Promoción | Deploy canónico 39655518694 SUCCESS, finalizado 19:08:29 UTC, estado VALIDATED_RUNTIME en recibo. Diez generaciones nativas con reservas y auditoría inmediata/demorada pasaron; host stress SKIPPED por instrucción del operador. |
| Paquete vivo observado | Imagen sha256:b27dd6670c1231de49277c0bce1729b42f23a71837a25bb6d2ccfea9edcfe8b0, los tres contenedores coincidentes a 19:10:26 UTC. |
| Comportamiento de muestra | Diagnóstico independiente 39659192906, 19:10:26 UTC: PAPER RUNNING/MARKET_OPEN, real_orders_sent=0, real routes NOT_CALLED; SHADOW COMMITTED_COHERENT_SHADOW, 2 motores, edad 74/120 s; heartbeat factual 45,5/75 s; 52/52 HTTP200. |
| Droplet DO control plane | Droplet 594077619 con status active, tamaño reportado s-2vcpu-2gb (2 vCPU / 2048 MiB). API DigitalOcean no comprueba procesos/DB/HTTP, y los campos de capacidad de disco del plan y la instancia requieren conciliación con df nativo. |
| Handoff previo | PR documental #684 DRAFT, head 69acf69da4b2fe8e6196bf70aa3f1d204c7bf065, archivo docs/handoffs/RC6_679_HANDOFF_2026-10-21.md. NO fusionar a productiva. |
| Ownership anterior | Product/DEPLOY_OWNER de #683 liberados en #471/6324337442 y #473/6324339175. Dueño documental de #684 liberado en #471/6325000467 y #473/6325001893. Releer la punta antes de intervenir. |

Fuentes principales:
- https://github.com/mbalbo2023/Porota-trading/pull/684
- https://github.com/mbalbo2023/Porota-trading/issues/679#issuecomment-6324328778
- https://github.com/mbalbo2023/Porota-trading/actions/runs/39655518694
- https://github.com/mbalbo2023/Porota-trading/actions/runs/39659192906
- https://github.com/mbalbo2023/Porota-trading/blob/f0c393ca2b5c1560bf57cf776f1e9fcfcbca582f/docs/releases/RC6_679_BACKUP_DASH_AUDIT_20261021.md
- https://github.com/mbalbo2023/Porota-trading/issues/624

El último listado de Actions de esta lectura no mostró Deploy V2 posterior a 39655518694, pero no es vigilancia continua. El 22/10 es la primera rueda entera posterior al último deploy; no usar un corte del 21/10 para certificarla antes de que ocurra.

## 2. Cronología de las fallas del 21/10

| Hora UTC | Hecho | Dictamen |
|---|---|---|
| 16:22–16:27 | Intentos de retiro de backups RED, incluido systemd rechazando mask --force sobre unidad regular; corrección del procedimiento. Action 39634914650 SUCCESS retiró 31 archivos exactos y recuperó espacio. | Error histórico reproducido; retiro VALIDADO por recibo, suspensión confirmada en sonda posterior. |
| 17:00–17:03 | Deploy V2 39639853326 FAILED antes de aceptar nueva imagen: COLD_ARCHIVED_RECOVERY_NOT_PROVEN. El recibo intradía decía intraday_caucion y el guard sólo admitía paused_postclose. | ERROR→RCA y fix-forward #682; no reintentar SHA rojo. |
| 17:57–17:58 | Schedule auténtico Market Watch 39648438228 FAILED por P0 confirmado: CONTAINER_IMAGE_MISMATCH:porota_critical_approval_rc6 y DEPLOY_PROVENANCE_INVALID. Hallazgos adicionales DISK_BELOW_TARGET, PERFORMANCE_REPORT_STALE y SCHEDULE_SILENCE_TWO_INTERVALS. | RED histórico real previo al deploy final; monitorización genuina sigue requiriendo prueba nueva. |
| 17:49–18:08 | Deploy V2 39647192358 SUCCESS para #682, COLD2 y caución PAPER intradía conservada. | VALIDADO_RUNTIME en su corte. |
| 18:12 | Sonda 39650565955 encontró falso STALE en latido PAPER a 31,8 s, aunque observer duerme 60 s. | RCA preciso: umbral de vista 30 s; corregido en PR #683 con rango controlado +15 s. |
| 19:08 | Deploy V2 39655518694 SUCCESS para #683: 10 generaciones nativas, seguridad, estabilidad y limpieza. | VALIDADO_RUNTIME acotado. |
| 19:10 | Sonda 39659192906: imagen homogénea, SHADOW 74/120, factual 45,5/75, 52 HTTP200, PAPER/0, backup timer masked. | PASS factual de esa muestra; NO prueba QA visual, histórico anual ni primera rueda entera. |
| 19:45 | PR documental #684 disparó CI SUCCESS 39664351709 pero Checkpoint attestation FAILED 39664351619. | P2 documental, no problema del motor: PR body sin línea obligatoria Checkpoint ID. |

No contar intentos administrativos cancelados como errores funcionales de mercado, ni un workflow SUCCESS como aceptación total del comportamiento.

## 3. Matriz priorizada de hallazgos, causa, corrección y prueba exigida

### F01. Market Watch P0 de las 14:58 ART: procedencia/imagen

**HECHO:** Action 39648438228, job 119390179164, emitió P0 por CONTAINER_IMAGE_MISMATCH del critical_approval y DEPLOY_PROVENANCE_INVALID; fail de clasificación con exit2. También constó estado SHADOW_OBSERVING, no es un fill. **HECHO POSTERIOR:** Action 39659192906 encontró los tres contenedores con imagen exacta del último deploy. **No resuelto retrospectivamente:** no se ejecutó el mismo monitor sobre el nuevo estado con veredicto P0/PASS comparativo.  
**HIPÓTESIS a probar:** control-plane de main a SHA anterior, reconciliación incompleta/transitoria del servicio crítico o contrato de identidad distinto entre monitor y Deploy; ninguna se declara RCA probado.  
**FIX PROPUESTO:** comparar pin esperado de cada servicio con imagen real y manifest del deploy vigente en snapshot atómico; distinguir drift de release, transición gobernada y falso positivo por discrepancia de referencia. No desactivar controles de procedencia.  
**GUARD / TEST:** tests before/after para imagen crítica distinta real, imagen homogénea, carrera durante promoción y main atrasado; comparar recibos firmados de Deploy y monitor con timestamps/árbol. Una sonda READ_ONLY en ventana legítima tiene que entregar evidencia actual, no replay del run viejo.  
**Estado:** HISTÓRICO RED / resultado actual del MONITOR NO_VERIFICADO. P0 antes del deploy, no P0 afirmado sobre el runtime final.

### F02. Ausencia de cadencia genuina / alertas externas

**HECHO:** schedule-health del mismo run dio genuine_recent_runs=0, state=SCHEDULE_SILENT y silencio de más de dos intervalos; ACK del watchdog era HISTORICAL_VERIFIED, no ACK del ciclo actual. El paso Telegram indicó SENT, lo cual **no acredita recepción humana** ni tres schedules correctos.  
**FIX PROPUESTO:** revisar cron/permissions/branch/timegate y continuidad de GitHub schedule; preservar un único monitor canónico y deduplicación por transición.  
**GUARD / TEST:** tres runs con event=schedule y separación esperada, el mismo SHA productivo pinneado, recibos distintos y ACK real cuando hubo transición; distinguir push/dispatch/retry de schedule.  
**Estado:** P1 NO_VERIFICADO. No crear timer nuevo ni enviar notificaciones de prueba para fabricar salud.

### F03. COLD intradía y caución PAPER

**HECHO:** Deploy 39639853326 RED al intentar adoptar recibo COLD de caución intradía; el check anterior sólo aceptaba paused_postclose. PR #682/Predeploy 39642951943/Deploy 39647192358 corrigieron la transición con identidad SHA, ventana <=15s, libro 0 spots/1 caución/0 futuros y mismas identidades Docker; 39655518694 volvió a certificar COLD/COLD2.  
**FIX/GUARD ya integrado:** aceptar sólo testigo exacto, fallar cerrado ante ventanas/IDs/libros divergentes y ante orden real.  
**PENDIENTE:** la última sonda tenía una caución PAPER OPEN con vencimiento 17:00 ART. Verificar ledger, liquidación natural y estado de salida de la jornada. No sintetizar settle, cierre ni fill.  
**Estado:** transición COLD VALIDADA_RUNTIME en su corte; final económico y cronológico de caución NO_VERIFICADO.

### F04. Falso STALE de heartbeat PAPER

**HECHO:** vista previa consideraba STALE a 31,8 s por TTL fijo30 frente al intervalo configurado60; PR #683 aplica intervalo válido15..120 más15 y si es inválido conserva fail-closed30. En sonda final FRESH45,5/75.  
**Estado:** FIX VALIDADO_RUNTIME EN MUESTRA; monitorizar otra rueda para detectar regresión sin extender indiscriminadamente TTL de datos, libro, trades ni SHADOW.  
**GUARD:** 61s fresh, 76s stale y configuración inválida stale31; indicador muestra presupuesto efectivo.

### F05. SHADOW coherente pero cobertura trunca y aceptación OPEN incompleta

**HECHO:** SHADOW COMMITTED_COHERENT_SHADOW, dos motores, edad74/120s; observation_read_truncated=true por límite de 3000 puntos por fuente/día. Candidato #681 optimizó CLOSED, no se demostró A/B de recursos homogénea ni liveness continuo durante una rueda completa.  
**HIPÓTESIS distintas:** la truncación podría ser sólo límite del lector de presentación o afectar medidas usadas en decisiones; debe verificarse flujo producer→persistencia→funnel→lector→estrategia y denominador.  
**FIX PROPUESTO:** separar cobertura observada, cobertura requerida, cobertura censurada y costo de la lectura; hacer paginación/cursor acotados o agregados matemáticamente equivalentes **sólo si la estrategia requiere esos puntos**, sin aumentar RAM ni autoridad de señal a ciegas. No convertir SHADOW_OBSERVING en COMMITTED artificialmente.  
**GUARD / TEST:** fixture >3000 y cortes por fuente/sesión, checks de monotonía, SHA, provenance, COLD COMPLETE, edad<=120s, no perdida silenciosa de errores; equivalencia de decisiones antes/después.  
**Estado:** salud SHADOW puntual VALIDADA; cobertura integral y primera rueda completa NO_VERIFICADAS.

### F06. Capacidad OPEN y preopen sin baseline

**HECHO:** capacity_open_status=NO_VERIFICADO, capacity_policy OFF_BASELINE, preopen_quality NO_VERIFICADO. No es algo que deba pintarse verde por HTTP200.  
**FIX PROPUESTO:** censar baseline OPEN con fecha/fase/denominador/CPU/RSS/frescura de proveedores; comprobar contrato de preopen, snapshot publicado, algoritmo de capacidad y su wiring al dashboard.  
**GUARD:** no aumentar APPROVED_DYNAMIC ni autorizar entradas por conteos de catálogo; tests de baseline ausente, futuro, parcial, stale y firma incorrecta.  
**Estado:** BLOQUEADO POR EVIDENCIA, no por cosmética.

### F07. Riesgo ARS sin referencia validada

**HECHO:** ARS BASELINE_UNAVAILABLE; USD/CCL/MEP READY en una muestra previa, que no aporta autoridad a ARS.  
**FIX PROPUESTO:** rastrear provider → moneda/base/settlement → período → snapshot/reader → riesgo. Identidad PPI primaria, IOL/BYMA complementarias sin sobreescritura; no imputar retorno/FX inexistente.  
**GUARD:** mezcla ARS/USD prohibida, no conversión implícita, fail-closed de estrategias dependientes y pruebas con fuente vencida.  
**Estado:** NO_VERIFICADO/BLOQUEADO en ARS.

### F08. Instrumentos y Mercado & Liquidez: STALE real de fuente

**HECHO:** las cuatro vistas con identidad exacta respondieron HTTP200; ficha/contrato/evidencia sin STALE compartido en sonda posterior. Mercado & Liquidez contiene ocho **apariciones** STALE en HTML ligadas al mismo tipo de fuente, no ocho incidentes distintos.  
**FIX PROPUESTO:** por identidad exacta informar timestamp de mercado, recepción, chequeo, edad, source, last-known-good y presupuesto por dato; cotización vieja sigue STALE aunque heartbeat nuevo.  
**GUARD:** pruebas de dos monedas y quotes frescos/viejos, paginación/scroll, fuente ausente, igualdad entre API/DB/UI, no promocionar READY por familia o HTTP.  
**Estado:** datos fuente STALE; RCA de origen por instrumento NO_VERIFICADO.

### F09. Anual/histórico: cero series seleccionables

**HECHO:** market_history.db ~639 MB, history_canonical_v2 max rowid 569482 pero no tiene currency, price_basis ni adjustment_basis requeridos por Projection.history().canonical(). Fallback production_history 1151 registros totales, muestra diez y 0/10 con identidad y bases completas; /analitica/anual HTTP200 pero cero enlaces a series anuales. El máximo rowid no acredita recuento de filas válidas.  
**FIX PROPUESTO:** ADR de identidad completa y linaje: fuente primaria, instrumento, moneda, settlement, ajuste, price basis, version_known_at, event_at/observed_at; adapter y esquema compatibles mediante migración gobernada sin inferir campos. Tratar datos históricos legacy ambiguos en cuarentena.  
**GUARD / TEST:** fixtures de moneda/base mixtas, datos futuros/incompletos, migración atómica, reingesta idempotente, PK/dedupe, comparación de cortes, lectura de una serie real con trazabilidad. No llenar anuales con historia SAMPLED, divisas supuestas ni un CSV arbitrario.  
**Estado:** BLOQUEADO por procedencia y contrato de datos; UX explica honestamente ANNUAL_NO_SELECTABLE_CANONICAL_SERIES.

### F10. Dashboard: auditoría HTML no es QA visual

**HECHO:** 52/52 respuestas 200 y extracción de texto/contadores; **cero navegación visual autenticada**, capturas o interacción browser acreditadas. La revisión previa de 52 rutas tiene apariciones repetidas NO_VERIFICADO/STALE y fue anterior a los últimos fixes; nunca contarlas como fallos independientes actuales.  
**FIX PROPUESTO:** auditoría visual real mediante navegador interactivo autorizado del agente Work/Computer Use con sesión válida, o navegador headless en runner aislado que acceda sólo al loopback a través de túnel gobernado de lectura y secreto efímero. Sin sesión/token, dejar VISUAL_NO_VERIFICADO: prohibido exponer 8000 o publicar credenciales.  
**GUARD / TEST:** inspeccionar TODAS las 52 rutas del manifiesto congelado, además de 4 drilldowns por identidad exacta; viewport tablet Android y móvil, lectura de gráficos, leyendas y tooltips, scroll vertical/horizontal, tablas diez filas+ver más, elipsis, foco/orden tab, controles operables por voz, autenticación, estados vacíos y errores JS, coherencia campo por campo con DB/API. Vincular cada captura autorizada a SHA/runtime/as_of/identity sin guardar información sensible en artifacts públicos. Pruebas visuales/regresión con fixtures redacted; no sustituir con HTML/PDF synthetic mockups.  
**Estado:** QA VISUAL NO_VERIFICADO.

### F11. Crecimiento físico, WAL, amplificación de escritura y duplicación

**HECHO:** entre 18:12:39 y 19:10:26 UTC (incluye deploy) espacio libre 7.747.600.384→7.633.133.568 B, diferencia -114.466.816 B; observer_v17.db +11.550.720 B, WAL -57.680 B, market_history.db igual, shadow +503.542 B. Worker dinámico acumuló 23.281.664 B write_bytes/5s, comparado con 83.460.096 B/5s tras promoción previa. Las unidades y períodos no permiten afirmar crecimiento exponencial ni duplicación de filas.  
**FIX PROPUESTO:** series comparables a fase y carga constantes, SQLite page_count/freelist_count, WAL/checkpoint, dbstat cuando permitido, tamaños aparente/físico, bytes por PID/cgroup, COLD pack/GC y fuentes/dedupe. Diferenciar bytes escritos por proceso de bytes añadidos en archivo y espacios recuperables.  
**GUARD:** sólo READ_ONLY acotado durante rueda y respetando reserva128MiB/2GiB; NO VACUUM, full quick_check global, rebuild ni prune ciego con rueda abierta; luego RCA/guard de idempotencia si se prueba duplicación.  
**Estado:** tendencia/duplicación NO_VERIFICADA. Space medido >7 GB no garantiza margen futuro.

### F12. Backups retirados, coste y riesgo de continuidad

**HECHO:** Action 39634914650 retiró 31 archivos de tres raíces, tamaño lógico 4.519.770.023 B; el operador lo autorizó. Sonda final: raíces vacías, host general timer masked/inactive y productores suspendidos por política. La estimación anterior ~17 GB era un requisito de CREACIÓN con dos copias+2GiB+64MiB, no ocupación de backups.  
**RIESGO ACEPTADO:** backup suspendido/keep=0 implica ausencia de esa capa de recuperación; RPO/RTO y DR offhost son NO_VERIFICADO, no GREEN.  
**FIX PROPUESTO:** mantener explícita la suspensión hasta decisión humana nueva; dashboard debe mostrar SUSPENDED_BY_OPERATOR, no confundir con fallo de servicio ni declarar respaldo existente. Medir backup attempts bloqueados sin secret leaks.  
**GUARD:** nunca reactivar temporizadores/retención automáticamente, no tocar base activa ni recibos COLD de fallos; tests de escritor legacy/force/host que fail-closed.

### F13. Validez de la primera rueda completa / estrategias y cierres

**HECHO:** el 21/10 a 16:10 ART se observó 0 spots abiertos, 1 caución OPEN, 0 futuros; scalper heartbeat 122,2/300 y supervisor/lector/cauciones frescos. No se ha probado una rueda completa de la imagen f0c..., ni fills PAPER de scalping ni resultados finales EOD/TP/STOP.  
**FIX PROPUESTO:** aceptación PAPER por fecha e instrumento: 10 generaciones frescas sostenidas, book/trades/datos exactos, decisiones y abstenciones con razones, almacenamiento de señales, snapshots pre/post, secuencia de órdenes SIMULATION exclusivamente, TP/STOP/EOD/MAX_HOLD según triggers reales, cauciones al vencimiento y origen de cada evento.  
**GUARD:** no forzar trades ni cerrar posiciones para cumplir métricas. Si no hubo señal elegible, reportar NO_SIGNAL_EXPECTED con cobertura/condición acreditadas; si falla data, marcar bloqueado. Si no hay capturas suficientes, NO_VERIFICADO.  
**Estado:** NO_VERIFICADO hasta rueda posterior y conciliación DB/logs.

### F14. PR #684 documental con attest fallida

**HECHO:** run 39664351619 FAILED en paso "Require continuity and safety references in PR body", error exacto "PR body is missing: Checkpoint ID". La rama docs/main no modifica runtime; CI convencional 39664351709 SUCCESS.  
**FIX PROPUESTO:** completar metadata del cuerpo de PR #684 sin cambiar archivo fuente ni workflow: cinco líneas obligatorias definidas en checkpoint-attestation.yml (Checkpoint ID, Continuity checkpoint path, Checkpoint commit SHA, RC6 baseline SHA, Safety invariants). No modificar el guard para obtener verde.  
**GUARD/TEST:** reejecución legítima asociada a metadata actual y evidencia de PR; si failure persiste, RCA nuevo.  
**Estado:** P2 EN_GITHUB/CI DOC RED; independiente del último Deploy SUCCESS.

## 4. Auditoría visual: matriz de rutas y cómo evitar falsos defectos

La matriz íntegra de 52 rutas y apariciones históricas está en docs/releases/RC6_679_BACKUP_DASH_AUDIT_20261021.md del HEAD f0c. Prioridad de navegación (siempre recontar con la imagen actual):

| Vista | Indicador HTML anterior | Verificación visual requerida |
|---|---|---|
| /en-vivo/decisiones | 282 NO_VERIFICADO / 30 STALE | Diagnósticos, fuente por identidad, denominador, orden temporal, filtros y plegado. |
| /sistema/scheduler | 272 NO_VERIFICADO | Mapping recorded_at/active_state/enabled_state/next_elapse/last_trigger y transición de 32 filas de inventario a 19 ACTIVE_TIMER gobernados sin confundir conjuntos. |
| /en-vivo/fuentes | 181 NO_VERIFICADO / 26 STALE | PPI/IOL/BYMA por source-path y clock, no proveedor completo caído. |
| /trading/equity-spot | 248 NO_VERIFICADO / 17 STALE | Candidatos no igual a entradas READY; book/exits y moneda. |
| /trading/scalping | 198 NO_VERIFICADO / 17 STALE | Microventanas, señal/abstención, sample coverage/TTL. |
| /analitica/anual | 142 NO_VERIFICADO, 0 series seleccionables | UX de bloqueo correcto, no gráfico falso. |
| /sistema/workers | 146 NO_VERIFICADO / 4 STALE | Latidos por cadencia validada, SHADOW CURRENT/COLD y PAPER factual. |
| /instrumentos/mercado con identidad | Ocho apariciones STALE en muestra final | Reloj real de cotización y profundidad, fichas separadas. |

Son **ocurrencias previas de texto**, no cardinalidades de incidentes ni resultado de la última captura. Auditoría verdadera debe producir matriz por ruta, identidad, widget, componente, origen, reloj, estado backend, estado renderizado y screenshot verificado, con capturas redactadas cuando corresponda. La salida final también debe separar bugs UI de ausencia legítima de datos.

## 5. Misión precisa para un agente posterior, no ejecutada aquí

**Objetivo:** auditar exhaustivamente la jornada 21/10 y la primera rueda completa 22/10, realizar QA VISUAL real, corregir sólo hallazgos con RCA confirmado y dejar un candidato único FIX-FORWARD, con Deploy V2 y aceptación nativa únicamente si todas las condiciones están demostradas. Si faltan fuentes o no hubo mercado/eventos probatorios, entregar NO_GO concreto: no existe permiso para inventar GREEN.

**Fase 0 — Gobierno READ_ONLY.** Leer este dossier, PR #684, #679, #624, #680/#681/#682/#683, AGENTS.md y ops/policy/porota-policy.yaml del HEAD REAL. Comparar main/product HEAD, Actions, PRs/owners #471/#473, runtime a través de workflow existente read-only acotado y permisos/estado del host. No considerar un comentario histórico como owner vigente. Revisar 39648438228 P0 y su vigencia tras f0c. No modificar PPI Watch. No disparar carga paralela en el Droplet.

**Fase 1 — Cierre de la jornada 21/10 sin inferencias.** Contrastar libros PAPER, caución vencimiento17 ART, exit intents, EOD/cierre, decisiones SHADOW con DB/logs/as_of/provenance, presencia de señales, fuentes y red flags. El monitor P0 es histórico; exigir evidencia actual sobre los cinco códigos sin borrar run anterior. Consolidar capturas de estado y los 52 endpoints desde fuente actual; aún NO es visual.

**Fase 2 — Primera rueda del 22/10.** La automatización "RC6 primera rueda PAPER" está documentada para 12:30 ART (ID 6ad8e30010bc81919dd10e47d973f2a2), pero no había last_run verificado: comprobar existencia/ejecución en su herramienta y NO crear una duplicada. Adquirir únicamente evidencia READ_ONLY genuina de MARKET_OPEN, data source timestamps, 10 generaciones SHADOW con 120 s máximo de edad, dos motores, ledger PAPER/0, scalper/candles/history incremental, cau­ciones/supervisor, memoria/disco/WAL y cierre/EOD. Durante OPEN sólo consultas pasivas acotadas que no roben 128MiB/2GiB ni saturen IO; sin host stress. Cualquier guard rojo es NO_GO sin bypass.

**Fase 3 — Visión real con navegador.** Usar Work/Computer Use con sesión ya autorizada o Playwright/Chromium en runner aislado mediante acceso acotado al localhost autorizado. No instalar browser pesado en Droplet ni exponer panel en Internet. Capturar 52/52 pantallas + 4 drilldowns exact-ID en anchos tablet y móvil, revisar clic/scroll/foco/voice control, visibilidad de números, semáforos, filtros, 10 filas y ver más, gráficos/legendas y errores JS. Evidencia que pueda ser pública: solamente conteos/hashes/metadatos sin token/PII; pantallas privadas deben permanecer protegidas. Si no hay sesión autorizada, marcar visual NO_VERIFICADO.

**Fase 4 — RCA/plan de datos y capacidad.** Resolver origen de baseline ARS, schema histórico completo, missing annual selectors, cap3000, capacity OPEN/preopen, quotes STALE, calendario/cron real, DB/WAL/crecimiento, restauración/DR suspendida. Orden prioritario: integridad y frescura SHADOW, monitor P0/scheduler, salidas PAPER/caución, fuentes y riesgo, QA visual, anuales/datos históricos, capacidad/deduplicación. No gastar semanas retocando badges antes de validar el dato.

**Fase 5 — Correcciones autónomas gobernadas.** Antes de tocar un path, reclamar WRITE_OWNER exclusivo pareado (#471/#473), branch aislada con base HEAD exacta y ámbito declarado; preservar #684/PR antiguos; agrupar bugs independientes en análisis/pruebas paralelas OFFHOST, integrar un único candidato reconciliado. Para cada error: ERROR → RCA → FIX → GUARD → regresión negativa/positiva → Predeploy V2 sin FAIL/ERROR/SKIP/XFAIL → evidencia exacta. Aumentar TTL, relajar RSS/SQL/disco/frescura, habilitar órdenes reales o cambiar PPI Watch no son soluciones. Si falta evidencia del contrato proveedor, permanecer fail-closed.

**Fase 6 — Deploy V2 sólo cuando admisible.** Verificar condiciones legítimas actuales en #624, owner global DEPLOY_OWNER pareado, mutex rc6-unified-paper-deploy con cancel-in-progress=false, mercado/libro PAPER seguro, preflight RAM/disco dinámico, artifact exacto SHA/tree/tar/manifest/digest y COLD sin pendiente. Merge normal de dos padres; un solo build y único despliegue. No rebuild en Droplet, no rollback, no 405min/estrés host. Restaurar mismos servicios/timers gobernados sin backup host suspendido; aceptar con generaciones coherentes/continuas, HTTP, DB, modos y PPI Watch intocado. Un workflow SUCCESS no concluye lo pendiente de datos/visual.

**Fase 7 — Cierre y handoff.** Actualizar 02-ACTIVE_PENDING.md, 03-CURRENT_STATE.md, 06-ERROR_REGISTRY.md y 09-LESSONS_LEARNED.md sólo con owner pertinente y evidencia; publicar issue/PR/runs/artefactos/source SHA/tree, matriz de hallazgos y limitaciones; liberar WRITE_OWNER y DEPLOY_OWNER pareados. Nunca usar esta documentación como garantía de éxito futuro.

## 6. Criterio de GREEN por capa, NO un semáforo único cosmético

- **Seguridad PAPER:** estado firmado PRODUCTION_PAPER/SIMULATED, real_orders_sent entero0, rutas reales NOT_CALLED, PPI Watch sin mutación/llamada, DB consistente.
- **Runtime técnico:** SHA/tree/imagen/manifest exactos, contenedores/units/timers correctos, sin OOM/restarts nuevos, COLD/COLD2 y diez generaciones completas bajo reservas, rutas reales verificadas y cleanup medido.
- **Mercado/PAPER funcional:** rueda abierta real posterior a deploy, fuentes por identidad frescas, cotizaciones/volumen/provenance explícitos, decisiones/abstenciones y salidas/cauciones debidamente justificadas; sin simulación falsificada de resultados.
- **Dashboard veraz:** 52 rutas + drilldowns navegados efectivamente, estado/UI cruzado con fuente/DB, accesibilidad validada. Mostrar NO_VERIFICADO o STALE cuando corresponde **es un resultado correcto**, no una razón para cambiar a VERDE.
- **Datos/histórico:** moneda, price_basis, adjustment_basis, versión y origen verificables o bloqueo explícito; las series anuales no se desbloquean por suposición.
- **Monitorización:** runs genuinos schedule y checks sanos actuales, no repetir el P0 histórico en la versión nueva, sin silencios injustificados; entrega externa con ACK cuando sea exigible.
- **DR/backups:** el operador mantiene suspensión; se etiqueta capacidad de recuperación NO_VERIFICADA sin crear copias ni habilitar timers por cuenta propia.
- **Exclusiones:** host stress oficialmente SKIPPED por instrucción, NO llamarlo TEST_GREEN ni ocultarlo. Si no puede demostrarse alguna capa, informar VERIFICADO_PARCIAL/NO_GO con alcance exacto.

## 7. Entradas rápidas al próximo ciclo

1. [PR #684 handoff y documentos](https://github.com/mbalbo2023/Porota-trading/pull/684).
2. [#679 estado y diagnóstico final](https://github.com/mbalbo2023/Porota-trading/issues/679#issuecomment-6324328778).
3. [Deploy V2 actual](https://github.com/mbalbo2023/Porota-trading/actions/runs/39655518694).
4. [Sonda de 52 rutas/host](https://github.com/mbalbo2023/Porota-trading/actions/runs/39659192906).
5. [Market Watch P0 histórico](https://github.com/mbalbo2023/Porota-trading/actions/runs/39648438228).
6. [Fallido COLD anterior](https://github.com/mbalbo2023/Porota-trading/actions/runs/39639853326).
7. [Checkpoint attestation rojo del handoff](https://github.com/mbalbo2023/Porota-trading/actions/runs/39664351619).
8. [Owners de integración #471](https://github.com/mbalbo2023/Porota-trading/issues/471) y [#473](https://github.com/mbalbo2023/Porota-trading/issues/473), [seguridad master #624](https://github.com/mbalbo2023/Porota-trading/issues/624).

**Cierre de esta auditoría:** se documentan HECHOS/hipótesis/gates, sin escribir código ni ejecutar reparación, nuevas sondas host, browser visual, operaciones PAPER, stress ni deploy. La observación del 22/10 todavía no existe al redactar. Cualquier estado posterior debe prevalecer sobre este informe únicamente mediante evidencia fresca.
