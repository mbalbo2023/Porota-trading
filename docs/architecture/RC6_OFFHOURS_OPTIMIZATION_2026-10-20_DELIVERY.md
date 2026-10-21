# RC6 OFFHOURS — Implementación, pruebas y criterios de aceptación

**Estado:** PROPUESTO / EN_GITHUB DOCUMENTACIÓN; NO IMPLEMENTADO/NO DESPLEGADO. **Baseline:** productiva bc661130968f2b85b8b2146f76923ddd26eb7837, tree 1d7e9fad7e1af792f15a39a6cc68dcd2ecc8f33b. **Issues:** #679 y #624. **Referencia normativa:** ADR e INVENTORY adyacentes. PAPER/SHADOW ONLY, PPI Watch NO TOCAR, FIX-FORWARD.

## A. Objetivos y métricas de éxito

**P0:** no degradación del servicio crítico PAPER OPEN/CLOSED: exit clock, cauciones, Telegram, health, datos, catálogos, SHADOW y trazabilidad. Identidad PPI intacta; SHADOW no confunde heartbeat con freshness y mantiene el ceiling 120 s para observación útil OPEN; otros mercados usan su contrato específico.

**P1:** eliminar CPU e I/O inútiles: 30s full SHADOW nocturno sin evento, transacción vela cada 2s vacía, históricos sin gaps, sondas repetidas sin cambios. No se acepta "ahorro" si desaparecen velas, evidencia o se retrasa salida PAPER.

**P2:** simplificar scheduling/retención conservando garantías anteriores y pruebas negativas.

Métricas **obligatorias por fase y actor**: CPU_sec/h, wakes/h, commits vacíos/h, IO_read/write_bytes/h, p50/p95 wall/SQL lock wait, RSS/peak/PSS si disponibles, MemAvailable, PSI CPU/memory/io, memory/swap, SQLITE_BUSY/LOCKED, WAL/DB/file bytes, HEADROOM, fuentes PPI/IOL/BYMA/A3 request count, fills/exit intents PAPER, hashes, cursors, last_good generation, end-to-end alert transition. Timestamps fuente/evento/recepción UTC y sesión ART, cada medición con SHA. NO_VERIFICADO cuando falta serie histórica.

**Guardas absolutas:** real_orders_sent es int 0; rutas reales NOT_CALLED; sin mutar PPI Watch; sin fills PAPER fuera de sesión/quote/book/identidad válida; no cambio de riesgo/material financiero no revisado; no interpolar precios/volume; no bajar SLO de generación SHADOW ni retirar progreso de exit_supervisor.

**Objetivos de eficiencia PROPUESTOS:** 0 transacciones candle-worker vacías causadas sólo por despertar; 0 nuevos cut/last/current de SHADOW por passage of time CLOSED sin evento; 0 llamadas externas de histórico sin gap/revisión autorizada. Como metas relativas tentativas (NO medidas): >=80% reducción de wakes con TX vacía en CLOSED y >=50% reducción de CPU_sec nocturno de SHADOW cuando no hay inputs. Si no se cumplen se registra NO_GO de ahorro, sin sacrificar P0. Para OPEN comparación de distribución A/B misma carga/hardware y rechazo de degradación significativa más allá del ruido medido, sin fijar tolerancia imaginaria de p95.

## B. Gobierno y desarrollo multi-workstream

1. Leer HEAD productivo actual, issue #679/#624, ADR, owners #471/#473, open PRs/branches, hardware real y runbook. Todo SHA histórico es solo baseline.
2. READ_ONLY puede medir sin ownership. Para código/units tomar WRITE_OWNER **exclusivo por paths** con branch aislada, SHA base, contrato de scope en #471/#473. Para promover, tomar DEPLOY_OWNER global y mutex GitHub Actions exacto `rc6-unified-paper-deploy`, `cancel-in-progress=false`.
3. Integrar frentes en un único candidato después de CI y A/B; no merge/deploy por chat. No escribir branch productiva; no borrar ramas REVIEW/open heads o evidencia única.
4. No usar Codespaces; SSH/manual sólo break-glass. No nueva dependencia/agent/service/DB a priori, preferir instrumentos existentes. PPI Watch jamás forma parte del scope.
5. ERROR → RCA → FIX → GUARD → TEST/CI/PREFLIGHT → EVIDENCIA. Cualquier FAIL/ERROR/SKIP/XFAIL no gobernado bloquea.

## C. Paquetes paralelizables con scopes no solapados

### WS-OBS-01 — Línea base retrospectiva PASIVA

**Owner READ_ONLY.** Inventariar con GitHub Actions read-only de duración/capacidad acotada los 20 timers, external/residual/retired, GitHub Market Watch, procesos del observer, sqlite observer+WAL, cold market_history, dynamic-shadow/cold-audit, Docker, journals, backups. Análisis del 14 al 20/10 ART y nuevas muestras CLOSED/OPEN sin afectar rueda. Separar periodos 1vCPU/1GiB y 2vCPU/2GiB si corresponden; no atribuir bytes sin PID/cgroup; pérdidas de journal NO_VERIFICADO.

**Entrega:** lista por unidad (last trigger, next trigger, ExecStart, result/exit, wall/CPU/io/rss, fase y service SHA), por fuente (request/response, clave, bytes/versions/gaps), comparativa host actual/A-B. Clasificar DUPLICATE_CONFIRMED / COMPLEMENTARY / INTENTIONAL_REDUNDANCY / NOT_EXECUTED / NO_VERIFICADO. Sin quick_check global, sin VACUUM/checkpoint, sin nuevos providers requests ni disclosure secretos. El collector también debe demostrar bajo costo y cero side effects.

### WS-SHADOW-02 — IDLE_CLOSED seguro

**Scope propuesto:** rc6_shadow_runtime/worker.py, timing.py, persistence.py, cold_audit.py, consumers versioned, tests. Un único owner para persistence/cold_audit, que son zonas críticas del #624.

- Separar heartbeat operacional (SHADOW_IDLE_MARKET_CLOSED) del CURRENT financiero. NO renovar as_of, source_watermark, CURRENT o computed report si no cambió fuente/cursor/policy/session. El último cut sigue consultable como histórico, nunca "fresh OPEN".
- Calcular FULL sólo al entrar POSTCLOSE/PREOPEN, por nuevo watermark semántico PPI/IOL/BYMA/A3 verificado, configuración cambiada o OPEN, y por COLD pendiente previamente comprometido. Tiempo de wall por sí mismo no es evento de mercado.
- Reproducir COLD sin pérdida: pack original, hash/inode, manifest, queue/receipt, guard del dueño, FAILED se preservan; GC por reachability y cuota recursiva de cold-audit que cuente staging y bytes reales. No borrar snapshots fallidos sin RCA.
- Un heartbeat idle jamás satisface native 2 cuts/strict10 durante OPEN; preservar techo 120s y presupuestos por fuente; si PREOPEN no llega, nuevas aperturas BLOCKED, EXIT sigue activo.
- Rewarm por calendario auditado, sesión por instrumento, cutoff y hash, registrar `reason=DUE_*` y no reescribir PREOPEN inmutable erróneo.
- No resolverlo con sleep(300) global que deja cortes OPEN stale.

**A/B:** mismo fixture/SHA/hardware comparando outputs y hashes de dinero, risk, funnel, errors, warmup, source identities; footprint CPU/RSS/IO; errores simulados con report/pointer corruptos, checksum, clock skew, reboot, deadline, insufficient RAM/disk.

### WS-CANDLE-03 — Escritura sólo ante cambios

**Scope:** bl_candle_engine.py y tests, sin concurrencia sobre el archivo con otros. Fast path read-only de cursor y dirty_due; BEGIN IMMEDIATE sólo si snapshots nuevos, barra completada, revisión o heartbeat materialmente vencido. CLOSED solo drena backlog acotado y publica liveness barato bajo nuevo SLA justificado, evita 2s transactions vacías. En OPEN procesa como antes con ≤500 filas, cursor sin saltos y revision hash idempotente. Heartbeat puede mantenerse de baja frecuencia sin que la prueba de OPEN confunda idle con stuck.

Dos auditores RO se mantienen mientras sus invariantes no sean idénticos: rc6_candle_integrity.py valida semántica (SAMPLED≠FULL, sin volume/trades/vwap ficticios); rc6_candle_inmarket_evidence.py valida progreso/dirty/quick_check focalizado. Limitar “inmarket” a sesión, medir CPU/I/O de ambas, no eliminar una antes de preservar todos sus checks.

**Pruebas:** 0/1/100/500 nuevas muestras, current OPEN/CLOSED, revisiones tardías, duplicados, día feriado, reloj y DST invalid, restart cursor, dos writers competidores, backlog postclose, source type != TRADE, calidad y memoria; conteo WAL/tx before-after.

### WS-HISTORY-04 — Bootstrap único por clave; incrementales justificadas

**Scope:** bf_production_paper_observer.py historia, ew_a3_history_rc6.py, rc6_local_eod_history.py, History v2/cursors/attempt ledger si imprescindible, tests. Ownership separado de la integración de scanner y SCHED.

- Política DUE exacta por (símbolo,familia,mercado,moneda,plazo,sesión,source,quality) con cursor hash y estado BOOTSTRAPPED / GAP / CORRECTION / STALE_VERIFIED / BLOCKED; un fetch externo por ventana y necesidad comprobada. SOURCE FRESHNESS y calidad no derivan de received_at.
- PPI _daily_sync y _background_ingest utilizan mismo gate: no doble solicitud. Guard de cutoff repair completo (2026-09-21) y 512MiB migration conservado; bloqueos legacy no se maquillan GREEN. Si un provider no da FULL_OHLC, datos locales SAMPLED no sustituyen.
- A3 DAILY 18:30 sólo sesiones faltantes; RECONCILE 23:30 sólo correcciones posibles definidas y con presupuesto; WEEKEND_DEEP sólo gaps comprobados, no repetir backfill de 180d por cron vacío. Mantener opciones/futuros si no puede generarse su historia local.
- Local EOD lee vela SAMPLED 1m/5m por cursor y publica en cold; bloqueo ANY OPEN PAPER se debe resolver **probando aislamiento de la lectura del libro** (snapshot RO corto, guard de seguridad y recursos, no ampliar window trader). Si no se puede probar, dejar DEFERRED con causa y retry al cambio real de condición, nunca cada 5m condenado.
- Append-only history revisions sobre hash distinto; source precedence PPI identidad, IOL/BYMA/A3 complementarias; no borrado/vacío retroactivo. Importación inicial con manifiesto y calidad; historia propia no afirma full coverage de minutos no observados.

**Pruebas:** bootstrap verificado, gap exacto, intercalado A3/PPI, opciones no READY, currency mismatch, 10d correction, feriado, duplicate payload, auditoría report de payload changed, rate/timeout, legacy bloqueado sin descargas, no synthetic OHLC.

### WS-SCHED-05 — Calendar/resource admission con timers actuales

**Scope:** 20 .timer/.service bajo systemd/ops/systemd, ops/policy/host-control-plane-reconciliation-v2.json, tests; un solo owner y main/productiva intactos hasta PR.

- Preservar safety/exit/Telegram, health fast; ajustar export scheduler CLOSED a frecuencia medidamente suficiente; no prometer “timer disabled” como servicio muerto.
- OnCalendar y gate in-service para todas operaciones pesadas; verificar ART+feriados+excepciones+contrato mercado, y OnBoot/OnUnitActiveSec que pueden volver a despertar en CLOSED. Evitar catch-up de backup Persistent=true en OPEN sin perder backup: diferir con recibo.
- `market-watch-silence` y GitHub schedule son redundancia intencional: misma incident key, no silenciar caída real del cron. IOL OAuth evitar MCP calls cuando LIVE_FRESH; PPI Budget de scanner permanece primario.
- Introspección open_over_max_hold separar salida pendiente EOD mercado cerrado del breach OPEN, escalamiento y dedupe, sin ocultar posición; no cambiar MAX_HOLD.
- Mantener retired GDELT/functional-health, quarantined history-postclose, external cockpit/approval/proxy y PPI Watch protegidos.
- Asegurar full_db_quick_check y backup COLD coordinados: no correr uno mientras EOD o shadow COLD ocupa disco/SQL, salvo prioridad de seguridad.

**Pruebas:** systemd-analyze calendar, reboot / 09:00 / 11:00 / 17:00 / holiday / cross-midnight, 20 file SHAs, manifest matches, exclusividad, missed timers, external untouched. Ningún cron automático habilita orden real.

### WS-SRE-06 — Resource envelope/retención

**Scope:** métricas existentes, retención shadow (coordinar con WS-SHADOW-02), DB guard, tests; no crear daemon nuevo.

- Medir por cgroup2 CPU/IO/RSS, PSI; sólo luego considerar CPUWeight/IOWeight/MemoryHigh de servicios fríos, nunca MemoryMax arbitrario que cause OOM. Prioridad exit_clock/reader.
- Medir sqlite WAL y attr source DB RO; NO PRAGMA wal_checkpoint como sonda de solo lectura; FULL/RESTART/TRUNCATE y VACUUM requieren ventana COLD, own lock, FS headroom y backup probado.
- GC reachability por CURRENT/cuts/computed references/queue/receipts/backup/inode/hash y preservación FAILED; cuota recursiva real cold-audit, symlink hardlink guard, no rm masivo. Evitar crecimiento invisible de preopen-*.gz con retención solamente tras archivado/restauración verificada.
- Configurar límites de carga/errores de auditoría; deploy plan actual 2GiB reserva y COLD memoria 128MiB mínimo, ambos son pisos no objetivos de uso.

**Pruebas:** EXHAUSTED_DISK / LOW_MEM / inode pressure / old failed receipt / multiple queues / temp file orphan / absence cgroup2, latency OPEN con shadow/publisher saturación, preservar 3 contenedores y todo su ledger.

### WS-INTEG-07 — Integración única

Reconciliar con HEAD productivo vigente, ramas PR abiertas y ownership. Mismo artifact congelado test/deploy. Si cualquier ruta compartida recibe dos autores, bloqueo hasta adjudicar owner. No se valida compilación aislada como funcionamiento runtime.

## D. Matriz de pruebas obligatorias

| Test | Condición | GREEN requerido |
|---|---|---|
| T01 | CLOSED sin nueva fuente durante 12h | SHADOW idle sin CUT nuevo/freshness sintética; security heartbeat intacto |
| T02 | 2 spots EOD OPEN con mercado cerrado | exit intent persiste, reloj vivo, sin fill ficticio |
| T03 | OPEN siguiente y BOOK verificado | exit paper seguro; native shadow 2+strict10 y ceiling 120s |
| T04 | heartbeat idle perturbado | jamás READINESS/SHADOW_FRESH en OPEN |
| T05 | cold pack pending/FAILED/reboot/cancel | replay exacto o fail-closed; evidencias originales preservadas |
| T06 | crash entre manifest/receipt/CURRENT | no status verde falso, no doble commit |
| T07 | 30min candle sin market_snapshots | 0 candles, 0 TX vacía, heartbeat al contrato |
| T08 | late candle corrections/hash igual/diferente | 0 duplicates, revisiones reales sólo nuevas |
| T09 | SAMPLED sin OHLCV full | no claim FULL, no synthetic volume/trades/vwap |
| T10 | 3 históricas DUE a la vez | 1 writer/identidad, 1 provider request cuando realmente DUE |
| T11 | bootstrap histórico completo sin gaps | 0 descargas recurrentes, cobertura igual |
| T12 | A3 verdadero correction últimos 10d | sólo nueva revisión, identidad PPI intacta |
| T13 | fuente >512MiB legacy/cutoff no completo | BLOQUEADO sin migración parcial/tormenta |
| T14 | EOD local con spots OPEN y 1 caución OPEN | lee aislado de ledger o DEFERRED exacto |
| T15 | reboot 11:00, Persistent timer pendiente | backup/hist no invade OPEN |
| T16 | feriado, A3 ventana distinta, reloj inválido | fail-closed por instrumento, sin apertura sintética |
| T17 | 20 timers + residual + critical | exact policy; unknown preserved, PPI Watch intacto |
| T18 | competing SHADOW/COLD/SQLite vs exits | p95 critical no peor que baseline (error medición) |
| T19 | disk/memory bajo piso | COLD/admisión bloqueada; seguridad viva, alerta útil |
| T20 | GitHub Market Watch silencioso | host fallback valida schedule y dedupe Telegram |
| T21 | real_orders !=0 / fake quote / ambig identity | rutas NOT_CALLED y PAPER no simula |
| T22 | 405 minutos virtuales **en Droplet** postdespliegue | estrés por ventana admitida+restauración GREEN |
| T23 | 24h CLOSED A/B mismo fixture | ahorro CPU/IO y ninguna evidencia nueva perdida |
| T24 | faltan journald previos | NO_VERIFICADO, sin relleno inferido |
| T25 | image/artifact/runtime source mismatch | Deploy BLOQUEADO |

**T22 no se reemplaza por CI simulado**; prueba 405min del host quedó explícitamente DEFERRED en #624. No correrla durante rueda ni cuando RAM/disco insuficiente. Si continúa DEFERRED, NO cerrar misión integral como GREEN.

## E. Secuencia de certificación

**S0. READ_ONLY baseline** por GitHub Actions low impact. Si es OPEN sólo consultas pasivas cortas; conocer todos los writer/locks y estado EOD/caución; salida NO_VERIFICADO si hay métricas rotadas.

**S1. Código + tests deterministas** en ramas con owners por scope; tests unit/integration incluyendo fall-before negativo + guard permanente. Cero FAIL/ERROR/SKIP/XFAIL salvo exclusión gobernada y documentada.

**S2. A/B offhost** misma base/fixture, code SHA, carga, hardware y fuente nativa, comparando resultados financieros exactos, CPU/IO/WAL/latency, negative cases y seguridad. No usar provider live en stress.

**S3. Predeploy V2 único candidato** HEAD/trees/manifest, artifact inmutable, build once, test suite completa y native sandboxes. Validar SHA/digest, fuente/imagen/versiones y espacio.

**S4. Deploy V2 normal** SOLO DEPLOY_OWNER global legítimo, mutex rc6-unified-paper-deploy cancel-in-progress=false, preflight PAPER+data/space/host, no rebuild en Droplet, promotion del mismo artifact. Si falla, RCA/fix-forward nuevo SHA, no rollback/same-SHA retry sin justificación.

**S5. Runtime directo e independiente** observer/dashboard/critical running, image ID exacto, 0 OOM/restarts, 20 timers control plane, exits/cauciones preservados, 2+strict10 SHADOW, FIFO candles, WAL/headroom, real_orders integer0, PPI Watch no mutation, journal/results/digests.

**S6. Primera rueda OPEN genuina:** sources/BOOK por instrumento, 2 posiciones pending si aún vigentes, caución vencimiento, IOL budget, decisiones y velas, LATENCY p95, alert delivery, sin provocar operación real ni fills ficticios.

**S7. 405min host stress** sólo ventana postclose autorizada y recursos admitted; funcional/full system, restauración y ledger digest. No test invasivo durante rueda.

**S8. Handoff/cierre** registrar PR/SHA/tree/Predeploy run/artifact digest/Deploy run/independent audit, resultados y NO_GO, actualizar CURRENT_STATE/ACTIVE_PENDING/ERROR_REGISTRY/LESSONS con owner, liberar WRITE/DEPLOY_OWNER. Nunca cerrar por success de Actions sin runtime real.

## F. Gates GO/NO_GO

- **GO A DESARROLLO:** WS-OBS o baseline explícitamente incompleto pero falla medida, ownership correcto y tests pactados.
- **GO A PREDEPLOY:** changes de todos workstreams integrados a único candidato; auditoría A/B y zero regressions P0.
- **GO A DEPLOY:** artifact certificado/host metrics/resources, owner exclusivo, no cruce rueda o caución, disk floor dinámico, seguridad PAPER.
- **GO A VALIDATED_RUNTIME:** SHA/tree+image+strict native+20 timers+ledger+security+health host con hashes y timestamps directos.
- **GO RODA PAPER** por instrumento: aparte, durante próximo OPEN fuente/bid/depth/freshness, exit intents y cauciones/heartbeats. Nunca equivale a todas familias READY.
- **NO_GO:** falta evidencia del host real, benchmark degradado, locks OOM, fuentes/identidad ambiguas, PPI Watch tocado, 405 host exigido no ejecutado para cierre integral, archivos FAILED eliminados, efectos SQL inesperados, timers residuales destruidos, PAper orders !=0.

## G. Prohibiciones

No apagar entire observer ni supervisor, deshabilitar outbox, borrar DB/históricos/cuts FAILED, convertir SAMPLED a OHLCV FULL, revivir GDELT/legacy, tocar PPI Watch, editar productiva a mano, SSH normal o abrir real trades. No introducir Redis/Kafka/K8s/segundo broker o un nuevo agente sólo para ahorrar CPU en un único Droplet.

**Referencias:** ADR e INVENTORY adyacentes, issue #679, issue #624, owners #471/#473, Github Runs 39516481351/39519161504/39521189732, documentación systemd.timer / SQLite WAL / Linux PSI / systemd.resource-control. Toda tarea marcada con condición de implementación posterior.
