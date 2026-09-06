# POROTA TRADING — CHECKPOINT CANÓNICO RC6

Fecha de corte: 2026-09-05 (America/Argentina/Buenos_Aires)
Base exacta: `release/v17.0.0-rc5` @ `852b812d610860b57b579212978443f7450e8855`
Nueva rama de integración: `candidate/v17.0.0-rc6-weekend-20260905`

## 0. Regla de continuidad

Este archivo es la fuente canónica de continuidad para RC6 y para cualquier chat/trabajo posterior. No inventar estado live a partir de este checkpoint: verificar GitHub/runtime antes de actuar. Mantener contexto técnico, plan de sábado/domingo/lunes y backlog semanal sin pedir al operador que reconstruya decisiones.

## 1. Invariantes no negociables

- `PRODUCTION_PAPER`.
- `SIMULATED`.
- real-money bloqueado.
- `real_orders_sent=0` como gate estructural + empírico.
- no network order tests.
- no derivados con order routing.
- PPI live continúa como fuente primaria para el hot path donde ya está homologado.
- A3/CEM/Primary sólo background/read-only hasta promoción explícita por familia.
- no mezclar campos de PPI y A3 en una misma decisión.
- no auto-tuning de threshold/stop/target/min reward-risk.
- no claim `COMPLETE` sobre sampled trade candles.
- close-only nunca se convierte en FULL_OHLC.
- scripts operativos accesibles: un bloque, `sudo -n`, no interactivo, salida compacta + OSC52 con `trap EXIT`.

## 2. Estado físico del Droplet al cierre de P0-1

- RC5 exacta activa.
- observer/dashboard running, restart 0; observer readonly.
- DB observer/history `quick_check=ok`.
- `real_orders_sent=0`.
- filesystem final: 49% usado; ~11.82 GiB libres.
- se eliminaron sólo artefactos RC4/staging redundantes, un predeploy redundante, dos forenses RC4 y caches regenerables.
- NO se tocaron DB activas, History Store, Contract Evidence ni backups PAPER diarios.
- no seguir borrando por borrar; C-3 retention requiere atribución por tabla.

## 3. Auditoría externa — consenso y discrepancias vigentes

Se aceptan de fondo los hallazgos RC5-01..RC5-11 y compromisos C-1/C-2/C-3, con estas precisiones:

- C-1 Forward Lab v2 bloquea tuning/real-money, NO la observación PAPER del lunes.
- P0-4 PAPER state y P0-5 Sunday Readiness son obligatorios antes del lunes.
- RC5-05: `if __name__` es contención; cierre real = `main()/build_checks()` import-safe.
- linaje `3e60501c` -> `7284fbb5` resuelto; no hay discrepancia.

### RC5-12 propuesto por auditor: RECHAZADO como está formulado

El auditor afirma que BYMA Comunicado 19016 (2026-09-01) no existe y propone reemplazarlo por 18782 (2025-07-17). Verificación independiente posterior contra BYMA oficial encontró una página oficial titulada `Horarios de Negociación, Liquidación y Recepción de Información` con `Nro. Comunicado 19016` y fecha `1/9/2026`.

Consecuencia:
- NO aplicar `RC5_12_FUENTE_NORMATIVA.patch`.
- mantener 19016 hasta auditar su contenido/adjunto y relación con 18806.
- sí conservar como gate una auditoría normativa completa: números/fechas hardcodeados deben apuntar a fuente oficial verificable; adjunto roto => `UNVERIFIED`, no GREEN.

### Labor Day USA 2026-09-07: CONFIRMADO

BYMA Trading Calendar 2026 lista `September 7 — Monday — Labor Day (USA)`.
Argentina no tiene feriado nacional el 2026-09-07.

Implicación:
- BYMA local puede operar, pero CEDEARs con subyacente USA no deben considerarse muestra normal del régimen de arbitraje habitual.
- domingo decidir y registrar política Monday: preferencia conservadora = no abrir nuevas posiciones PAPER en CEDEARs USA durante esa rueda, pero seguir observando/registrando; excluir esa jornada de cohortes normales CEDEAR.
- verificar foco actual antes de tocar config: RC5 tiene 10 identidades de foco e incluye AAPL/AAPLD/AAPLC; no asumir el conteo 1 de 8 del auditor.

## 4. Estrategia de release

Decisión: consolidar el trabajo en `17.0.0-rc6`, NO acumular hotfixes sobre RC5.

- RC5 queda congelada como baseline/rollback inmediato hasta que RC6 esté GREEN.
- RC6 parte exclusivamente de SHA RC5 exacto.
- ningún path activo de RC6 debe depender de RC4: no preflight, timer, service, deploy gate, readiness, imagen ni rollback RC4.
- referencias RC4 sólo históricas/legacy no ejecutables si son necesarias para trazabilidad.
- el draft hotfix previo queda supersedido; no promoverlo.

## 5. Política de rollback

Dos niveles obligatorios:

1. Rollback inmediato: la versión activa anterior permanece local durante deploy + soak. Debe funcionar aun con GHCR/red externa indisponible.
2. Archivo histórico: release exacta publicada en GHCR por digest inmutable + commit/version/manifest.

Gates antes de nuevo deploy:
- publish externo verificado;
- re-pull por digest verificado antes del deploy;
- label/commit exactos;
- rollback local inmediato probado con GHCR deliberadamente inaccesible;
- DB compatibility explícita o restore del backup canónico asociado;
- postflight DB/containers/mode/orders=0.

Nunca borrar la release anterior local antes de postflight/soak GREEN.

## 6. A3 Historical — decisión definitiva

A3 Historical se habilita este fin de semana si pasan gates.

### Bootstrap fin de semana
- validar identidad PPI↔A3 por familia/instrumento/mercado/vencimiento/moneda/settlement;
- validar contenido real de CEM `closing-prices`/`tick-prices` y/o Primary `getTrades`;
- validar timestamps, unidades, timezone, paginación, duplicados, gaps;
- materializar History Store v2 con provenance explícito;
- checkpoint/backoff/reanudación; no hammer.

### Cadencia normal
- DAILY INCREMENTAL post-cierre por segmento/familia, no full reload diario;
- segunda reconciliación nocturna acotada;
- WEEKEND DEEP BACKFILL/reconciliation;
- smart retry y known-empty backoff;
- idempotencia y `COMPLETE/PARTIAL/EMPTY_CONFIRMED/FAILED` por trading day.

A3 live/order routing siguen OFF. A3 background jamás veta una decisión PPI live válida por divergencia asíncrona.

## 7. Principio de ejecución del fin de semana

Desarrollar todo lo independiente en paralelo, PERO separar:

- amplitud de desarrollo/validación: puede ser alta;
- superficie del deploy previo al lunes: debe ser mínima, coherente y probada.

No confundir `hacer este fin de semana` con `meter todo al runtime del lunes`.

## 8. Anillo 1 — candidato a deploy antes del lunes

Sólo entra si CI + rollback + Sunday Readiness GREEN:

1. RC5-08 guard business-day para closing summary.
2. RC5-06 order choke point fail-closed.
3. RC5-04 corregir `Persistent=` sólo en monotonic timers.
4. RC5-05 contención/import-safe; preferencia `main()` si tests completos.
5. Auditoría normativa real: NO cambiar 19016→18782 sin evidencia; validar 19016/18806/horarios.
6. RC5-02 UI: MFE/MAE `NO_MEDIDO`, no llamarlos evidencia.
7. RC5-01 + RC5-09: una sola autoridad de render + paginación preservada.
8. P0-4 PAPER state read-only; reset sólo si prueba contaminación.
9. P0-5 Sunday Readiness fail-closed.
10. Tablet/Voice Access: la UI debe ser realmente usable en Samsung; no aceptar columnas deformadas ni horizontal scroll global.

### MFE/MAE schema

No hacer `ALTER TABLE` sólo por presión pre-lunes. Como no existe writer histórico, todas las filas previas al cutover pueden marcarse `NOT_MEASURED` cuando se implemente la medición. Diseñar `excursion_state`/provenance correctamente y migrar con backup + tests; incluir antes del lunes sólo si queda demostrado metadata-only/seguro y realmente necesario. El rótulo UI sí es inmediato.

## 9. Anillo 2 — desarrollar/testear en paralelo, no desplegar pre-lunes salvo evidencia extraordinariamente fuerte

- RC5-03 reloj único fail-closed.
- RC5-11 fase persistente/idempotente.
- policy central Telegram calendar/severity.
- Forward Lab v2 completo.
- expectancy telemetry observation-only.
- retention v2.
- refactor grande de tablas si requiere cambiar arquitectura semántica.
- sector map/correlation/family normalization.
- History full-universe/freshness refinements.

### Telegram

Antes del lunes: guard puntual de cierre + verificar que CRITICAL siga llegando.
Policy central se puede desarrollar este fin de semana, pero no se despliega hasta tener tests que prueben expresamente que `priority=0`/CRITICAL nunca se suprime.

## 10. Accesibilidad de tablas — posición del proyecto

El auditor señala que data tables pueden estar exceptuadas de WCAG Reflow y propone horizontal scroll interno. Para este operador eso NO resuelve el requisito: operación 100% por voz en tablet implica evitar scroll horizontal y columnas ilegibles.

Criterio de aceptación propio:
- cero horizontal scroll de página;
- tabla o representación alternativa usable por Voice Access;
- 10–20 filas iniciales;
- Mostrar más/menos o paginación textual;
- controles visibles, no icon-only;
- foco/estado preservado al refrescar;
- prueba de campo real en Samsung antes de GO.

Si el card-layout actual degrada semántica/AT, diseñar representación responsive alternativa sin sacrificar Voice Access.

## 11. Sábado 2026-09-05 — trabajo paralelo

- branch RC6 exacta y checkpoint canónico.
- materializar fixes pequeños de auditoría en source, no patch monolítico.
- tests focales por fix.
- CI RC6 específico.
- rollback externo publish/recovery + rollback inmediato offline rehearsal tooling.
- P0-4 inventory tool.
- P0-5 Sunday Readiness aggregator.
- A3 alignment/payload validation/bootstrap.
- dashboard pager/double-render/MFE UI/a11y.
- auditoría normativa BYMA completa.
- no deploy hasta candidate GREEN.

## 12. Domingo 2026-09-06 — host truth y GO/NO-GO

- CI completo y build exacto.
- external artifact verified + rollback inmediato con GHCR bloqueado.
- deploy transaccional sólo de Anillo 1 aprobado.
- `systemctl cat/status/list-timers` y scheduler dashboard = host truth.
- Telegram domingo: routine=0; critical path preservado.
- calendario BYMA + Labor Day USA explicitados.
- P0-4 estado PAPER interpretable.
- P0-5 único informe GREEN/RED.
- tablet/Voice Access field test.
- A3 background/history independiente del hot path.
- Contract Evidence weekend policy: no browser auth innecesario.
- introspección/early warning validado.
- después de GO domingo: CHANGE FREEZE hasta cierre del lunes; ante incidente, rollback, no roll-forward improvisado.

## 13. Lunes 2026-09-07 — campaña PAPER

Antes 10:15:
- exact SHA/image;
- containers/restarts/readonly;
- DB quickchecks;
- disk;
- timers/scheduler;
- CE;
- `real_orders_sent=0`.

10:15–10:30:
- preopen;
- PPI auth read-only;
- catálogo/current quotes;
- decisión explícita sobre CEDEARs USA por Labor Day.

10:30–17:00:
- PAPER only;
- monitor quotes/decisions/gates/positions/PnL/marks/candles/history/freshness/CE/disk/restarts/Telegram.
- no cambios de código/config durante rueda.

17:00:
- CLOSED/reconciliation;
- `real_orders_sent=0`.

EOD:
- `/validacion` campaña: objetivo, cumplimiento, evidencia, desviaciones, incidentes, lecciones, blockers.

## 14. Semana posterior — backlog obligatorio

No olvidar ni reabrir decisiones sin nueva evidencia:

- unificar reloj de mercado y calendar fail-closed;
- persistencia de fase;
- MFE/MAE medidos bid-based con provenance;
- Forward Lab v2: HAC/panel correcto, stationary/block bootstrap por `trading_day_ar`, leave-symbol/day-out, cohorts, subperiods, multiple testing, DSR donde aplique;
- expectancy observation-only;
- retention v2 tras `dbstat`/atribución de tamaño por tabla; backup, batches, local day AR, WAL management; no VACUUM imprudente;
- Telegram central severity/calendar con CRITICAL unsuppressible;
- A3 daily incremental + weekend deep backfill operativo;
- sector map/correlation/family normalization;
- historical coverage/freshness full universe;
- close-only salvage effectiveness;
- candle integrity/sample semantics;
- `/validacion` critical path y campaña diaria;
- lifecycle rollback GHCR + limpieza automática sólo post-soak;
- table/accessibility regression suite + pruebas reales de tablet;
- real-money continúa BLOCKED hasta proyecto/gobernanza específicos;
- derivatives execution continúa BLOCKED;
- threshold 0.83 y tuning masivo continúan rechazados sin evidencia.

## 15. Regla final de consenso

El objetivo no es `meter más cambios`; es llegar al lunes con la mejor evidencia posible y el menor radio de fallo. Todo lo que pueda desarrollarse/validarse sábado y domingo se hace en paralelo. Sólo lo que pase gates estrictos entra al runtime. Lo demás queda versionado, probado y en el plan semanal, nunca olvidado.
