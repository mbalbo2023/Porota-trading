# POROTA TRADING RC6: peritaje arquitectónico SHADOW para auditor externo

Fecha de corte: 17-10-2026. Audiencia: ingeniería de sistemas, performance Linux, Python, SQLite, sistemas de trading y auditoría de software.

**Informe autocontenido.** Este documento y el ZIP acompañante están pensados para un tercero SIN credenciales, ni GitHub, ni SSH, ni acceso al Droplet. El reporte distingue HECHO VERIFICADO, INFERENCIA, HIPÓTESIS y NO_VERIFICADO. No representa un despliegue ni una corrección implementada por esta auditoría.

## 1. Dictamen independiente, en una página

**P0 y NO_GO vigentes.** La versión de motor Shadow instalada procede del candidato PR #619. Las ejecuciones de CI y Predeploy resultaron GREEN, y el Droplet instala código fuente/imagen coherentes; pero el proceso SHADOW no logra sostener las dos generaciones consecutivas nuevas y frescas requeridas por el gate de aceptación. Por lo tanto el estrés integral de 405 minutos virtuales EN EL DROPLET NO SE EJECUTÓ.

La evidencia nueva elimina la explicación simplista de un gate excesivamente estricto. Hay un par temporal REAL de SHADOW: 60,835 s y 69,032 s de trabajo con espera obligatoria de 30 s, un total de **159,867 s, mayor al techo de 120 s**. Siete CURRENT fueron observados con antigüedad 124,226 a 155,677 s. Cualquier aumento artificial del TTL, cambio de hora o supresión del rechazo ocultaría un fallo real de frescura.

El host documentado y contrastado con metadatos del proveedor tiene **un único vCPU, 1024 MiB de RAM, 25 GiB de disco, Ubuntu 24.04 LTS x64, swap aproximado 2 GiB**. Dos sondas independientes nativas mostraron MemAvailable por debajo de la reserva contractual de 128 MiB en 40/57 y 8/18 muestras; mínimo 70.311.936 B (67,055 MiB). Hubo espera de scheduler, swap in/out y presión de memoria/IO. La ausencia de throttling por cgroup NO demuestra ausencia de contención en un host compartido por varios procesos.

El camino crítico secuencial combina SQL, transformación, planner, familias, embudo operativo, laboratorios, generación de auditoría completa, varias codificaciones JSON, SHA-256, gzip, fsync, checkpoint, status, corte atómico y CURRENT. Las mejoras anteriores redujeron errores específicos pero no demuestran un SLO temporal sostenible en esta VM. Una optimización experimental por memo incluso empeoró tiempos de 34,411 a 66,297 s y multiplicó llamadas de 64,2 a 216,2 millones. Fue correctamente descartada.

**Diagnóstico parcial CONFIRMADO:** insuficiencia de capacidad RAM bajo el contrato, presión/espera del host, y falta de presupuesto temporal del worker. **No está medida con precisión la contribución individual por fase de fsync, GC, page faults o scheduling**, ni cuánto ahorraría el rediseño completo. Se precisa experimento A/B con mismo input, clocks, hashes y restricciones, en hardware equivalente. SQL específico ha dejado de ser dominante en varias muestras nuevas (137 a 233 ms frente a decenas de segundos del ciclo), pero errores históricos SQLITE_INTERRUPT/BUSY requieren defensa propia.

**Propuesta que recomiendo contrastar:** primero eliminar cálculos redundantes e instrumentar tiempo real as_of->CURRENT; después, si no cabe en el contrato bajo carga, separar HOT (cut íntegro pequeño para señales/seguridad/frescura) de COLD (auditoría forense completa versionada y acotada) sin romper causación, hashes, clocks, fail-closed ni consumidores. Dada la VM de 1 GiB, estudiar derecho a dimensionamiento superior sólo con evidencia y presupuesto aceptado por el operador: NO está autorizado comprar capacidad adicional automáticamente.

## 2. Proveniencia, niveles de verdad y alcance

Fuentes privadas aportadas por el operador: cuatro archivos de continuidad del 17/10: 03-CURRENT_STATE-1.md, 02-ACTIVE_PENDING-1.md, 06-ERROR_REGISTRY-1.md y 09-LESSONS_LEARNED-1.md. Los cortes vigentes están al FINAL de esos documentos y reemplazan notas anteriores; su afirmación es documentación, no por sí sola comprobación operacional.

Fuentes GitHub consultadas: código en SHA productivo 47d949b72f0b3affffecdc6cc90d31ba1347d9b7; main en 5a87538d3816601a4fddf4aaafc975b4ebc674d7; PR #619 (fusionado e instalado pero no aceptado), #622 (guard-only abierto, NO FUSIONAR), #621 (diagnósticos de sólo lectura), #623 (documentación), Issues #599/#600/#603/#620 y ownership #471/#473. Las referencias de SHA, árbol y runs sirven para reconciliación posterior: no suplantan archivos disponibles en el ZIP.

Jerarquía: SHA GitHub prueba código; un run Actions GREEN prueba únicamente su CI; artifact + hash + manifest prueban bytes; Docker runtime/imagen prueba instalación; DB/logs/APIs prueban comportamiento; un chat nunca representa ese último nivel. Estados: PROPUESTO, EN_GITHUB, ARTEFACTO_VALIDADO, DESPLEGADO, VALIDADO_RUNTIME, NO_VERIFICADO y BLOQUEADO.

Este paquete excluye deliberadamente: datos de posiciones/tickers de clientes, SQLite productiva, secretos PPI/IOL, Telegram, credenciales, .env, IP/SSH, infraestructura accesible y datos financieros privados. El código se comparte en snapshots versionados con auditoría de archivos y hashes. Un tercero puede ejecutar tests offline sintéticos, inspeccionar estructuras e identificar hipótesis; NO puede reproducir condiciones de proveedores reales sin entorno aislado y autorización adicional.

## 3. Configuración real de infraestructura y software

| Aspecto | Dato conocido y certeza |
|---|---|
| Proveedor y host | DigitalOcean, Droplet activo, región New York 1, Ubuntu 24.04 LTS x64, verificado por API |
| Tamaño | Plan s-1vcpu-1gb, 1 vCPU, 1024 MiB RAM, 25 GiB de disco local |
| Swap | 2.147.479.552 B de swap total en sonda nativa, no equivale a RAM física |
| Runtime RC6 | Docker/Compose; observer y dashboard; motor SHADOW como worker en runtime; servicios adicionales PAPER/critical-approval |
| Lenguaje | Proyecto Python 3.11; el Python efectivo del OCI debe verificarse con imagen exacta, no inferirse del host |
| DB | SQLite/WAL, observer_v17.db en data/paper_v17, lecturas Shadow read-only; historia local separada |
| Fuentes | PPI primaria en identidad; IOL/BYMA/A3/ROFEX complementarias sólo con provenance y freshness |
| Mercado | BYMA / Argentina, zona ART UTC-03; sesión simulada 10:30-17:15 |
| Seguridad | PRODUCTION_PAPER/SIMULATION, real_orders_sent entero 0, real routes NOT_CALLED y PPI Watch UNTOUCHED |
| Trabajo | Poll SHADOW de 30 s al terminar cada tick, worker con os.nice(+10) |
| Vigencia | SOURCE_MAX_AGE_SECONDS=120 y budget=min(120,2*(30+tick_seconds)) |
| SQL | Deadline 2000ms para snapshot/transform nativos, presupuestos acotados y rechazo fail-closed |
| Estrés aislado | 512 MiB, 0,75 CPU, networknone, 8 grupos, 405 minutos virtuales |
| Reservas | RAM host disponible >=128MiB durante admisión y estrés; 2GiB de espacio libre de reserva más cuentas de backups |
| Deploy | GitHub Actions V2, build once, mismo OCI y bundle certificado; no rebuild en host, mutex rc6-unified-paper-deploy |
| Operación | Cero órdenes reales, sin rollback, PPI Watch intocado, owners #471/#473 exclusivos cuando correspondan |
| Instrumentos | Catálogo observado alrededor de 14.611 entradas/9.902 READY; READY catálogo no significa orden elegible |
| Dashboard | 52 rutas canónicas de inventario y rutas legacy/API; HTTP200 no garantiza función correcta |

En muestras previas observer/dashboard tenían NanoCpus/Memory/CpuQuota/CpuPeriod sin límites efectivos declarados (valores cero); revalidar Docker inspect si se quiere concluir CPU share real. Procesos en host conviven sobre un único core. Un proceso nice10 recibe menor prioridad relativa cuando hay otros contendientes. El swap puede evitar OOM y simultáneamente hacer incumplible una latencia. Discos con 4,65GiB libres no excluyen I/O alto o faults. 

## 4. Diagrama lógico de arquitectura ACTUAL, de entrada a publicación

Ruta secuencial, descripción del source instalado PR619:

1. PPI da identidad/catálogos primarios; datos IOL/BYMA/A3 cacheados aportan observaciones con origen y relojes. Histórico local y SQLite WAL constituyen snapshots de entrada; no completar instrumentos ambiguos o clocks ficticios.
2. Worker rc6_shadow_runtime/worker.py::run_worker espera a no estar detenido, obtiene datetime.now(UTC) como as_of inicial, crea/retiene objeto ShadowRuntime, llama a ShadowRuntime.tick(as_of). Después de terminar (o fallar) espera siempre 30s.
3. ShadowRuntime.tick: leer capacity_controller, checkpoint anterior, source identity/configuración y sesión; leer runtime SQLite bounded; construir/preparar snapshot congelado preopen si falta; ejecutar planner con engine SCALPING/EQUITY_SPOT, enrich_pipeline, family_reports, evaluate_runtime_lab, entry_signals y evaluate_runtime_funnel.
4. En el mismo tick se proyectan durable_report_view y durable_checkpoint_view, se produce un computed-report extenso, se serializa/codifica, se hace SHA256 y gzip, write+fsync, se escribe latest.json.gz y checkpoint.json.gz.
5. Se registra measured(durable_serialization), luego se construye status con generation_timing.tick_seconds/tick_cpu_seconds; se hace write(status.json), publish_generation(report,status), cut inmutable, manifest, CURRENT.json atomizado y prune de reportes obsoletos. En PR619, tick_seconds EXCLUYE parte de status/publish/prune.
6. rc6_shadow_runtime/persistence.py::read_committed_generation valida pointer, manifest, hash de report/status, status vivo, source, checks PAPER0 y revocación tras fallo nuevo; no toma un cut anterior por actualidad.
7. scripts/rc6_deploy_generation_gate.py::collect re-lee cada ~2s durante ventana acotada; exige dos generation_id distintos, secuencia/as_of crecientes, mismo fingerprint/source/tree, budget de frescura, SQL2s, PAPER/0, hashes y estado.
8. Si gate GREEN, Deploy V2 continúa a validación completa y después scripts/rc6_postdeploy_stress_host.py lanza 405min virtuales en el host. Si gate RED, estado VALIDATION_PENDING y stress SKIPPED: es lo sucedido con #619.

Archivos principales en paquete: rc6_shadow_runtime/worker.py, timing.py, persistence.py, funnel.py, families.py, lab.py, preopen.py, stages.py, entry_signals.py, source_reads.py, read_contract.py, source_authority.py, _rc6_json_bound.c; rc6_dynamic_universe (rutas de planner y snapshot); scripts/rc6_deploy_generation_gate.py, rc6_postdeploy_stress_host.py, rc6_postdeploy_stress.py, manifests y workflows Predeploy/Deploy V2. Las pruebas relevantes están empaquetadas sin credenciales.

**Invariantes:** la publicación debe ser atómica e íntegra; no mover CURRENT antes de escribir miembros exigidos; no falsear as_of con publish time; no compartir objetos mutables arbitrariamente; fail-closed ante estado parcial/corrupto; salvaguardar moneda, settlement, mercado y bases.

## 5. Qué prueba la brecha temporal

Sea T1 el tiempo del primer tick y T2 el siguiente; después de tick1 se espera P=30s. Un primer as_of puede quedar antiguo al publicarse el segundo si T1+P+T2 y etapas finales posteriores superan 120. Con muestras reales T1=60,835 y T2=69,032, el subtotal es 159,867s, superior a120. Siete CURRENT vencidos (124,226 a155,677s) son evidencia nativa adicional. El cálculo no autoriza cambiar la edad de fuente.

Ejemplos cronológicos de runs:
- Predeploy #619 run39037517622: SUCCESS; 3950 pruebas, cero FAIL/ERROR/SKIP/XFAIL y una exclusión histórica.
- CI imagen exacta #619 run39038468598: 405min de mercado simulados, ocho grupos, 771 casos GREEN, 176 fills sintéticos; NO equivale a 405min en Droplet ni a operaciones reales.
- Deploy #619 run39038259114, job117412357625: FAILED por TWO_COHERENT_NATIVE_GENERATIONS_NOT_OBSERVED. Descarga/artifact/host install e inspección 252 HTTP200/63 rutas, soak y cero reinicios GREEN parcial; estrés Droplet completo SKIPPED, restore de ese estrés NO EJECUTADO.
- Sonda39043231531: corte inicialmente admitido con edad112,961s, rechazado al envejecer121,084s; última edad227,056s. Tick112,068 wall/43,635 CPU; durable_serialization56,672 wall/18,935 CPU; SQL232,722ms.
- Deploy #619 observó otro tick51,231s wall/35,124 CPU; durable19,181wall/13,811CPU; family6,792wall; funnel8,836wall; SQL137,877ms; transform367,287ms.
- Sonda39046830865: en un corte tick193,236wall/52,540CPU, durable111,816wall/25,651CPU, carga elevada. No atribuir los 140s de wall-CPU automáticamente a fsync.
- Sonda39047388608: pareja 60,835 y69,032s; host swap/runnable/IO pressure y reserva de RAM incumplida; clasificada GREEN como SONDA, pero runtime Shadow NO_GO.
- Sonda39047764577: DB PRODUCTION_PAPER/int0, heartbeat avanzó, HTTP200 y auth401, mismos IDs/imagen/sin OOM; GREEN sólo health, NO prueba dos cortes, jornadas, ni restore.
- PR #622 guard-only SHA3a1ce8a4000458982d6b31dec47e8269867e28ab tree c159d2ab879db262e782e347eb6351ece14dd005, Predeploy39047648539 SUCCESS 3963/3963 pruebas 0 FAIL/ERROR/SKIP/XFAIL, una exclusión histórica, artifact12106950949 y508 members. No se fusionó como fix; no hay Deploy nuevo.

El hecho de que el SQL esté <=2s en estos ejemplos NO exculpa todos los SQL ni cierra incidentes SQLite anteriores. Sí refuta que el presupuesto SQL sea el cuello principal de los ticks lentos medidos.

## 6. Presión real de recursos e interpretación segura

DigitalOcean certifica 1vCPU y1024MiB RAM. Sonda39046830865: 57 muestras, 40 incumplieron RAM disponible128MiB. Sonda39047388608: 18 muestras, ocho incumplieron. Min observado70.311.936B. 2GiB swap total, contadores host cambiaron pswpin+33.135 páginas y pswpout+21.836 páginas en88,561s. No transformar páginas en bytes sin tamaño de página confirmado; x64 típicamente4KiB, el peritaje puede verificarlo.

La cola de CPU medida por diferencias de /proc PID schedstat del proceso niceness10 mostró scheduler wait142,787s vs execution49,625s en un intervalo reportado; la contención es material. nr_throttled=0 y throttled_usec=0 en cgroup no lo refutan. Métricas host loadavg se elevaron aproximadamente2–4 con un core. PSI memory/io, major faults y VmSwap reflejan demanda; falta correlación temporal por fase de gzip/json/fsync/GC. Un proceso de Shadow mostró RSS de varios cientos de MiB.

La misma observación del cgroup mostraba memory_current alrededor488.804.352B y swap_current479.105.024B (ejemplo de muestra). Verificar qué cgroup abarca esos bytes antes de concluir que todos son de Shadow. Un report comprimido de solo ~330KB puede tener **9.910 rows/21.580.831B JSON crudo** y múltiples representaciones en RAM.

El stress runner anterior solo abortaba cuando RAM disponible caía bajo32MiB, aunque la política exigía128MiB. PR622 agregó comprobación de reserva durante la sesión, prueba con100MiB debe detener stress y restaurar owned. Es un GUARD útil, no aumento de capacidad.

## 7. Cronología de reparaciones y por qué no alcanza

- ERR-022 #596: rotación íntegra de sesiones del funnel y rechazo de overflow. Protege capacidad lógica, no garantiza cumplimiento temporal.
- ERR-022 #597: streaming de JSON/gzip/hash con cuotas y escritura atómica. Evita picos excesivos pero sigue serializando un volumen grande a cada ciclo.
- ERR-026 #600: lectura masiva SQLite packed/bounded y separación de transform Python; elimina una causa antigua de SQLITE_INTERRUPT, pero no hace que JSON/funnel/fsync ocupen pocos segundos.
- #603 y #618: medición de doble generación y arrays frozen; 9910 filas por engine, iguales por valor pero cache por identidad de objeto no garantizada.
- #619: extensión nativa C para predicado de tamaño JSON, no codificador completo; CI más rápido en predicado, pero falla aceptación real.
- #620: profiling nativo confirma stale, swap/IO/wait y RAM bajo floor.
- Experimento de memo FrozenJSONDict DESCARTADO: ticks34,411 ->66,297s, llamadas64,2m ->216,2m, pruebas de bytes pasaron pero hubo regresión de rendimiento. No promocionar como arreglo ni declarar A/B exacto (inputs/clocks diferentes).
- PR622: mejora índice opened_priority de40 recorridos a1 en fixture, conserva igualdad por5 campos; agrega publication receipt posterior, historial de128 rechazos y reserva RAM en stress. **No prueba beneficio nativo ni resuelve memoria/swap.** Queda NO FUSIONAR como producto.

Incidentes separables: SQLite BUSY/LOCKED telemetría, fuente histórica PPI ambigua633.856.000B, ON/OBLIGACIONES collision, HTTP500 por fecha año1, 52 rutas dashboard, cron/Telegram, historial EOD sampled y ausencia de señal TP. No usar esos síntomas como supuesto RCA único Shadow.

## 8. Arquitecturas candidatas y contrato de diseño

**A. Optimización monolítica mínima y medible.** Conservar worker/reader, instrumentar end-to-end source_start->CURRENT y proceso CPU/wall/IO/GC/fsync; indexar opened_priority por engine con identidad completa; reutilizar ranking inmutable solo si clave exacta/hash/version/config/time validan; evitar segunda/tercera serialización y digest redundantemente solo bajo bytes SHA idénticos; recuperar working set temprano sin GC forzado ni referencias fantasmas. Evaluar con 1CPU/1GB/512MiB stress y cargas comparables. Rechazar cualquier mejora que aumente llamadas o cambie un campo fiscal, identidad, hash o semántica de riesgo. Objetivo de diseño <=25–35s por cut end-to-end para margen bajo presión; NO reemplaza budget de120.

**B. HOT/COLD desacoplado si A no cumple.** HOT: un snapshot coherente de entrada+relojes reales, seguridad, readiness/riesgo y señales/estadísticas indispensables, corte compacto ATÓMICO y vigente; COLD: auditoría grande/checkpoints e histórico con generación propia e idempotencia, asociado a hot_generation_id/source digest, estado PENDING/COMPLETE/FAILED, backpressure durable, cuotas y posible ejecución incremental fuera del deadline HOT. No instalar por reflejo un servicio pesado adicional en 1vCPU. Evitar que COLD desactualizado se vea verde, ni que HOT borre denominator/moneda/provenance. Si un dato COLD es indispensable para seguridad, HOT queda NOT_READY en vez de fabricarlo.

**C. Replantear capacidad física si el presupuesto es imposible.** Probar en infraestructura equivalente aislada con más RAM/CPU manteniendo MISMO OCI y datos sintéticos/hash; A/B con delta de swap/PSI/scheduler/freshness. Alternativa a evaluar, ejemplo 2vCPU/4GB, NO es compra autorizada. Más recursos no repara algoritmo cuadrático, race de publicación ni clock incorrecto. Un resize productivo debe tener autorización presupuestaria específica y no ocurrir en esta auditoría.

**Hipótesis preferente a contrastar:** RED actual se debe a la combinación de ruta pesada + memoria insuficiente/swap + contención. Un split HOT/COLD puede acortar ruta de frescura; también puede crear deuda COLD si el trabajo total no se reduce. Se requiere benchmark nativo y prueba de cierre de recurso, no adoptar B automáticamente.

## 9. Pruebas negativas y aceptación exigibles

E01 perfil CPU/wall de as_of a CURRENT+durability/final writer con reloj monotonic, hashes y fuente constante; E02 carga con/sin presión swap en entorno aislado, mismo dataset/hash; E03 índice O(N*M) a O(N+M) con Unicode, settlement/moneda y same SHA; E04 encoder/gzip/hash/GC/fsync A/B con bytes canónicos idénticos y límites32MiB/4MiB/512MiB; E05 contratos HOT/COLD de fuente y consumer, crash tras HOT, proceso COLD FAIL y cola llena con NOT_READY honesto; E06 clock tests pares51/51,60.835/69.032,89/112,35/35, age120/121/futuro, dos generaciones distintas, revocación, writer race; E07 SQLite deadline2s con lock/interrupt y ledger intacto; E08 memoria128MiB/disco2GiB durante toda stress; E09 firmas image/manifest exactos; E10 jornada405min/8grupos en **DROPLET** + restore y audit diferida; E11 documento final + owners #471/#473 liberados.

La matriz debe incluir BEFORE FAIL/AFTER PASS con import-root y cwd aislados; hubo una prueba fallida de metodologia que importó nuevos módulos al intentar reproducir antes en cwd errado, generando 2PASS inválidos antes de obtener2FAIL verdaderos. No ignorar ese precedente.

Nunca cambiar TTL120, SQL2s, poll30, niceness10, memoria declarada512MiB/.75CPU ni reserva128MiB/disco2GiB para fabricar GREEN. PAPER/SHADOW integer0 y PPI Watch intangible. Readiness/fuentes no se deben inventar ni promover por cobertura.

## 10. Guía de reproducción para terceros

El paquete ofrece dos árboles versionados: product_619 (snapshot del código EXACTO instalado, sin datos privados) y guard_622 (archivos cambiados PR622, NO FUSIONAR) más diff, manifest SHA256 e índices. Adicionalmente diagnostic_621 aporta la sonda read-only en su versión más próxima; evidence incluye tres host-summary sanitizados y un resumen de runs. Tests relevantes y workflows permiten reproducir temporalidad, seguridad, serialización y preparación del artefacto, pero no reemplazan Docker/SQLite/clock del host real.

El auditor debe declarar OS, CPU, RAM, versión Python, versión librerías y deadline empleado; mantener mismo source SHA para comparar. No importar automáticamente ambos snapshots en mismo PYTHONPATH. Toda diferencia de clock/input/cgroup se etiqueta; no declarar un A/B exacto si no es el mismo dataset/hashes. Las rutas de fuente SQL del paquete no incluyen la DB productiva: usar fixtures sintéticas propias. La ausencia de secretos significa que endpoints externos no funcionarán por diseño.

**Preguntas obligatorias a tercero:** qué microcostos repetidos dominan, qué demuestra la brecha wall/CPU, cuánto swap interviene, cómo obtener P95/P99 de ciclos con tamaño de muestra conocido, cómo aislar fsync/GC/runqueue, cuánto cambia el índice por identidad, qué es el mínimo HOT necesario, cómo evitar lag COLD/cola infinita y qué haría falsable el dictamen de falta de capacidad.

## 11. Seguridad, gobierno y estados finales

PAPER ONLY, PRODUCTION_PAPER, contador órdenes reales debe ser int y0, broker real NOT_CALLED, PPI Watch intocado, no órdenes/Telegram durante stress, no copiar DB privada, no modificar host ni systemd, sin rollout de otro motor ni limpiar Docker por esta auditoría. GitHub Actions es plano de control; source SHA/tree -> Predeploy exacto -> frozen bundle/digest -> Deploy V2 único bajo mutex y DEPLOY_OWNER -> runtime checks -> stress host -> restore; no rollback. En ausencia de evidencia NO_VERIFICADO.

**NO_GO comprobado hasta corte:** #619 instalado NO ACEPTADO, #622 guard-only ARTEFACTO_VALIDADO pero no implementado en runtime, estrés405min Droplet no ejecutado, 10 ciclos no certificados, restore de stress inexistente. Salud puntual PAPER0 y HTTP200 no altera esa decisión. Usuario autorizó correcciones autónomas en #620, pero no autorizó compra de infraestructura.

Fuentes para trazabilidad: Issues #599/#600/#603/#620, PR #619/#621/#622/#623, runs 39037517622, 39038468598, 39038259114, 39043231531, 39046830865, 39047388608, 39047764577, 39047648539. Los IDs se conservan para reconciliar, pero los datos necesarios para estudio offline se acompañan cuando es técnicamente posible y seguros de compartir. La ausencia de archivo se registra en INVENTARIO y no se inventa.
