# POROTA TRADING RC6 | Agente autónomo SRE: reparación definitiva y limpieza profunda

**Fecha de emisión:** 11 de octubre de 2026.  
**Workstream:** `WS-RC6-SRE-REPAIR-DISK-DEEP-20261011`.  
**Repositorio:** `mbalbo2023/Porota-trading`.  
**Registro canónico:** GitHub (Issues, branches, PR, Actions, artifacts, manifiestos y SHA).  
**Plataforma de ejecución recomendada:** **ChatGPT Work** como agente SRE/orquestador, con GitHub Actions como plano de control. Codex continúa por separado en G5P (Issue #489).  
**Modo financiero no negociable:** `PRODUCTION_PAPER / SIMULATION`, `real_orders_sent=0`, rutas de órdenes reales bloqueadas.  
**Reglas permanentes:** PAPER/SHADOW ONLY; PPI Watch **NO TOCAR**; FIX-FORWARD ONLY; nunca rollback; no Codespaces ni operaciones manuales por SSH como vía ordinaria.

## 0. Resultado exigido, no una nueva auditoría circular

Resolver **causalmente** y **validar en runtime** las fallas SRE del Droplet; limpiar **todo lo que sea demostrablemente prescindible** sin perjudicar operación, integridad de datos, estabilidad, trazabilidad o posibilidad de recuperación. Una ejecución `success` de GitHub Actions, un servicio `active`, un documento de diseño y un semáforo pintado de verde NO equivalen a servicio funcional.

**Trabajar de punta a punta sin pedir instrucciones de rutina.** Si falta una herramienta, descubrir la capacidad instalada y ofrecerse un método admitido; si no existe, crear un workflow o script acotado mediante branch y PR aislados, añadir tests, control de privilegios y artefactos, y usarlo por Actions. No recurrir a un comando SSH manual por conveniencia ni usar un rechazo de permisos para fingir que se resolvió un problema. No repetir un experimento bajo condiciones iguales: cada nueva prueba debe tener un discriminante material, un guard o una corrección de código/configuración.

El éxito de esta misión no es un único `GREEN` global inventado. Se admiten cierres parciales verificables por componente. Si una limitación real no puede superarse sin romper seguridad, destruir el único dato o pagar infraestructura, cerrar **sólo ese subproblema** como `BLOQUEADO` con errno/permiso/costo, RCA y propuesta concreta; avanzar autónomamente en todos los frentes independientes.

## 1. Fuente de verdad y punto de partida verificable

Antes de cualquier mutación, recuperar información **en vivo**; la evidencia a continuación es un checkpoint histórico, nunca estado de ahora:

- Issue SRE principal: <https://github.com/mbalbo2023/Porota-trading/issues/491>.
- Limpieza anterior y evidencia: PR DRAFT #490 <https://github.com/mbalbo2023/Porota-trading/pull/490>; run GREEN `38155946058`; artifact `11708675267`; digest SHA256 `a0a9e285456b069b2fd40ffaa2cf5d24f2dd0952ded1cf947d6836ab55fe402f`.
- Auditoría posterior: run `38155277100`, intento **2**, artifact `11708123880`, digest SHA256 `e05885b2eaaf2a832a993a05411a3ce42e2028210d4c3fbb8c37cbc5f603eb5c`.
- Al cierre anterior se eliminaron **26 backups** previamente autorizados y se recuperaron **5.585.100.800 bytes físicos**. Disco libre observado: **9.431.719.936 bytes** en la auditoría posterior. No hay evidencia de backups/snapshots DigitalOcean asociados, y el timer de backup permanece programado. Nunca afirmar que existe recuperación externa sin verificarla.
- En esa captura el Droplet era DigitalOcean `594077619`, 1 vCPU, 1 GiB RAM, disco nominal 25 GiB, región `nyc1`, estado activo a nivel plataforma. El host reportó checkout HEAD `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`, lo cual **no es prueba** del SHA exacto ejecutado en la imagen Docker.
- Contenedores anteriores: `porota_production_observer` running (46 reinicios acumulados, sin OOMKilled registrado); `porota_production_dashboard` healthy; `porota_critical_approval_rc6` running, pero **unhealthy** (health failing streak 24.784 y últimas sondas ExitCode `-1`). El crítico tenía aproximadamente 60 PIDs; investigar el límite exacto del contenedor antes de afirmar agotamiento de procesos.
- En los últimos 500 renglones recientes del observer se detectaron 250 coincidencias de texto relacionadas con SQLite lock y 68 con error/traceback. **No equivalen a 250 incidentes independientes**; localizar las transacciones y callsites causales.
- Unidades `systemd --failed` históricas: `porota-preopen-rc6.service` (fallo 9/oct), `porota-scheduler-export-hf6.service`, `porota-contract-evidence-weekend-backfill-rc6.service`, `porota-gdelt-event-risk-rc6.service` (not-found). Diferenciar unidad obsoleta de servicio actualmente necesario, sin resetearla para disimularla.
- Docker: **una imagen ID** compartida por los tres contenedores, **cero imágenes reclamables** según `docker system df` y caché de build 1,402 GB informada activa/no reclamable. El `du` de `/var/lib/docker` fue pequeño respecto de lo reportado por Docker: identificar `DockerRootDir`, mounts y almacenamiento real, no asumir la ruta por defecto.
- `PAPER` en SQLite observado con `real_orders_sent=0`. Debe verificarse de nuevo después de cada intervención autorizada.
- Rama productiva de referencia: `deploy/rc6-pr69-isolated-20260915` @ `da697c6e6c2274579f9e4a112fabc4327475dd35` en el último control GitHub. Revalidar HEAD y árbol reales.
- Otro trabajo CONCURRENTE: Codex/Issue #489, `WRITE_OWNER=CODEX_CLOUD_G5P_20261011` en la última consulta, archivos de certificación G5P. No tocar ese scope, su branch, sus workflows ni su PR.

## 2. Autorización operacional y límites de seguridad

El propietario **autoriza expresamente** a este agente a:

1. Investigar autónomamente, instalar o preparar herramientas auxiliares **sin costo nuevo** y crear workflows/scripts controlados mediante GitHub y GitHub Actions.
2. Eliminar archivos, copias antiguas, cachés, capas o imágenes Docker **únicamente cuando exista prueba de que son prescindibles** para runtime y no constituyen el único respaldo o evidencia requerida. El permiso previo para eliminar backups se mantiene respecto de copias identificadas, sin extenderse a la base activa ni a su WAL/SHM.
3. Hacer correcciones operativas acotadas, incluidas configuraciones seguras, límites de recursos y **reinicios puntuales indispensables**, sólo cuando haya RCA, guard, preflight y **DEPLOY_OWNER global exclusivo auténtico**, sin interrumpir otro deploy. No asumir que un reinicio por sí solo resuelve la causa.
4. Crear PR técnicos con fixes permanentes y regresiones. Si un cambio de código necesita nuevo artefacto, usar Predeploy V2/Deploy V2 canónico con las autorizaciones, pruebas y hashes exactos; la autorización SRE **no** equivale a aprobar el merge ni a desplegar un PR candidato no certificado.

**Prohibiciones absolutas incluso bajo autorización amplia:** operaciones reales, habilitar rutas reales, tocar o reconfigurar PPI Watch, leer/imprimir secretos en artefactos, destruir SQLite activa/diarios WAL/SHM, borrar Docker volumes/bind mounts utilizados, borrar imagen o contenedor activo, truncar logs antes de conservar evidencia de incidentes, deshabilitar alertas para fingir verde, borrar evidencias únicas, desactivar controles fail-closed, sobrescribir rama productiva, romper mutex de deploy o crear gastos de terceros no presupuestados. No cambiar ni mergear el trabajo G5P de Codex.

**Ausencia de backups:** no realizar migraciones destructivas de la DB, `VACUUM`/checkpoint forzado, eliminación de WAL ni cambios de schema sin plan de recuperación independiente **verificable**, capacidad y autoridad. Una intervención conservadora de sólo lectura sí puede avanzar mientras se diseña dicho plan. No elevar costos ni crear snapshots de pago automáticamente.

## 3. Concurrencia y flujo de autoridad (obligatorio)

- Abrir o reutilizar Issue **#491** como registro de incidentes; comentar trabajo y scope antes de actuar. Crear una branch SRE aislada nueva, basada en HEAD real (no en un SHA histórico), y PR DRAFT **contra una base no productiva** para que la CI legacy `porota-predeploy-v2.yml` no se dispare con `opened/synchronize`. Documentar SHA base, árbol y rutas exactas.
- Adquirir **WRITE_OWNER exclusivo** sobre scripts/workflows SRE, logs/runbooks y tests propios mediante recibos emparejados en Issues **#471** y **#473**. Verificar que no solape con el trabajo G5P activo.
- Mientras se lee, modo `READ_ONLY`: no consume ownership global ni bloquea Codex.
- Para **cualquier** borrado del Droplet, restart, cambio systemd/config, Docker cleanup u operación de DB: verificar en tiempo real ausencia de otro `DEPLOY_OWNER` global y adquirir **DEPLOY_OWNER exclusivo** para SRE, con fecha de expiración, branch/SHA/actor y scope. Anotar recibos inmutables emparejados y revalidar antes de cada etapa. Evitar carreras con un gate vivo y el mutex:

  `concurrency: group: rc6-unified-paper-deploy; cancel-in-progress: false`

- No lanzar cleanup por evento genérico `push` sobre productiva; exigir operación solicitada, owner/Source validado, revalidación de run/attempt y protecciones `apply` separadas de `plan`. Conectar GitHub Actions a Droplet por **SSH automatizado y host key fija desde Secrets**, no por terminal del usuario. Credenciales nunca visibles en logs.
- Mientras el `DEPLOY_OWNER` SRE esté activo: impedir simultáneamente limpieza Docker, despliegue de Codex, reinicio o DB mutation desde otros chats. Codex puede continuar código/CI en aislamiento sin tocar host.
- Publicar y liberar ownership de escritura/global al terminar, **incluso si hay RED**. No dejar leases activas.

## 4. Fase A: diagnóstico forense de raíz (autónomo, READ_ONLY)

Generar artefacto original de GitHub Actions con snapshot JSON y hashes, hora UTC, identidades de host/imagen, estado Docker, cgroups, filesystem, locks y seguridad PAPER; acompañarlo de un resumen **sin secretos** en GitHub. No depender de lo dicho en chats.

### A1. Contenedor `porota_critical_approval_rc6` unhealthy

1. Leer `docker inspect` del contenedor y de la **imagen exacta**: `.Config.Healthcheck`, timeout/interval/retries/start-period, `.State.Health.Log`, `.HostConfig.PidsLimit`, `.HostConfig.Memory`, `.State.OOMKilled`, `.RestartCount`, `.State.Pid`, runtime rootfs/mounts, historial de PID/FD/zombies/threads y cgroup `pids.current`/`pids.max`.
2. Correlacionar `Healthcheck ExitCode=-1` con razones concretas: fallo de creación de proceso, PID exhaustion, timeout, ruta binaria/entrypoint, permisos, IPC/Unix socket, endpoint lógico, recursos, servicio obsoleto o configuración. **No declarar PID exhaustion sólo porque aparecen 60 PIDs.**
3. Verificar read-only el protocolo local de aprobación crítica y que su capacidad sea exclusivamente GitHub Issues / PAPER, sin llamar endpoints con efectos secundarios. Comparar contrato y bytes de imagen con Source conocido.
4. Hacer test offline/CI contra la causa reproducida: si es límite PIDs, por ejemplo, diseñar mínimo seguro medido; si es comando de salud, corregir sin mentir sobre salud funcional. RCA → FIX → GUARD → test nativo focal → evidencia. Reiniciar solamente si está justificado y adquirido owner global; medir continuidad de salud posterior.

### A2. Observer con SQLite locks y reinicios

1. Identificar el error **exacto** por stack/callsite, frecuencias por ventana, PID/hilo y DB path; cruzar `docker logs`, journal, supervisión, estado systemd, `docker inspect`, señales/exit codes y marcas temporales de los 46 reinicios.
2. Inventariar lectores/escritores SQLite, duración y solapamientos de transacciones, busy timeout, journaling WAL, checkpoints, conexiones persistentes, locks por procesos, reintentos, deadlocks, histórico, workers de scalping/caución, snapshots y timers. No imprimir rutas sensibles/token/SQL con secretos en público.
3. Ejecutar comprobaciones sqlite **solo lectura** (incluidos PRAGMA query_only y quick_check acotados) y correlacionar saturación CPU/mem/swap/disco/inodos/FD/PIDs con incidentes. No usar WAL reset, delete, VACUUM ni REINDEX para limpiar síntomas.
4. Corregir la fuente de concurrencia: arquitectura de un solo escritor o serialización por objeto, transacciones cortas, timeout/backoff acotado, cierre de conexiones, índices o scheduling, **según RCA**. Un aumento ciego de `busy_timeout` no cierra el incidente.
5. Añadir regresiones de múltiples procesos/lectores, interruption/recovery, no-loss, integridad de datos, `real_orders_sent=0` y ausencia de duplicaciones. Integrar correcciones por PR y deploy controlado cuando corresponda; no sobrescribir trabajo financiero concurrente.

### A3. Systemd, calendario, controles y observabilidad

- Auditoría de unidades/timers `rc6`, `hf6`, legacy y huérfanos: estado actual, último fallo, próximo disparo, Unit/ExecStart, dueño, dependencias, si está referenciado por el runtime/alertas/código; diferenciar `active`, `healthy`, `due`, `not due` y error histórico.
- `porota-preopen-rc6.service`: estudiar su fallo del 9/oct y comparar cron/rueda BYMA, exit code y resultado `NOT_DUE`; preservar fail-closed, no activar rueda fuera de horario. No confundir con el bug del *Deploy V2* del 3/oct que interpretó un `NOT_DUE` legítimo como failure, cuya corrección sigue en candidato sin certificación final.
- `porota-gdelt-event-risk-rc6.service` not-found: comprobar si es dependencia operacional o residuo antes de restaurarlo/retirarlo. No borrar unidad para esconder una falla ni reactivar funcionalidades no autorizadas.
- `porota-scheduler-export-hf6` y backfill de fin de semana: documentar si quedan obsoletos y sus consumidores antes de reset/retirement. Revisar alarmas, logs y salud del dashboard con rutas autenticadas y sin efectos.
- Verificar timers del backup tras eliminación previa: evitar recreación **descontrolada** de copias; preservar política de recuperación. Cualquier cambio systemd bajo `DEPLOY_OWNER` y test.

## 5. Fase B: búsqueda profunda y física de espacio recuperable

La eliminación previa de 26 backups no agotó todas las oportunidades. Elaborar un **mapa de ocupación real** del FS del host, no de la VM Codex ni del runner Actions. Trabajar con `nice`/`ionice`/`timeout` para que un escaneo no degrade POROTA 1 vCPU/1 GiB.

1. Identificar mounts reales con `findmnt`, `df -B1 -i`, `statvfs`, block devices, overlays, bind mounts y `DockerRootDir`/containerd; demostrar por qué `/var/lib/docker` sólo ocupaba ~9,8 MB por `du` mientras Docker declaraba imagen ~2,46 GB. Evitar sumar dos veces capas compartidas.
2. Inventariar ocupación y mayores inodes de `/opt`, `/var`, `/usr`, `/home`, `/root`, `/tmp`, directorios de datos POROTA, imágenes/overlays, logs y caches. Realizar `du -x` por subárbol acotado con errores visibles, top N directorios y archivos ordenados por **bloques físicos** (`st_blocks * 512`), no sólo tamaño lógico. Sin recorrer procfs ni symlinks a discos ajenos.
3. Correlacionar `df` vs `du` y buscar archivos borrados aún abiertos mediante `lsof +L1` o `/proc/*/fd` read-only cuando sea posible. No reiniciar un proceso sólo para recuperar estos bytes sin demostrar que el costo operativo sea aceptable y coordinar owner global.
4. Docker: `docker system df -v`, `docker image inspect`, containers/images/layers/BuildKit/cache/volumes, root real, manifiestos, conteo de referencias y pruebas antes/después. Prohibidos `docker system prune -a`, `docker volume prune` o limpieza directa de containerd. Borrar **únicamente** imágenes/layers/cache demostrablemente huérfanos y no referenciados, con criterio explícito; si el espacio reclaimable es cero, registrarlo y buscar en otras categorías.
5. Logs: medir `/var/log`, systemd journal archivado vs activo, logrotate y Docker json-logs (sin truncar el log activo); conservar el intervalo indispensable para RCA y auditoría. Compactar/eliminar sólo logs **rotados y prescindibles** según retención justificada y verificada; medir el espacio realmente recuperado.
6. Otros candidatos: cache APT ya limpiada, cache pip/build, temporales RC6 antiguos, compilación e instalaciones antiguas, ficheros `*.tmp`/staging, artefactos de deploy abandonados, reportes duplicados, backups futuros, índices regenerables, Python caches. Para cada uno demostrar dueño, última referencia, fecha, inode, FD abierto, symlinks/hardlinks, dependencia de systemd/Docker y costo de reconstrucción.
7. **Protegidos por defecto:** única imagen en uso, contenedores activos, Docker volumes, SQLite+WAL/SHM, `/opt/porota-trading/data` que contenga históricos/evidencia/catálogo/vector DB, config/secretos, scripts y branches, PPI Watch, swap, manifest de Predeploy/Deploy vigente, custodia y RAW únicos, logs aún necesarios y cualquier archivo de referencia desconocida. No inferir prescindibilidad a partir de nombres `old`, `legacy`, `cache`, `temp` o antigüedad.

Emitir un manifiesto de inventario con **clase de borrado** por objeto: `SAFE_DELETE` (demostrado y aprobado), `RECOVERABLE_CACHE` (reconstrucción demostrada), `PROTECTED`, `AMBIGUOUS`, `OPEN_FD`, `ACTIVE`, `REQUIRES_DEPLOY_OWNER`. Calcular bytes esperados y espacio libre real ANTES de borrar, y bytes recuperados (delta `statvfs`) DESPUÉS; nunca declarar liberados GB teóricos.

### Ciclo de limpieza autorizado

- Separar `audit`, `plan`, `apply`, `verify`; cada `apply` referencia un plan original sellado SHA256 y un set **explícito, finito y limitado** de paths/inodes, más actor, run/attempt y recibo vivo de ownership. Revalidar identidad y existencia inmediatamente antes de `unlink`. Rechazar symlink, mount rebound, hardlink, cambio de owner, FD abierto, path fuera de allowlist, carrera u operación activa.
- Antes de mutar, medir `PRODUCTION_PAPER`, `real_orders_sent`, imágenes activas, restart counts, servicios críticos, PPI Watch, `df/inodes` y estado de timers. Tras mutar, comparar y exigir que ninguna protección haya cambiado. Si falla, detener siguientes etapas, conservar RAW y ejecutar RCA/fix-forward: **nunca rollback ni borrar logs del fallo**.
- La limpieza ya no debe suprimir ni alterar el cron de backup sin decisión/documentación de recuperación. No reinstalar herramientas pesadas o crear copias masivas innecesarias en la máquina de 25 GiB.
- No establecer objetivo ficticio de bytes: buscar exhaustivamente y terminar cuando cada clase consumidora quede inventariada y lo recuperable seguro haya sido retirado o esté marcado con causa precisa.

## 6. Fase C: correcciones y validación real

Para cada incidente repetible, secuencia obligatoria: **ERROR → RCA → FIX → GUARD → TEST/CI/PREFLIGHT → EVIDENCIA**. No considerar cerrado un problema sólo porque el contenedor reinició y volvió a `running`.

- Preparar fixes en rama SRE aislada, tests adversariales y CI económica. Reconciliar HEAD real y evitar merges ajenos o cambios sobre archivos en posesión de Codex G5P. Nunca tocar productiva directamente.
- Si corrección es puramente de operación de host (resource limit, systemd unit, logrotate seguro), usar Action idempotente con parámetros de admisión verificados, snapshot de configuración (hash/metadata, sin secretos), preflight, aplicación acotada y comprobación post; reinicios individualizados sólo tras owner global y sin interferir con rueda. No ejecutar simultáneamente reinicio de observer, dashboard y crítico salvo necesidad demostrada.
- Si necesita cambiar código/imágenes, incorporar PR SRE a la consolidación de candidato y construir **una sola vez** en Predeploy V2; no introducir un deploy paralelo durante G5P. Ejecutar un único Deploy V2 canónico con artefacto inmutable, digest, mutex y owner global, únicamente cuando estén todos los gates y autoridad de deploy del candidato exacto. El presente permiso operacional SRE **no sustituye** la autorización de release específica.
- Verificar salud de aprobación crítica por Docker Healthcheck **y** función real (canal Issues exclusivamente PAPER); tolerancia de fallos debe justificarse, no aumentar retries para esconder fallo.
- Observar observer vivo durante una ventana concreta suficiente para demostrar que los bloqueos SQLite no se repiten en condiciones representativas; reinicios no deben aumentar. Los contadores históricos 46 y failing streak previos no deben confundirse con incidentes nuevos: medir **deltas y timestamps**. En domingo/no-rueda, las funciones de rueda quedan `NOT_DUE` hasta la primera ventana operativa; nunca fabricar GO del mercado.
- Verificar dashboard y dependencias críticas, DB `quick_check` sin writes, políticas `PRODUCTION_PAPER`, rutas reales deshabilitadas y `real_orders_sent=0`, frescura, errores P0/P1 y PPI Watch inalterado. Registrar `VALIDADO_RUNTIME` **por superficie**, con hora, run/artifact y pruebas, no extrapolar un GREEN genérico.

## 7. Mecanismo de autonomía ante errores de herramienta o permisos

1. Descubrir GitHub autenticado, Actions, conector DigitalOcean, ramas, secretos referenciados por nombre (no leer valores), permisos efectivos, workflow existente y owner actual.
2. Si falta la acción `workflow_dispatch` o comando directo, usar UI autenticada compatible o crear/ajustar workflow en branch aislada con trigger acotado, guard de owner y SHA, según contrato. Comprobar la ejecución real. No usar `gh`/SSH manual del usuario ni asumir que el plugin hace operaciones no disponibles.
3. Si falta `lsof`, `jq`, `du` o una herramienta específica: usar Python estándar, `/proc`, utilidades host instaladas o construir herramienta auxiliar auditable en Actions. Priorizar recursos mínimos. No instalar software de sistema en el Droplet si no hace falta.
4. Si el problema requiere permisos superiores: verificar alcance de `sudo -n` existente y documentar error exacto; no desactivar permisos ni elevar privilegios por una vía clandestina. Implementar guard soportado y seguro en Actions sólo cuando la autoridad necesaria exista.
5. Si un fix falla, preservar prueba original, aislar RCA, cambiar una condición causal, añadir regresión y reintentar sólo el nuevo candidato; no entrar en loop de reintentos idénticos. No crear tareas de pago ni redimensionar Droplet sin presupuesto autorizado.

## 8. Publicación obligatoria y criterios de cierre

**Todo debe quedar en GitHub**. Al comenzar: comentario inicial en #491, ownership en #471/#473, branch/PR y checklist. Por cada ciclo: checkpoint con SHA/tree, Actions run/attempt/jobs, manifests, digest SHA256, tiempo y bytes, evidencia de error, seguridad PAPER y decisión. Archivos de datos/logs sensibles se preservan en custodia apropiada y sólo se publican metadatos **redactados** más hashes. No se aceptan afirmaciones basadas únicamente en texto de un chat, summary de Codex o workflow verde.

**Entregables mínimos:**

1. `RC6_SRE_CURRENT_RUNTIME_BASELINE_2026-10-11.md` (o fecha vigente) y artifact JSON de inventario íntegro, distinguido de estados anteriores.
2. RCA separado de `critical_approval unhealthy`, `observer SQLite locks/restarts`, timers/preopen y demás hallazgos, con primer error causal demostrado y severidad real.
3. PR SRE con fixes, guards y tests permanentes; revisión de seguridad, fail-closed y evidencia de integración sin conflictos G5P.
4. `RC6_DEEP_DISK_FORENSICS_AND_CLEANUP.md`, manifest por inode/objeto, guard de borrado, delta físico, estado de Docker/DB/PPI antes/después y qué quedó protegido.
5. Validación runtime del host por intervalos y flujos representativos; servicios `healthy`, no nuevos reinicios, sin locks repetidos bajo carga observada, PAPER real_orders_sent=0, métricas de espacio/inodos y conservación de PPI Watch. Si queda una función pendiente de rueda, `NO_VERIFICADO/NOT_DUE` con criterio de siguiente ventana, sin fingir GREEN.
6. Handoff final en #491, referencias #471/#473, estado por componente (`VALIDADO_RUNTIME`, `EN_GITHUB`, `NO_VERIFICADO` o `BLOQUEADO`), impactos medidos, riesgos y decisiones pendientes; liberar todos los owners de forma emparejada.

**Criterio final de éxito:** root causes corregidas con defensa permanente + observación runtime positiva + disco limpiado hasta el límite seguro, con evidencia completa en GitHub y sin activar órdenes reales, afectar PPI Watch, degradar producción ni pisar G5P. Si cualquier requisito no se cumple, no declarar `GLOBAL_GREEN`.

---

### Prompt mínimo para iniciar este agente en Work

> Ejecutá íntegramente y de forma autónoma `RC6_AGENTE_SRE_AUTONOMO_REPARACION_LIMPIEZA_2026-10-11.md` sobre `mbalbo2023/Porota-trading`, tomando Issue #491 y la evidencia de PR #490 como base. Corregí las causas SRE, profundizá la limpieza segura del disco y validá runtime exclusivamente mediante GitHub Actions, coordinando ownership con Codex G5P. Publicá TODO en GitHub y liberá owners. PAPER ONLY, PPI Watch intocable, FIX-FORWARD, sin deploy de candidato no certificado ni gastos nuevos.