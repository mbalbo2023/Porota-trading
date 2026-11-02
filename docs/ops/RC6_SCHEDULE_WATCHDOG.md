# RC6 A358 — watchdog de ausencia de Market Watch

**Estado de este documento:** diseño y pruebas del control de GitHub, NO es certificación runtime del droplet. No modifica PPI Watch, DB, motores, órdenes, fuentes, timers del host, Predeploy ni Deploy. Invariantes: PRODUCTION_PAPER/SIMULATION, real_orders_sent=0, rutas reales bloqueadas, FIX-FORWARD.

## Incidente

- 30-Oct-2026: el cron Market Watch produjo solo 2 eventos schedule observados frente a 48 slots programados de las 13–20 UTC. El run 41107508601 registró SCHEDULE_SILENT/P1, pero NOTIFICATION=NONE; el segundo run 41139201025 terminó tras SKIP_OFF_HOURS, sin artefacto de host.
- 02-Nov-2026 a las 11:47 ART: API Actions registró 0 runs del día. Esta **ausencia de creación de workflows** es distinta de un job con conclusion=skipped. No demuestra que el runtime del droplet haya cesado.
- RCA del fallo de notificación propia: la alarma depende de dos snapshots próximos del mismo cron y el cron ausente no puede notificar su propia ausencia. RCA del scheduler externo: NO_VERIFICADO, no atribuir a GitHub sin leer estado y actor.

## Dos capas independientes

1. Este workflow A358 utiliza Actions con un cron separado a los minutos 12,32,52 de las horas 13–21 UTC de lunes a viernes. También ofrece workflow_dispatch y push en main para ensayar sin llamadas al host. Lee directamente API GitHub de definición de workflow 384354765 y sus runs. No depende del código Market Watch/SSH/SQLite y no prueba el estado del droplet.
2. La supervisión recurrente de ChatGPT es una segunda capa **fuera de GitHub Actions** para comprobar que tanto Market Watch como A358 emitieron runs. Si fallara todo el scheduler GitHub, un segundo cron dentro de GitHub tampoco puede garantizar la alerta.

## Contratos y métricas

- Un evento auténtico exige event=schedule, run_attempt=1, head_branch=main y fecha del día ART. Manual, push, rerun y CI nunca cuentan como muestras de cadencia.
- El control considera slots después de 10:30 ART y hasta 18:07, con 12 minutos de gracia; dos slots maduros sin run nuevo producen P1_SCHEDULE_SILENT.
- Workflow state disabled_manually / disabled_inactivity / disabled_fork / deleted produce P1_WORKFLOW_DISABLED. Estado/API desconocidos no se presentan como ACTIVE.
- Estados NO_VERIFICADO, NOT_APPLICABLE_PR y NOT_APPLICABLE_CLOSED no son GREEN de runtime.
- La notificación P1 se añade a issue #732 solo tras comprobar ausencia de marcador idéntico para fecha y causa (máximo cinco páginas de comentarios). Ante dudas de deduplicación no se publican repetidos.
- El recibo JSON es siempre sanitizado, ligado a run_id mediante nombre del artefacto. Un workflow FAIL representa P1 o API/alerta no verificable, no fallo de trading.

## Aceptación y acciones

1. Pruebas determinísticas sin red + CI en PR, sin jobs reales omitidos. Un paso PR se declara NOT_APPLICABLE_PR.
2. Después de merge seguro a main, comprobar push run, artifact y salida de estado. El push no equivale a schedule.
3. Verificar estado real del workflow en GitHub API/UI; si disabled, habilitar con permisos existentes, sin cambiar deploy.
4. Exigir al menos 2 ejecuciones event=schedule de primer intento de Market Watch durante rueda, recibos comparables del host y un alertado independiente efectivamente entregado. Hasta entonces P1 observabilidad, runtime NO_VERIFICADO.
5. No forzar A302 en SQLite WAL: mode=ro puede crear -shm y immutable=1 sobre WAL vivo podría ocultar commits. A338 Book tiene mode DROPLET_ATOMIC_BOOK y requiere dispatch propio; el skip de A331 History es esperado por diseño y jamás debe ser rerun como Book.

Fuentes: issue #732 checkpoints A356/A357/A358, issue #478, runs 41107508601, 41139201025, 41355863267. Baselines al iniciar: main a68013f2bc39dc9ae874ee658387ef7307172d8a, product 7e1e744d6ca9ab30c51b5747dd4467af5ece0145; Draft #778 no instalado.
