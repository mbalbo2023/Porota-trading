# RC6 OFFHOURS — Inventario canónico, consumo y duplicidad

**Fecha:** 20/10/2026 ART; **estado:** ANÁLISIS DE CÓDIGO / RUNTIME RETROSPECTIVO NO_VERIFICADO. Fuente: SHA productivo bc661130968f2b85b8b2146f76923ddd26eb7837. Documento de apoyo a ADR RC6-OFFHOURS-01. **No es orden para desactivar unidades.**

## 1. Política host: 20 timers ACTIVE_TIMER

Extracción de ops/policy/host-control-plane-reconciliation-v2.json y unidades .timer bajo systemd/ y ops/systemd/. Horarios en ART cuando especificados; en los timer OnUnitActiveSec los instantes son relativos al servicio, no al reloj. En todos los casos se debe cotejar con systemctl show/last-trigger/next-elapse/journal del host; **no afirmar que se ejecutaron**.

| # | Timer canónico | Programación | Rol/decisión PRELIMINAR | Investigación prioritaria |
|---|---|---|---|---|
| 1 | porota-runtime-evidence-rc6 | cada hora :50, Persistent=true | Observabilidad 24x7; conservar | tamaño JSON, git publication cost, duplicación introspection |
| 2 | porota-a3-history-daily-rc6 | L-V 18:30, Persistent=true | incremental derivativos fuera rueda | API requests útiles, datagaps, rate y duración |
| 3 | porota-a3-history-reconcile-rc6 | L-V 23:30, Persistent=true | corrección retrospectiva de 10 días | redundancia de lecturas y costos, hash/diff |
| 4 | porota-a3-history-weekend-rc6 | Sáb-Dom 03:00, Persistent=true | catch-up 180 días si falta cursor | only gap-ledger, budget, weekends/holiday safety |
| 5 | porota-candle-integrity-rc6 | +5min desde boot, cada 10min | RO semántica de velas, 5000 versiones | evitar repetición si cursor/hash sin cambio |
| 6 | porota-fast-functional-health-rc6 | +3min boot, cada 5min | RO liviano, conservar 24x7 | prueba no full quick_check ni writes |
| 7 | porota-market-watch-silence-rc6 | L-V cada 10min, all day | fallback de GitHub watch; guard interno 11:02–17:15 rueda normal | evitar red fuera ventana; dedupe de alertas |
| 8 | porota-full-db-integrity-rc6 | L-V 17:20 + jitter <=120s | PRAGMA quick_check global RO, intenso | no coincidir con COLD/EOD/backup, costo real |
| 9 | porota-host-general-backup-rc6 | diario 04:15 + jitter <=10min, Persistent=true | backup vital off-hours | storage admission, catch-up en OPEN, último backup válido |
|10 | porota-preopen-rc6 | L-V 09:45 y 10:20 | gates fail-closed | no ampliar a otros mercados sin contrato |
|11 | porota-introspection-publish-rc6 | cada hora :42, Persistent=true | publica introspección | duplicados GitHub, desacoplar alarm storms |
|12 | porota-introspection-rc6 | cada hora :35, Persistent=true | observa DB/trading, alertas | open_over_max_hold falsos CRITICAL en CLOSED |
|13 | porota-iol-shadow-collector-rc6 | L-V cada min 10:30-16:59 | IOL OBSERVE_ONLY+BYMA+universo | costo requests, flock, rate 40/min, cold writes |
|14 | porota-rc6-candle-inmarket-verify | boot +3min y cada 10min | RO progreso/integridad focalizada | horario declarado 24x7 contradice “inmarket” |
|15 | porota-scheduler-export-rc6 | boot +2min y cada 1min | export systemctl para dashboard | >1440 activaciones nominales/día (timer 1min), muy frecuente CLOSED |
|16 | porota-snapshot-postclose-rc6 | L-V 17:08 | snapshot/review/evidence | cron de día feriado y timeouts |
|17 | porota-snapshot-preopen-rc6 | L-V 10:10 | snapshot preopen | superposición con 09:45/10:20 |
|18 | porota-byma-morning-watch-rc6 | L-V 08:30, Persistent=true | BYMA public scrape y authority watch | no ejecutar tarde por catch-up |
|19 | porota-iol-oauth-health-rc6 | 30min OnUnitActiveSec + L-V 09:30/10:20 | guardian OAuth, defer LIVE_FRESH | hacer una sola comprobación real/ventana |
|20 | porota-local-eod-history-rc6 | L-V 17:15/5min y 18..22:00/5min | muestreo 1m/5m a frío, hasta 69 veces por día | OPEN_POSITIONS bloquea con 2 EOD pendientes; errores repetidos |

**Tres timers de código fuera de ACTIVE:** porota-functional-health-rc6.timer RETIRED, porota-gdelt-event-risk-rc6.timer RETIRED, porota-history-postclose-rc6.timer QUARANTINED/MASKED. No reactivar GDELT ni el legacy backfill. El JSON host también declara unidades externas (approval/proxy/cockpit privado) y residuales NO_VERIFICADO; preservar hasta dueño/identidad demostrados. PPI Watch EXTERNAL_SEPARATE_OWNER / NO TOCAR.

## 2. Procesos del observador vs unidades systemd

### Persistentes dentro del contenedor

| Proceso/hijo | Escrituras/lecturas en código | Estado OPEN/CLOSED esperado | Identidad funcional / RCA |
|---|---|---|---|
| exit_clock + PositionExitSupervisor | estado posiciones/exit intents/heartbeat y valoración, reloj 5s | SIEMPRE ON; book y fill sólo con sesión/quote válidos | prioridad absoluta; no acoplar a SHADOW |
| exit_reader | PPI book READ_ONLY, prioridad EXIT_CRITICAL | OPEN; WAITING_MARKET en CLOSED (5s local heartbeat) | no duplicar scanner; verificar API budget |
| scanner bf_production_paper_observer | health, historial/catálogo por TTL, data OPEN | health 5s; estrategia sólo OPEN; catálogo/historia en CLOSED por gates | separar valor y costo histórico |
| dynamic_shadow | lee SQLite RO y escribe evidencia comprimida, CURRENT y COLD | FULL OPEN; propuesta IDLE CLOSED | no versionar nuevos cortes sólo por reloj |
| candle_worker | writer de candle_samples / candle_versions / candle_worker_state | OPEN + drain postclose; idle heartbeat CLOSED | BEGIN IMMEDIATE + UPDATE 2s incluso sin filas |
| notification_worker | outbox Telegram | 24x7, backoff, dedupe | conservar; alertas off-hours útiles |
| performance_worker | métricas propias | guard conservador | evaluar si datos inexistentes ameritan writes |
| intraday_scalping | scanea sólo con ventana BYMA y modo activo | WAITING_MARKET CLOSED | examinar rate, cadence, shadow-only |
| caucion_cash_sweep | lee términos y libros por ventana; estado cada >=30s | WAITING_WINDOW CLOSED; operativa fin de rueda | cadencia idle optimizable, maduración es deber distinto |

El contenedor crítico de aprobación y el dashboard son procesos independientes, no duplicados del observer. docker-compose.yml de bot monolítico es modo compatibilidad/sandbox, NO fuente operativa del PRODUCTIVE split. El APScheduler az_maintenance_scheduler.py entra por entrypoint.py legacy; NO suponer que ejecuta en el runtime actual sin prueba de PID/cmdline/cgroup.

### Caso vela: aclaración explícita

- Único generador LOCAL: SampleMaterializer con market_snapshots TRADE, FIFO cursor, inserta samples+1m/5m sampled. **NO pide velas al proveedor**.
- Auditor 1: rc6_candle_integrity.py confirma que SAMPLED no reclama COMPLETE, VWAP, trades ni volumen.
- Auditor 2: rc6_candle_inmarket_evidence.py verifica progreso cursor/dirty_due, health, scoped btree quick_check; emite JSON.
- **Overlap parcial de RO:** estado del candle_worker y tablas de velas, NO doble ingesta confirmada.
- **Riesgo de mantenimiento:** actualización del heartbeat candle en la misma transacción SQLite cada 2 segundos aun cuando no hay snapshots; carga potencial sin barra nueva. Teoría ~1.800 vueltas/h (no es medición host).

## 3. Histórico: cinco circuitos y una fuente de confusión

| Actor | Fuente / destino | Condición actual de código | Problema potencial |
|---|---|---|---|
| scanner PPI _daily_sync | PPI catalog+hist / History v2 y source_sync | daily after cutoff; historia si cutoff-repair 2026-09-21 COMPLETE, presupuesto copy <=512 MiB y una fecha DUE | contiene lógica de reconsulta aunque bootstrap ya esté |
| scanner PPI _background_ingest | PPI history / History v2 y health | sesión hábil post17h, máximo intento/día; compartir _download_histories | verificar si _daily_sync ya consumió cuota; evitar redundant fetch |
| A3 DAILY_INCREMENTAL | A3 CEM / History v2 | 18:30; fecha última +1 o fallback 7 días | necesario sólo si falta nueva sesión A3 |
| A3 RECONCILE | A3 CEM / History v2 | 23:30; reconsulta últimos 10 días | detectar corrección verdadera y costo |
| A3 WEEKEND_DEEP | A3 CEM / History v2 | sáb/dom 03:00; hasta 180 días por gaps/cursor | potencial backfill reiterado sin razón |
| EOD LOCAL | source candle_versions / cold market_history.db | 17:15-22:55, cursor con batch <=500, bloquea si ANY OPEN PAPER | bloqueo por carry legítimo, exceso de reintentos |
| LEGACY postclose History v2 | legacy script/timer | QUARANTINED_TIMER, no ACTIVE | jamás reactivar en cambio de horario |
| PPI Watch externo | fuera de alcance | OWNED_EXTERNALLY / NO_VERIFICADO | no enumerar/cambiar configuración |

**Clave:** History v2 almacena source/version/price basis y potencialmente revisiones históricas; version append se deduplica por payload, pero la descarga externa puede seguir costando CPU/red aun con 0 nuevas versiones. El histórico propio sampled ofrece **cobertura observada**, no fuente de OHLCV full para instrumentos/horarios que no se observaron. No borrar historia PPI/A3 existente al pasar a local; preservar material fuente.

## 4. Qué hay que medir y por qué no se puede deducir aún

### Ventana temporal obligatoria

14–20/10/2026 ART, ampliable a 7 días recientes en la medida en que journal/Actions/artifacts lo permitan, y 1 ciclo completo OPEN después del deploy. Separar periodos 1vCPU/1GiB vs 2vCPU/2GiB por hardware; medir antes/después en la misma clase de host y con misma carga. Identificar runs de deploy y stop para excluir de distribución normal.

### Instrumentación de diagnóstico READ_ONLY

1. systemctl list-timers --all, list-unit-files, show ActiveState/SubState/UnitFileState/LastTriggerUSec/NextElapseUSecRealtime/Result/ExecMainStatus y journalctl --since/--until por los 20 + residuales EXTERNAL. Añadir unit loaded SHA y runtime ExecStart a cada fila; si no hay registros antiguos, NO_VERIFICADO.
2. Procesos realmente ejecutados: PID/cgroup/cmdline saneado, procs/vmRSS/rss_peak, cpu.stat, memory.current/peak/events, io.stat, /proc/[pid]/io y /proc/pressure/{cpu,io,memory}, counters como diferenciales con tiempo wall verificado. **Nunca iterar /proc de forma masiva** y nunca leer secretos/env ni stacks de terceros.
3. SQLite observer_v17.db + WAL/SHM y cold market_history.db: stat sizes/mtime, page_count, page_size, freelist_count mediante consultas RO cortas; COUNT por tablas SÓLO si índice/planner/budget seguro y costo medido; table snapshots/logs históricos. Nunca PRAGMA wal_checkpoint como “lectura” ni VACUUM para medir.
4. Directorios exactos: dynamic-shadow/latest, checkpoint, cut-*, computed-*, preopen-*, cold-audit manifests/packs/receipts FAILED, backups, logs/journal y Docker overlay. Registrar tamaños REALES según stat y cuotas recursivas; comparar dos cortes y hash fuente para nuevas versiones de valor, descartes y escrituras vacías.
5. Red por consumidor: auditoría ya disponible de PPI HTTP, IOL MCP y scraping BYMA/A3; no hacer nuevas requests como sonda. Distinguir autenticar vs current/book/history, identidad exacta, retries, cuota y éxito/fallo.
6. Exportar JSON mínimo temporal con hashes SHA/tree del código y horas, clocks normalizados UTC/ART y política en vigor. No persistir inventarios con secretos ni datos de usuario; usar GitHub Actions read-only con límites de 60s, CPU/mem/disk guards y control de no modificación antes/después. No ejecutar test de estrés para “medir”.
7. Construir costos por fase y actor, incluyendo CPU seconds por wakeup, row_changes/new_versions/rejections, median/p95, lock-wait, read/write bytes, WAL tail size, temp files y reclaimed_bytes. Si no existe historia per-PID, escribir NO_VERIFICADO; no “retroingeniería” de métricas inventadas a partir de un df final.

### Base para comparar

La última auditoría 39521189732 (2026-10-20 21:25 ART) muestra:
- tres contenedores ejecutándose, sin OOM/restarts; SHADOW_OBSERVING con provider_requests=0 en MARKET_CLOSED;
- veinte ACTIVE_TIMER enabled+active, pero SIN último Result/duración de cada timer en su resumen;
- libre 4.942.311.424 bytes, MemAvailable 1.017.540.608 bytes, swap 70.144.000 bytes;
- dos spot OPEN EXIT_PENDING_MARKET_CLOSED/EOD_PAPER, una caución OPEN;
- 405 minutos de estrés host diferidos, DB full integrity diferido, PPI Watch externo NO_VERIFICADO.

**No derivar de estos valores una curva de crecimiento ni saturación causada por SHADOW/velas.**

## 5. Matriz de duplicación a entregar

Cada fila debe contener **exact consumer, producer, fase, trigger, fuente, identidad/cursor, tabla/archivo, mode R/W, rate real, output/quality, idempotency key, locks, costo, veredicto**. Veredictos sólo:
- DUPLICATE_CONFIRMED: trabajo idéntico y escrituras/requests observadas con misma causa/clave sin exclusión.
- REDUNDANCY_INTENTIONAL: funciones distintas o un respaldo antifalla, contrato explícito.
- COMPLEMENTARY: semántica diferente o calidad/cobertura distinta.
- NOT_EXECUTED: habilitada pero el guard rechazó sin side effect, probado por logs.
- NO_VERIFICADO: faltan trazas/resultados suficientes.

Focos: candle audit x2; A3 daily/reconcile/weekend; scanner PPI daily/background; local EOD vs supplier History v2; GitHub Market Watch + host watchdog; introspection/publish/runtime-evidence; scheduler-export; backups host/legacy; preopen/snapshot; DB integrity global/focalizado.

**Antipatrones prohibidos:** decidir “duplicado” sólo por nombre de unit, matar watchdog de seguridad para bajar CPU, eliminar evidencia FAILED sin manifest/receipt, atribuir tamaño de imagen Docker a sumas directas de capas solapadas.

## 6. Decisiones para aceptación

- Aceptar 24x7 sólo seguridad, heartbeat de baja huella y tareas offline **admitidas** por valor, horario y presupuesto medidos.
- Cualquier publisher que repita cálculo con la misma fuente/cursor/metadata debe registrar reason=NO_NEW_EVIDENCE y evitar actividad costosa, sin reemplazar as_of de mercados.
- Cambios de cadencia en OPEN requieren A/B/strict native GREEN, no promediar errores a cero.
- Verificar compatibilidad postclose de posiciones abiertas sin permitir fills ficticios ni bloquear EOD local indefinidamente.
- Antes de cerrar, enseñar matriz con **qué fue eliminado, qué fue conservado y por qué** y pruebas contra la semana real.

**Referencias:** source exacto SHA y issue #679; docs ADR + DELIVERY + HANDOFF adyacentes.