# RC6: contrato propuesto de certificación económica

Estado: **DESARROLLADO** para observación, aritmética y regresiones económicas;
**PROPUESTO / NO_VERIFICADO** para la revisión contractual y aceptación nativa.
Base preservada: PR #481,
`aee4261c58d63ca1cec54c32719878affe1d0f4f`. Costo recurrente adicional: **USD 0**.
No provisionar, contratar, ampliar ni instalar infraestructura persistente.
No merge/deploy/heavy con bloqueos de admisión. PAPER/SHADOW ONLY,
`real_orders_sent=0`, real routes `NOT_CALLED`, PPI Watch intacto,
FIX-FORWARD ONLY.

## Revisión crítica de G0

| Requisito de G0 | Garantía que debe conservarse | Implementación revisable |
|---|---|---|
| Source/fullGit/candidato | SHA/tree exactos, modos y bytes canónicos, fsck, objetos originales requeridos; ningún Source parcial o alternates | Seed y clones duplicados; cada eliminación requiere prueba de identidad y separación de escrituras |
| Product157 y CPython dual | Nombres/versiones/pins completos y ambas versiones exactas, incluyendo cuatro clases sdist | Dos instalaciones retenidas simultáneamente cuando los gates podrían ejecutarse secuencialmente |
| Cuota | Todos los escritores y descendientes suman bajo hard byte/inode cap; ningún escape | Loop ext4 de tamaño fijo; no es la única implementación de project quota nativa |
| ROOT/NONROOT | Custodia desde la primera instrucción, privilegios mínimos, señales/reap reales, FDs/UID/GID/seccomp, montajes ajenos readonly | Preparación y formato del backing; no se reemplazan por flags de admisión |
| FIN/custodia/recuperación | FIN propio físico antes de captura/cleanup; RAW/controles originales con hashes y recuperación | Copias de transporte y respaldos temporales adicionales después de que una custodia verificable exista |
| Capacidad | Envelopes completos auténticos, reserva e inodos libres, medición viva antes de cada productor | Usar almacenamiento nominal como techo o sumar todos los máximos que nunca coexisten |

El bootstrap de `rc6_capacity_calibration.py` usa un seed completo, dos clones
fullSource y dos entornos Product157, conservando ambos hasta FIN. Esto explica
la presión del harness. No demuestra que el runtime de Porota necesite 20 GiB ni
que el valor 26 GiB sea mínimo. La reserva interior de 4 GiB, la quota 20 GiB,
el proyecto probe 1 MiB y metadata justifican **la forma** del contenedor actual;
el tamaño elegido no surge de una prueba de pico mínimo del producto. Se
mantienen `limits_for`, todas las cuotas y las prohibiciones originales.

## Capacidad nominal frente a física

El contrato debe admitir un runner público estándar con más espacio real que su
garantía nominal, siempre que pase cada control. Los 14 GB documentados por
GitHub son 14.000.000.000 bytes informativos, no un límite de ejecución. No se
concluye imposibilidad gratuita por esa cifra. El floor legacy permanece en
32.596.295.680 B para bloques de 4096 B; capacidad suficiente significa únicamente
que ese piso cabe. Faltan los costos dinámicos y pruebas nativas independientes.

Existe ya una observación física positiva: el ZIP original del run 37988736476
de #481 contiene 91.698.405.376 B libres en ext4/4096 B y 18.423.046 inodos libres.
Su origen, SHA256 y CRC se verificaron al recuperar el artefacto; los hashes
exactos están en [RUNNER_DECISION.md](RUNNER_DECISION.md). Supera el piso legacy,
pero no cierra ROOT, quota, reserva ni el pico compuesto. No se vuelve a gastar
tiempo intentando demostrar que 14 GB nominales hacen imposible ese runner.

La observación read-only exige mount/dev/inodo coherentes entre lecturas;
filesystem/unidad real, bytes e inodos; proyecto/inheritance y quota readback;
kernel/config/herramientas metadata; y estado UID/GID/capabilities/namespaces del
observador. No ejecuta mounts, cargas de módulos, asignaciones de project ID,
quotas mutantes ni actores. Si `quotactl_fd` no existe, el readback es unknown;
no se recurre a un comando privilegiado ni se inventa una cuota ilimitada.
Si statfs puede estar proyectado por quota, se bloquea el uso de esa cifra como
backing físico: se necesita medir también el mount sin proyección.

## Direct project quota sobre disco efímero existente

El contrato alternativo evita un backing nuevo si el filesystem actual admite
project quota nativa. Es una alternativa al mecanismo del loop y requiere una
revisión contractual autenticada contra el SHA/tree congelado; los checks
booleanos no prueban equivalencia. El código propuesto nunca acepta esa revisión
ni concede launch por sí mismo.

Se conservan:

- hard quota agregada de 20 GiB y 100.000 inodos del proyecto principal;
- proyecto EDQUOT de 1 MiB y límite de inodos histórico;
- reservas independientes de 4 GiB para workload y 4 GiB para custodia;
- controles exteriores completos, ambas copias de captura actualmente
  requeridas y cero crédito por cleanup futuro;
- dataset, cobertura funcional, bytes exactos, identidad, FIN y recuperación.

La cota aritmética conservadora es
`quota20GiB + probe1MiB + reservas8GiB + controles + metadata incremental`.
El ahorro necesario máximo es `2GiB - probe1MiB - metadata incremental`.
Metadata unknown bloquea aun con un disco grande. Ese ahorro es una cota
diagnóstica; no prueba el pico completo, la reserva efectiva ni soporte nativo.
Una propuesta más agresiva que fusione reservas o calibre a menor escala queda
fuera de esta aceptación. Sólo podría aceptarse con su propia demostración y
revisión explícita, sin modificar límites históricos silenciosamente.

La eliminación del loop cambia una propiedad importante: el `fallocate26GiB`
actual reserva físicamente los bloques frente a escritores exteriores. Una
quota directa limita consumo pero **no reserva** esos bloques. Por tanto, usar
direct quota requiere demostrar exclusión/cotas de escritores externos y reserva
equivalente efectiva; no alcanza un `df` previo. Unknown de esos escritores
bloquea esta alternativa. Si no se demuestra, se conserva el contrato legacy y
se prueba su capacidad real en el runner gratuito; no se pide comprar disco.

## Obligaciones para demostrar equivalencia

| Invariante | Prueba necesaria | Estado de esta revisión |
|---|---|---|
| Cuota agregada | Readback kernel exacto; herencia a archivos/directorios/descendientes, writes/fallocate/rename/link y EDQUOT reales; temp/log/home/cache/build/fetch confinados | Contrato y sensores desarrollados; enforcement nativo NO_VERIFICADO |
| Reserva física | Capacidad sin vista proyectada; cotas auténticas de metadata y todos los escritores externos; recheck inmediato por productor y tratamiento de ENOSPC | Aritmética conservadora demostrada; reserva equivalente NO_VERIFICADO |
| Aislamiento | Namespaces propios exclusivos autenticados; nodev/nosuid, montajes ajenos readonly, sin aliases ni proyecto reasignable | Pruebas originales preservadas; alternativa nativa NO_VERIFICADO |
| ROOT | Custodia privilegiada antes de primera instrucción; signal/reap incluso si falla antes del UID drop; TERM2/finalize5, ECHILD y reap completos | Hold original intacto; BLOQUEADO |
| NONROOT | UID/GID/caps reales, NNP/seccomp, FDs cerrados, sin mount/unshare/quotactl mutante ni signals ajenas | Sensores observan su propio proceso; no prueban un worker futuro |
| Source/evidencia | SHA/tree/list/graph/codecs exactos; Source original y fullGit requerido, counts/identidades/JUnit auténticos; all11 sin hardlinks nuevos | Guardas existentes preservadas; nuevo candidato debe recalificarse |
| FIN | FIN propio físico antes de capture/cleanup; namespace desconocido preservado, sin matar/limpiar agentes ajenos | Prohibiciones intactas; cada ejecución debe demostrar FIN |
| Recuperación | Captura original RAW/controles verificada fuera del namespace; hash de custodia y restore/crash/GC genuinos | Sin crédito de cleanup futuro; pruebas nativas pendientes |
| Costo | Runner público estándar, nada persistente, cero contratación/ampliación y sin perfil facturado nuevo | Diseño USD0, sin provisionamiento |

La prueba capability con quota principal 512 MiB no se convierte en bootstrap
20 GiB. El kernel y readback de cada ámbito deben corresponder al contrato exacto
de ese ámbito. Las regresiones económicas no son receipts G0–G8.

## Duplicaciones y separación de jobs

Separar épocas/gates en jobs **secuenciales** puede liberar Source, fullGit,
entornos y temporales tras FIN y captura, manteniendo un candidato y un DAG.
Cada job necesita su propio owner/attempt/runner, inventory exacto, readback,
preflight y receipt auténtico. Una aprobación antigua de G0 no acredita el
filesystem del siguiente job. Capturar sólo índices/hash sin RAW requerido o
reusar una venv como prueba Product157 de otra época no conserva cobertura.

La quota de dos jobs simultáneos no es una quota agregada común: dos caps de
20 GiB podrían sumar 40 GiB. Esa división queda prohibida para el mismo dominio
de agregación sin revisión/proof específica. El costo debe calcularse por el
máximo de fases simultáneas, incluyendo overlap real de RAW/captures/artifact
downloads y la retención original. Sólo liberar recursos **ya** capturados,
verificados y con FIN permite restar su costo en fases posteriores. No se usan
hardlinks para deduplicar Source cuando el contrato exige nlink=1/all11.

La certificación económica y compatibilidad de recursos pueden estar aisladas
de tooling dual Product157 pesado, conservando los controles de origen y el
candidato. El ambiente de 14 dependencias económico nunca demuestra por sí
solo las 157 dependencias, fullGov ni G0.

## CAS y revisión de formato

G5 conserva los 1.202 ciclos, quota 512 MiB, profundidad máxima 32, bytes exactos
y retención original. La optimización algorítmica reduce duplicaciones físicas
con recipes de slices sobre objetos CAS retenidos y restore acotado; no reduce
universo ni reemplaza los datos por una aproximación.

**Revisión requerida:** el recipe V4 necesita dispatch de versión y lector
**>=4** en todos los consumidores de archive/restore/GC/retention. El envelope
V3 debe ligar con hash el codec y layout exactos. El decoder original debe
permanecer byte exacto. Mantener rutas lectoras de versiones anteriores y
probar rechazo fail-closed del nuevo formato en lectores no compatibles;
autocompatibilidad del writer nuevo no demuestra compatibilidad histórica.
No aprobar el contrato por agregar un campo `version` o un receipt económico.
El modelo de todos los prefijos y su gate material deben quedar ligados al
mismo codec/candidato y al schedule completo. Esta revisión no declara G5 GREEN.

El planner compara el costo lógico y la asignación física real de cada plan
contra su baseline del mismo catálogo y rechaza cualquier alternativa que
empeore cualquiera de esas dos métricas. Lee/authentica cada pack una sola vez
por pasada, valida el presupuesto de expansión antes de materializar y usa
vistas inmutables para derivar slices sin duplicar el parent completo. Los
límites de componentes, referencias, miembros y packs permanecen intactos.
Conservar un slice conserva su pack padre entero: GC no puede borrar bytes
necesarios para verificar el hash completo. Este criterio por corte no demuestra
una cota global para el catálogo contrafactual de los 1.202 ciclos originales.

La regresión económica de los 1.202 prefijos usa una fixture sintética de
32 KiB. Mide 49.233.920 B de nuevas asignaciones en baseline frente a
14.815.232 B en el plan elegido (69,91% menos); el directorio real seleccionado
ocupa 15.056.896 B. No son datos financieros Horizon ni un peak G5. La fixture
nativa privada de los cinco miembros originales recupera bytes exactos por la
API pública, con custody sin cambios y una cadena mixta V3/V4. El lector exacto
de #481 rechaza V4 con `RETENTION_RECIPE_HEADER_INVALID`; por eso la revisión
de lectores >=4 es un bloqueo real. La selección automática V4 en el universo
financiero original, el peak global bajo 512 MiB, GC/crash y la retención
9 h + 1 h completas siguen **NO_VERIFICADO**.

## Producción acotada

El Droplet permanece en 1 CPU, 1 GiB RAM y 25 GB de disco. El contrato de
compatibilidad debe medir cargas representativas bajo límites reales de CPU,
RAM y almacenamiento en CI, impedir rutas reales y conservar linaje de datos.
Registrar cgroup/limites aplicados, memoria máxima/OOM, CPU y tiempo, bytes e
inodos máximos y evidencias semánticas. El límite de discos medido se separa de
la garantía nominal del host. Ni el éxito de cuatro CPU ni un RLIMIT sin
confinamiento de descendientes prueba el límite productivo. Después, G8 verifica
el artefacto inmutable exacto; antes de promoción se exige medición read-only
actual de producción y su reserva según policy, sin ampliar recursos.

El job aislado `product-resource-compatibility` prepara nueve identidades
originales de consumidores PAPER/concurrencia/recuperación. Exige cgroup v2
inmutable y observado en supervisor/worker: `cpu.max` <= 1 CPU, affinity de una
CPU, `memory.max` <= 1 GiB, swap=0 y pids<=64. El almacenamiento de trabajo
agregado es un mount tmpfs propio <=256 MiB / 65.536 inodos, incluido dentro
de ese mismo presupuesto de RAM. Source y **todos** los mounts ajenos deben
ser readonly; se rechazan propagación compartida, migración a otro cgroup,
saved/filesystem UID/GID privilegiados, grupos suplementarios, capabilities
residuales y cambios de topology/namespace. FIN/capture/cleanup originales
permanecen obligatorios.

El launcher/filtro nativo del proceso actual todavía no demuestra las reglas
efectivas que impiden `mount/unshare/setns` y la creación de namespaces por
`clone`. `Seccomp: 2` sólo informa el modo, no esas reglas; tampoco las demuestra
un G0 de otro namespace. Por ello **execute y worker bloquean antes de fixtures
o consumidores**, incluso con CPU/RAM/filesystem observados conformes. No hay
un flag para levantar ese bloqueo. Hace falta integrar y probar el filtro en
el launcher actual, con evidencia de instalación y denegación en esos procesos.
La readiness preserva el motivo concreto y nunca equivale a compatibilidad
demostrada. El tmpfs no prueba durabilidad/rendimiento del disco persistente ni
que el sistema operativo y servicios vecinos quepan en 1 GiB; ese margen se
contrasta con la medición read-only del host y el artefacto exacto antes de G8.

## Evidencia económica y costo

[ECONOMIC_VALIDATION.json](ECONOMIC_VALIDATION.json) conserva hashes, counts,
origen, alcance y errores previos. El plan de 59 suites preserva las 56 de #481
y sus 14 dependencias exactas; agrega CAS, contrato y recursos. En el checkpoint
completo pasó 976 casos sin failures/errors/skips en CPython 3.11.16 y 3.12.14.
Las dos guardas posteriores se verifican por regresiones de sus ámbitos en
ambas versiones, registradas por separado; no se atribuyen a un run hospedado
ni se sustituye el ambiente Product157 por este ambiente económico.

Los primeros RED locales se conservaron. Las causas fueron un directorio de
pruebas en otro filesystem y modos no canónicos del checkout mutable. Se
prepararon parent corto en el mismo filesystem y modos del índice Git antes
de congelar Source; no se corrigieron receipts ni snapshots calificados.

No se contrató, provisionó ni amplió nada. El diseño sólo usa runner público
estándar y jobs efímeros; **USD 0 de costo recurrente adicional**. Los minutos y
artifacts consumen los recursos de Actions existentes y deben retener los
límites y política de custodia: duplicar RAW indefinidamente no es un ahorro.
Las regresiones de esta revisión son locales; no se lanzó ningún gate heavy.

## Camino finito y condiciones de cierre

1. Congelar un candidato reconciliado y confirmar ownership disjunto. Validar
   guardas y regresiones económicas duales; ningún heavy empieza en este paso.
2. Ejecutar observación read-only del runner público real. Si no cumple un
   requisito físico, informar el número exacto, bytes/FS/inodos/errno, ámbito y
   SHA/attempt; no concluir imposibilidad general por los 14 GB.
3. Desarrollar y probar custodia ROOT/NONROOT antes del primer actor, conservando
   native manager y límites. El hold no se levanta por metadata ni permisos sudo.
4. Probar capacidad nativa y costos completos del bootstrap legacy en el runner
   observado, o demostrar/revisar la alternativa direct-quota conservadora. Si
   direct quota no puede garantizar reserva/custodia, mantenerla bloqueada.
5. Revisar dispatch/lector >=4 y envelope hash-linked del CAS; probar modelo de
   los 1.202 prefijos y compatibilidad exacta sin simular retención reducida.
6. Ejecutar el DAG G0→G6, gates sólo con predecesores auténticos y cada productor
   con preflight vivo. Validar además el perfil productivo 1 CPU/1 GiB/disco
   acotado; failures requieren fix/guard, no retries idénticos.
7. G7 build once y G8 sobre el mismo artefacto/digest, coherentes con el candidato.
   Preparar medición read-only de producción. Cualquier pendiente mantiene
   bloqueada promoción; sólo al cerrar estos requisitos puede someterse una
   promoción concreta a la autorización separada del propietario.

Ningún paso requiere infraestructura de pago. Los bloqueos que quedan son
custodia privilegiada, reserva/cotas y enforcement nativos, revisión de formato
CAS, calificación material completa y compatibilidad del artefacto. No se
declara que sean físicamente imposibles en GitHub Actions gratuito: todavía
requieren implementación y evidencia discriminante.
