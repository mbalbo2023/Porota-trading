# ADR RC6-OFFHOURS-01 — Arquitectura eficiente de POROTA PAPER/SHADOW

**Fecha:** 20/10/2026 ART. **Estado:** PROPUESTO / EN_GITHUB (sólo documentación; NO IMPLEMENTADO). **Owner de arquitectura:** WS-RC6-OFFHOURS-ADR-20261020, documentos solamente. **Issue de seguimiento:** #679; relación #624. **Baseline del examen:** rama deploy/rc6-pr69-isolated-20260915 @ bc661130968f2b85b8b2146f76923ddd26eb7837, tree 1d7e9fad7e1af792f15a39a6cc68dcd2ecc8f33b. **No confundir una propuesta con runtime desplegado.**

## Decisión ejecutiva

Proponer una arquitectura de **trabajo útil condicionado por fase, evidencia nueva y presupuesto de recursos**. Mantener **vivo siempre** el plano de seguridad (exit clock/supervisor, notificaciones, maduración de cauciones, heartbeat mínimo, diagnóstico), pero **no mantener computación SHADOW completa ni escáneres históricos sin razón durante CLOSED**. Separar explícitamente la *vida del proceso*, la *frescura de la fuente*, la *frescura de una generación analítica* y la *admisión PAPER por instrumento*. Aprovechar los contenedores, procesos y timers existentes; **no introducir nuevos daemons, agentes, coordinadores externos, colas de terceros, Redis, Kafka ni otra base principal**.

**La decisión de implementación sigue condicionada a inventario real y benchmarks A/B.** No se establece que los timers o procesos actuales estén defectuosos sólo por estar habilitados; sí hay hallazgos verificables de código (ver inventario complementario). Se requiere un único candidato integrado y Deploy V2 fix-forward.

## 0. Restricciones no negociables

- Modo **PRODUCTION_PAPER / SIMULATED**. real_orders_sent **entero 0**, real routes NOT_CALLED/BLOCKED; nunca se habilitará operatoria real.
- PPI Watch **EXTERNAL_SEPARATE_OWNER / NO TOCAR**. No buscar atajos para modificarlo ni atribuirle actividad por inferencia.
- No rollback, no deploy por chat. Branches+PR, ownership #471/#473, READ_ONLY durante trabajo ajeno, Deploy V2 build once/artefacto congelado, mismo artefacto testeado/desplegado, mutex rc6-unified-paper-deploy con cancel-in-progress=false.
- No modificar identidad primaria PPI con datos IOL/BYMA/A3, ni llenar gaps con interpolación/síntesis que reclame OHLCV verdadero. Opciones/futuros/cauciones tienen contratos y ventanas específicas, fail-closed por identidad.
- El reloj de salidas EOD/MAX_HOLD/SL/TP y maduración de cauciones no dependen de disponibilidad o cadencia SHADOW; preservan propiedad de avance y salida segura con BOOK real fresco para llenar PAPER; nunca inventar fills.
- Si la fase/calendario/datos/clock/regla no se pueden verificar: **FAIL CLOSED para trading**, y mantener observabilidad. No sustituir la falta de prueba con “success” de Actions.

## 1. Línea base de hechos, límites y preguntas abiertas

**Hecho GitHub:** 20 ACTIVE_TIMER en ops/policy/host-control-plane-reconciliation-v2.json; otros TIMER retirados/cuarentenados y unidades EXTERNAL/RESIDUAL fuera del control del manifiesto. systemd ACTIVE sobre el timer significa reloj armado, no consumo permanente del servicio. **Hecho código:** se ejecutan procesos persistentes dentro del observer (bv_paper_runtime.py, scanner, exit_reader, exit_supervisor clock, notifications, candles, dynamic_shadow, performance; scalping/caución condicionales). **Hecho código:** SHADOW usa SQLite de entrada en modo RO y evidencia persistente en archivos; el materializador de velas escribe el estado en SQLite incluso sin nuevas barras. **Hecho runtime de auditoría 39521189732:** tres contenedores sanos, observer WAITING_MARKET / MARKET_CLOSED, 20 timers restaurados, 2 posiciones PAPER spot OPEN con EXIT_PENDING_MARKET_CLOSED/EOD_PAPER, 1 caución abierta, seguridad PAPER/0, libre 4.942.311.424 bytes, MemAvailable 1.017.540.608 bytes, SHADOW fresco con provider_requests=0. **Hecho pipeline:** #39516481351 predeploy SUCCESS, #39519161504 Deploy V2 SUCCESS, #39521189732 inventario independiente SUCCESS; **405 minutos de estrés íntegro en el host, restauración postestrés, crecimiento SQL/WAL por proceso, off-host DR y operación del próximo OPEN: NO_VERIFICADO**.

**No inferir:** tasa actual de fsync, si existen cron residuales activos, bytes exactos de shadow/cold-audit, duración real de cada timer, errores nocturnos, total de PPI/A3 exitosos ni duplicación real sin logs nativos. La documentación de Proyecto contiene baselines anteriores y NO sustituye el SHA/runtime actual.

## 2. Modelo de dominios: cinco planos, un dueño de la verdad por dato

1. **Plano 0: SAFETY ALWAYS ON.** Reloj y supervisor de salidas/posición PAPER, supervisión de futuros/cauciones contractual, notificaciones y Telegram outbox, salud mínima y watchdog de procesos. Alta prioridad relativa. No necesita trabajo bursátil de SHADOW. Puede crear estados/heartbeats mínimos sin consultar proveedores fuera de sesión.
2. **Plano 1: LIVE MARKET.** Scanner PPI READ_ONLY, lector de BOOK de posiciones abiertas, señales, riesgo, scalping si autorizados, IOL SHADOW complementario y contratos caución sólo en su sesión **por instrumento**. Consume cuotas de CPU/IO y proveedor cuando OPEN. Datos con source_at/event_at separados de captured_at/received_at.
3. **Plano 2: SHADOW ANALYTICS.** Preopen congelado verificable, corte analítico de sesión, funciones de capacidad/riesgo/economía y auditoría COLD. Hace computación **cuando hay nueva evidencia semántica, un cambio de política o una transición de fase**, no por simple wall clock en CLOSED. El procesador puede residir pero permanecer quieto.
4. **Plano 3: HISTORY & COLD MAINTENANCE.** Bootstrap, backfill por gaps reales, versiones de velas locales de calidad SAMPLED, A3/PPI sólo con faltantes o correcciones demostrables, integridad global, backup/restauración. Se ejecuta fuera de la ventana de decisión y con recursos admitidos. Una misma fuente/identidad/ventana tiene un único dueño de escritura.
5. **Plano 4: OBSERVABILITY/CONTROL.** GitHub Actions y evidencias host pasivas; heartbeat, timers, métricas de CPU/IO/RAM/PSI, memoria por proceso, WAL y archivos por prefijo; health semanticamente preciso. No instalar instrumentación permanente que consuma más de lo que ahorra.

**No mezclar “control” con “fuente”:** PPI Watch sigue externo y sin administrar desde el control plane RC6. GitHub Market Watch y fallback host pueden ser redundancia deliberada, pero deben registrar la misma transición deduplicada y no realizar escrituras simultáneas sobre los mismos archivos.

## 3. Máquina de estados de planificación y prioridad

FASES: HOLIDAY/WEEKEND, CLOSED, POSTCLOSE_DRAIN, PREOPEN_PREPARE, PREOPEN_GATE, OPEN, OPEN_DEGRADED, MAINTENANCE_DEFERRED.

Las fases se derivan de **ak_byma_calendar + co_market_sessions_hf6**, timestamps ART y contratos por mercado/instrumento. Un horario systemd es un disparador; **cada función debe revalidar fase, condición de mercado y seguridad al entrar y justo antes de cualquier commit**. RECONCILE sólo ante respaldo acreditado; nada de presumir que todos los A3 siguen BYMA 10:30-17:00.

| Fase | Obligatorio | Condicionado / diferido | No debe suceder |
| --- | --- | --- | --- |
| WEEKEND/HOLIDAY | seguridad/heartbeat, alertas esenciales, caución contractualmente debida | backup, backfill validado y frio con cupo | PPI BOOK/current, entradas PAPER, escaneo SHADOW analítico cíclico |
| CLOSED nocturno | supervisión de exits, notificadores, maduración, lectura de comandos acotada | finalización de COLD pendiente, historia de gaps, copia y checkpoint gobernados | recalcular FULL SHADOW cada 30 s sin evento, candlestick transaction cada 2 s vacía |
| POSTCLOSE_DRAIN | snapshot EOD, llevar cursors hasta la última muestra conocida, conciliar exits pendientes sin fills falsos | integridad/archivo frío y luego backups, por turnos | full quick_check simultáneo a escribir EOD o COLD |
| PREOPEN_PREPARE | reconcile gaps mínimos, bootstrap del corte congelado necesario, reloj/identidades | refresh fuentes permitidas, warmup escalonado | promociones READY sin fuente/ejecución fresca |
| PREOPEN_GATE | evidencia de capacidad/frescura, identidad, PAPER/0 | compute/cold si termina dentro de deadline y margen | habilitar entradas sin green exacto |
| OPEN | scanner/live, exits prioritarios, publicación SHADOW con SLO de sesión, observabilidad ligera | data ingest de cuota probada; fuera si compete | backup, migración, quick_check global, sweeping, archivos fríos intensivos |
| OPEN_DEGRADED | salir/observar con datos autorizados; nuevas entradas fail-closed | reportes de diagnóstico baratos | freshness sintética, bucles de recuperación o retry storm |

**Orden de prioridad:** safety exit/book > scanner crítico/PPI budget > heartbeats obligatorios > SHADOW activo > ingest complementario > histórico/backup > auditoría pesada. La prioridad real se valida por CPU/IO y latency; **nice(10) no garantiza aislamiento** ni sustituye pruebas de cgroup v2.

## 4. SHADOW: propuesta de cálculo basado en eventos y snapshots

### Problema confirmado

rc6_shadow_runtime/worker.py ejecuta run_worker/tick de forma recurrente con POLL_SECONDS=30, incluso en CLOSED. Cada tick puede leer catálogo/SQLite, crear una foto de entrada COLD, computar analítica, escribir latest/checkpoint/CURRENT/cut/auditoría y fsync. El hecho de que provider_requests=0 fuera de rueda NO implica cero CPU, IO o escritura.

### Contrato nuevo propuesto (ADR pendiente de implementación)

- **OFFHOURS_IDLE**: inmediatamente tras publicar el último corte completo y drenar COLD **ya admitido**, registrar un estado operacional liviano “SHADOW_IDLE_MARKET_CLOSED”, heartbeat de proceso y referencias inmutables al último corte. **NO publicar un CURRENT nuevo ni renovar source_watermark/as_of como si hubiera nuevas cotizaciones**. El último corte de mercado continúa disponible como evidencia histórica, pero se expone con fase CLOSED y freshness de fuente según su timestamp real.
- **Reactivación sólo por causas materiales**: aparición de nuevas evidencias con identidad/sello nativo y hash comprobados, transición PREOPEN o POSTCLOSE, cambio aprobado de configuración/capacidad, necesidad de replay de COLD pendiente, o mandato explícito en entorno aislado. Repetición de un evento ya consumido no dispara cálculo.
- **Procesamiento de preopen**: congelación una vez por sesión con manifest y source identity; publicar nuevo corte sólo si condiciones de memoria/CPU/DB/disk y latencia permiten. Antes de OPEN, demostrar que la nueva rama equivale a la anterior en cálculos, identificadores, hashes semánticos y reglas de readiness bajo inputs iguales. Si no llega a tiempo, bloquear nuevas entradas PAPER, no “hacer green” con datos cerrados.
- **En OPEN**: preservar contrato de generación actual (límite fuente de 120 s, comprobación de pares adyacentes y strict native soak). Un heartbeat OFFHOURS_IDLE **jamás** satisface requisitos de generación fresca ni fuentes actuales. Modificar lectores/status de forma versionada, sin reutilizar “SHADOW_OBSERVING” como sinónimo de idle.
- **COLD/Audit durability**: drenar y verificar los snapshots existentes ANTES de adoptar policy nueva. No borrar FAILED, no perder proof del pack original; mantener manifest digest/receipt, identidad exacta, barreras de 128 MiB HOT, packs COLD 512 MiB con piso de disco y RAM. Recuento de cuota **recursiva** con sumas medidas, reconciliación de orphan/pending, y GC por reachability tras recibo y backup, no por edad o nombre.
- **I/O ahorro sin mentiras**: comprobar semantically-equal input state basado en source native watermark + monotonic cursor + policy hash + session hash y fallo negativo si incertidumbre. Emitir un **heartbeat separado** del CURRENT financiero, con tiempo monotónico y event type, usando roles ya existentes, intervalo definido según consumidores reales. No transformar un old cut en nuevo cut mediante timestamp.
- **Sin nuevos procesos:** cambios internos acotados en el worker/cold-audit/lectores, con tests y un dispatcher de fase compartido, no nueva infraestructura.

## 5. Velas y muestreo: evitar escrituras vacías, conservar exactitud

**Responsabilidades observadas:** bl_candle_engine.py::SampleMaterializer es el escritor real. Consume market_snapshots PRODUCTION_PAPER/TRADE, construye 1m/5m SAMPLED (no FULL OHLCV); CandleIntegrity y CandleInMarketVerify son dos auditores RO diferentes, no dos descargadores de velas. Ambos tienen un timer intervalado ~10m independiente de rueda; el generador tick ~2s hace BEGIN IMMEDIATE + heartbeat UPDATE aunque no haya muestras.

**Propuesta:**
1. Fast path RO para inspeccionar cursor/cola; **sólo BEGIN IMMEDIATE si hay muestras nuevas, revisiones vencidas, cambio de estado material o prueba de heartbeat debida**, con retry acotado contra SQLITE_BUSY. Inactividad es normal fuera de rueda, no “candle worker dead”.
2. OPEN: priorizar nuevas muestras con límites existentes; mantener generación idempotente y ordered cursor; sin pérdida de barras ni duplicación tras crash/replay. CLOSED: drenar backlog hasta límite breve y mantener heartbeat de bajo costo; no crear velas artificiales por ausencia de trades.
3. Separar **DATA_PROVENANCE**: sampled snapshots vs OHLCV proveedor; nunca escribir volumen/trades/VWAP sintéticos, nunca READY_CANDLE_COMPLETE a partir de SAMPLED.
4. Mantener auditores semánticos y de progreso **sólo donde tengan preguntas distintas**; si se fusionan, mantener todas las assertions originales, no eliminar checks útiles. El “inmarket verify” debe admitir ejecución por calendario sólo en fase correspondiente; test también que faltantes reales durante OPEN fallan.
5. Medir beneficios por registros nuevos, transacciones y WAL de cada fase; cambios de lectura de tabla/scheduler exigen A/B en la misma instantánea, no deducir mejora por frecuencia nominal.

## 6. Histórico: bootstrap una vez, luego actualización justificada

El acuerdo funcional es **bootstrap documentado por identidad exacta + historia propia incremental cuando alcanza + gap repair/refresco de proveedor sólo si hay evidencia de falta o corrección legítima**. Esto NO equivale a “jamás consultar al proveedor”: las muestras REST locales no garantizan máximo, mínimo, volumen ni OHLC completos, ni observan sesiones/instrumentos no capturados.

**Contratos requeridos:**
- Provenance por clave completa: (símbolo, familia, mercado, moneda, plazo, sesión, price_basis, adjustment_basis, source, source_event_at, received_at, known_at, quality).
- Bootstrap de cada instrumento con data desde cutoff verificado, digest del dataset, highest_seen_day, calendario, cobertura por sesión y consumo real de API; idempotencia y cursor. Repetir sólo si hash/cobertura cambia o repair fue autorizado.
- Historial muestreado local inmutable: 1m/5m SAMPLED y daily PARTIAL con porcentajes de cobertura y missing_minutes. No “ascender” a OHLCV FULL ni reetiquetar fuente.
- Gap ledger: faltante exacto por instrumento/sesión y calidad requerida; reparación externa por presupuesto cuando un job declare DUE verificable. No son justificación suficiente “pasaron 2 horas”, “estamos de noche”, “se reinició” o “el timer está enabled”.
- PPI es identidad primaria; complementos A3 (FUTUROS/OPCIONES), IOL y BYMA informan, nunca sustituyen identidades PPI ambiguas. Mantener revisiones históricas versionadas y la precedencia sin recrear rows idénticos.
- **PPI history que ya existe**: scanner tiene _daily_sync y _background_ingest postclose (máximo un intento por sesión según estado), condicionados a cutoff repair COMPLETE y presupuesto de copia 512MiB. Antes de suprimir o adaptar, demostrar comportamiento runtime y no perjudicar cobertura; el bloqueo no es “descarga exitosa”.
- **A3**: tres timers distintos (daily 18:30, reconcile 23:30 y weekend Sat/Sun 03:00). Auditar efecto por identidad: versiones únicas nuevas/consulta, fuentes/correcciones y coste. Conservar sólo trabajo con valor probado; no perder capacidad de reparar datos derivativos no reproducibles localmente.
- **Local EOD**: lee 1m/5m sampled y publica en market_history.db; actual _runtime_block veta cualquier OPEN PAPER, lo que puede bloquear el archivo cuando un EXIT_PENDING_MARKET_CLOSED legítimo continúa vivo. Resolver con prueba READ_ONLY corta y snapshot/cursor idempotente bajo presupuesto sin tocar libro de posiciones ni flexibilizar seguridad, o diferir backlog con estado explícito. **No desactivar guard directamente**.

## 7. SQLite / WAL / filesystem: un presupuesto por escritor y valor

**Inventario obligatorio:** diferenciar WAL de observer_v17.db, cold market_history.db, preopen evidence, audit COLD, Docker/container logs, backups, systemd journald, snapshots, caches. Comparar filas + page_count/page_size/freelist, tamaños stat DB/WAL/SHM y bytes escritos por cgroup/proceso, retention/outbox y lock wait; no inferir crecimiento de SQLite porque shadow lea RO.

**Control de recursos propuesto:**
- Políticas de prioridad por **trabajo** y no sólo proceso: ad hoc nice/ionice en tareas externas existentes, y CPUWeight/IOWeight/MemoryHigh de cgroup v2 **si el host lo soporta y el test no penaliza al supervisor**. No imponer MemoryMax agresivo que dispare OOM.
- PITFALL: sqlite WAL puede crecer por checkpoints frenados por lectores largos; un UPDATE idéntico genera trabajo, no siempre una nueva fila. NO ejecutar WAL_CHECKPOINT(TRUNCATE), VACUUM, schema migration o copia pesada durante OPEN. No usar PRAGMA wal_checkpoint como sonda READ_ONLY; checkpoint es escritura operacional, sólo después con owner.
- Collector pasivo de baseline: datos ya presentes en /proc, cgroup y logs en ventanas acotadas, sin prometer atributos históricos que no estén retenidos. Si hay 2vCPU/2GB, contabilizar por cgroup y si cambió hardware antes/durante el periodo.
- Disponer un único actor por DB/tabla/función de negocio; modelar tabla [actor, modo, fase, read/write, cursor, idempotency_key, external_calls, owner]. Mantener compensaciones y retries bounded, no time.sleep global donde puede perder EXIT.

## 8. Schedulers/duplicación: conservar redundancia valiosa, eliminar redundancia costosa

**Regla:** scheduler es el disparador, la función es el guard y una evidencia/cursor es la autoridad. Tener dos timers para la misma DB no es automáticamente duplicado; tener dos escritores para misma clave/fase sin mutex, sí.

Revisar cada uno de los 20 ACTIVE_TIMER, además de: unidades EXTERNAL_UNOWNED como critical approval/proxy y cockpit privado; residuales Legacy por preservar; GitHub Market Watch scheduled en main; procesos internos condicionales; potencial monolítico antiguo NO_VERIFICADO en runtime. Declarar matriz canónica de solapamientos:
- Candle integrity semántica vs inmarket check del cursor/quick-check focalizado: complementarios, pero confirmar costo, cola/estado y horario.
- Runtime evidence vs introspection vs scheduler export: solapamiento parcial de salud; no recalcular consultas caras, dedupe de snapshot comprobado.
- GitHub Market Watch vs host 10m fallback: redundancia antifalla **deliberada**, dos fuentes de alarma con misma clave incidente y transición.
- A3 daily/reconcile/weekend, PPI daily/background, EOD local vs History v2: overlap si consultan mismas identidades/sesiones sin gap/new hash.
- Backups host general y viejo mantenimiento interno: asegurar exclusividad real por modo y paths, no inferir que el compose legacy esté corriendo.
- PPI Watch: inventario indirecto permitido sólo read-only, sin enumeración invasiva ni mutación; salud externa NO_VERIFICADA no se presenta como green.

## 9. Observabilidad y criterios de valor

Registrar por trabajo: run_id, fase, motivo DUE/NOT_DUE, source native watermark, cursor antes/después, filas nuevas/corregidas/descartadas, proveedor/API calls, CPU seconds, RSS peak, wall p50/p95, read/write bytes, failed fsync, SQLite BUSY/LOCKED count, delta DB/WAL y cold files, total space/inodes, pressure PSI CPU/memory/io (si existe), materialized generation age, alert transitions, papel real_orders. JSON limitado por tamaño, sin secretos ni censo masivo de datos de clientes; alimentar dashboard desde export existente, no crear segunda telemetría redundante.

**Ninguna métrica inventada:** la última auditoría host NO midió crecimiento WAL/Shadow por hora. Establecer baseline reproducible antes de modificar; hist 14–20/10 cuando journals permitan, NO_VERIFICADO para lagunas.

**Aceptación exacta:** p0 seguridad 0 real orders, intent EOD al abrir y caución sin mutar, no regressions SQL/frescura para OPEN, calendario por instrumento, no synthetic history, cero producer duplicado. Performance: menos writes/CPU nocturnos demostrado por A/B repetible y sin aumentar p95/uso crítico durante OPEN más allá del margen de medición aprobado. La cifra de ahorro es un OBJETIVO, no un hecho previo. Queda prohibido declarar éxito por ahorro que sacrifica cadencia o contenido.

## 10. Alternativas y decisión

1. **Apagar observer/SHADOW y timers enteros por la noche:** RECHAZADA. Mata seguridad, caución, notificaciones, durable replay, preopen o liveness y puede introducir restart storms.
2. **Mantener todo 24x7 como está:** RECHAZADA como decisión por defecto. Reescrituras y consultas sin nuevo evento no tienen justificación demostrada; altísimo costo operativo potencial.
3. **Nuevo orquestador externo, nuevo broker de mensajes, nueva base/instrumentación:** RECHAZADA por complejidad/ram/disco y riesgo de conflictos para un solo Droplet.
4. **Mantener componentes existentes con scheduling por fase/evento, un único escritor, COLD acotado y proof separada de heartbeat:** **RECOMENDADA**, *pendiente de auditoría real, benchmarks/ABI equivalence y aprobación para implementación*.

## 11. Referencias verificadas

- Fuente productiva: https://github.com/mbalbo2023/Porota-trading/tree/bc661130968f2b85b8b2146f76923ddd26eb7837
- Inventario y backlog: https://github.com/mbalbo2023/Porota-trading/issues/679
- Arquitectura/deploy previo #624: https://github.com/mbalbo2023/Porota-trading/issues/624
- Evidencia de despliegue y auditoría: https://github.com/mbalbo2023/Porota-trading/actions/runs/39519161504 y https://github.com/mbalbo2023/Porota-trading/actions/runs/39521189732
- Contratos externos: https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html ; https://www.sqlite.org/wal.html ; https://www.sqlite.org/pragma.html ; https://docs.kernel.org/accounting/psi.html ; https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html
- Todos los umbrales distintos de los ya gobernados son **PROPUESTOS**, no runtime validado.

**Continuación:** ver matriz completa en RC6_OFFHOURS_OPTIMIZATION_2026-10-20_INVENTORY.md, implementación en RC6_OFFHOURS_OPTIMIZATION_2026-10-20_DELIVERY.md y handoff en RC6_OFFHOURS_OPTIMIZATION_2026-10-20_HANDOFF.md.