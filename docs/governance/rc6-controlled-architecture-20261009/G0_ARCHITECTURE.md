# G0: decisión B, ejecución bloqueada por prerrequisitos

Se selecciona **B: hard cap agregado nativo de filesystem**, con todos los
escritores confinados a la misma frontera y el manager Original existente.
No se certifica viabilidad operativa del runner actual. Estado binario actual:
**BLOQUEADO_PREREQUISITES**. No hay un calibrador nuevo, backend nuevo, runner
contratado ni ejecución privilegiada en esta misión.

## Contradicción de dimensionamiento comprobada

Autoridad: código `ecad18b6010e3b7e874a00edc72644b3dcc78790`,
`scripts/rc6_capacity_calibration.py::_limits`,
`ops/policy/rc6-heavy-test-governance-v1.json` y RCA de #479.

| Término | Bytes | Significado |
|---|---:|---|
| Piso nominal canónico | 15.032.385.536 | 14 GiB; no garantía de 30 GiB libres |
| Backing del bootstrap actual | 27.917.287.424 | 26 GiB de almacenamiento físico requerido |
| Hard cap de proyecto | 21.474.836.480 | 20 GiB; no sustituye tamaño de backing |
| Reserva independiente | 4.294.967.296 | 4 GiB fuera del proyecto/backing |
| Mínimo antes de controles | 32.212.254.720 | 30 GiB; todavía faltan controles/overhead |

El piso de 14 GiB no garantiza los prerrequisitos del bootstrap que pide 30 GiB.
Los 91.698.458.624 bytes libres observados en una instancia del run37845108215
no convierten ese dato en garantía para futuras instancias. Tampoco prueban
imposibilidad universal: un runner concreto podría tener más espacio.
La calibración de capacidad aislada de 5 GiB/512 MiB no certifica el bootstrap
compound de 26 GiB/20 GiB.

El inventario completo debe acotar simultáneamente Python3.11 y3.12, las157
distribuciones originales y sus hashes/ABI, wheels y entornos, los cuatro sdists
originales (`msgpack`, `ppi-client`, `signalrcoreppi`, `ta`), sus backends/build
temporaries, seed fullGit y19 objetos originales, dos fullSource clones sin
hardlinks/alternates, packs/fsck, controles, directorios y cleanup/recovery.
Las copias no concurrentes sólo pueden descontarse con lifecycle demostrado.
Un size final, una descarga comprimida o el high-water de un prefijo no es
cota del peak compound. Ese bound sigue **NO_VERIFICADO**.

## Comparación y descarte finito

| Alternativa | Viabilidad respecto del contrato | Decisión |
|---|---|---|
| A: inventario estático completo | Necesario para dimensionar. Por sí solo no confina backends/writers adversariales ni acredita aislamiento físico equivalente. Añadir esa frontera lo convierte en B. | No seleccionada como arquitectura suficiente. Se usa únicamente su inventario dentro de B. |
| B: cuota/filesystem nativo preparado | Puede imponer un límite agregado atómico de bloques/inodes a todos los escritores. Exige frontera disponible **antes** del primer actor privilegiado, sin escapes, y custodia ROOT/NONROOT/FIN real. | **Única seleccionada**; ejecución bloqueada hasta demostrar dependencias. |
| C: runner preparado para el contrato original | Resolvería disponibilidad de plataforma sólo si garantiza también storage, cuotas, namespaces y custodia; otro runner no arregla automáticamente FIN. Cambia la decisión operativa/costos del contrato canónico. | No seleccionada ni provisionada. Sólo decisión explícita futura si la plataforma canónica no puede ofrecer B. |

No hay equivalencia demostrada mediante polling, timeout, RLIMIT, cgroups de
I/O, salida del CLI Docker o `--storage-opt` aplicado sólo a una capa mientras
volúmenes/writers quedan fuera. Un tmpfs de20GiB frente a RAM nominal16GiB
tampoco acredita la cuota física ext4 original. No se adopta ninguno.

## Dependencias concretas y aceptación conjunta

Se conserva la implementación de quota/admission y el manager Original;
`PRIVILEGED_SIGNAL_CUSTODY=false` continúa cerrado. Antes de habilitar **cualquier**
prueba privilegiada futura deben existir estos cuatro comprobantes:

1. **Envelope físico**: bound autenticado del compound bootstrap completo,
   todos los picos y entradas, reserva adicional4GiB y>=10% inodes libres en
   el filesystem real, métricas/origen comparables y lectura live<=60s.
2. **Límite nativo previamente disponible**: identidad device/mount/fs/allocunit,
   soporte real de quota/proyecto, hard cap agregado, todos writable paths
   incluidos, sin escrituras por FD externo/capability/UID o namespace que
   permita escapar. Todos los writers productivos corren NONROOT con privilegios
   mínimos. No hace falta inventar otro formato de recibo: usar los existentes.
3. **Custodia desde el primer actor**: autoridad auténtica para terminar ROOT
   y NONROOT durante setup, producer y cleanup. `killpg=SENT` puede entregar
   parcialmente; UID drop tardío, forwarding sudo o filecap no prueban cobertura
   del arranque privilegiado. TERM2/FIN5, wait4/ECHILD/ausencia de PGID y cierre
   real de escritores/namespaces son los originales, no una espera ampliada.
4. **Disponibilidad de plataforma**: el runner canónico ofrece1–3 sin costos
   nuevos ni instalación experimental para averiguar si puede ofrecerlos.
   Capacidad observada en un run no satisface esta garantía por sí sola.

**PASS de preparación = 1 AND 2 AND 3 AND 4.** Cualquier UNKNOWN/false =
BLOQUEADO; ningún PASS documental habilita G0. El posterior G0 nativo exige
recibos del SHA/tree final y autorización vigente, fuera de esta misión.

Evidencia anterior no se reinterpreta:37823993377 falló recursiveRO;
37830339583 pasó mount_setattr pero falló mount ext4/prjquota con exit32;
errno numérico discriminante no está preservado.37845108215 fue diagnóstico
READ_ONLY sobre7224ca0, con ROOT/image/inner namespace NOT_CALLED y FIN externo
positivo. No acredita FIN interno. `CONFIG_QFMT_V2=m` y un módulo no encontrado
en cuatro rutas no demuestran kernel incompatible. El paquete firmado exacto
fue examinado offline por el owner anterior; aceptación/carga real no probada.

**Decisión pendiente que bloquea**: garantizar B y el envelope completo en la
plataforma canónica, o reconocer que el piso/plataforma no satisfacen el contrato
y aprobar explícitamente su modificación/runner preparado. No se compra, instala,
carga ni solicita nueva infraestructura aquí. Se detiene la experimentación.
