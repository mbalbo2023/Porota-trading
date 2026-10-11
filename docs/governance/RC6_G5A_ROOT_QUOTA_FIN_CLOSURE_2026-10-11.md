# RC6 G5A: ROOT, cuota agregada y FIN — contrato de cierre técnico

Estado de este documento: **NO-GO material**. Es una auditoría y un plan de
admisión sobre PR #483. La aceptación del diseño
`RC6_G0_G5_CONTRACT_PROPOSAL_2026-10-10.md` sigue vinculada a su SHA256
`d284123f5a4d8133575793bd78fae411f42eb308e44eb6899ff5011ec06cc450`.
No es aprobación de una operación ROOT ni certificación de G5A/G5B. El guard
`G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED` permanece BLOQUEADO.

## Primer bloqueo preservado

`scripts/rc6_g5a_writer_inventory.py` recorre por AST los diez módulos de la
ruta, registra cada sitio de proceso junto con el hash del archivo y exige
Source y destinos de escritura dentro del mismo proyecto. En el workflow
actual, `prepare` recibe un proyecto sin establecer, por lo que el primer
error es `PROJECT_ROOT_NOT_ESTABLISHED`, antes de su primer subprocess Git.
Con un path declarado externo a Source, el siguiente error es
`SOURCE_REPOSITORY_OUTSIDE_PROJECT`. Además, el workflow ejecuta
`actions/checkout` y `actions/setup-python` antes de cualquier setup ROOT.
No es correcto marcar ese checkout como cubierto por una cuota creada después.
El desarrollo no reemplaza esos hechos con JSON, polling ni otro mecanismo.

## Inventario de escritores y procesos

| Etapa | Escritor/proceso real | Destino y estado actual |
|---|---|---|
| Antes de preparation | Actions checkout, setup-python y herramientas del runner | Checkout, cache y tooling previos a ROOT. NO-GO para cobertura agregada. |
| Preparation | `exact_repo`/`original.git`, `complete_full_git`, `fetch_originals`/`subprocess.run`, `git fsck`, exportador whole Source | `complete_full_git` usa el gestor para sus comandos, pero Git aún puede modificar la Source del checkout fuera del proyecto. `fetch_originals` también consulta Git directamente en el padre. NO-GO. |
| Preparation | Venv PRODUCT311, pip Product157, sdists de msgpack/ppi-client/signalrcoreppi/ta, compiladores, temporales, logs/FIN | El wrapper sellado y `stage_environment` conducen HOME, TMP, XDG, pip y caches al proyecto; sólo vale tras setup ROOT, readback y el cierre de todos los procesos previos. |
| Preparation | Descarga/verificación de RAW previos y publicación de controles | Los controles se ubican bajo la raíz propuesta; hay que comprobar project ID, mount ID e inodo antes de escribir. |
| Reader collection | `governed.source_pin` y Git internos, Product157 preflight, `rc6_material_focal.py`, archivos de observación | Product157 preflight ya pasa por gestor original, wrapper y FIN. Los Git directos del padre siguen sin sello. NO-GO. |
| Reader execution | Nuevo source pin, `inventory_from_source_pin` y Git, focal, JUnit, ACK/review, RAW | Focal pasa por gestor original; el inventario/source pin del padre sigue sin sello. NO-GO. |
| Después de readers | Source family, partes, uploader de Actions | Controles/partes deben permanecer dentro del proyecto; los procesos del uploader necesitan inventario y delimitación explícitos. Sin prueba de cobertura. |

`rc6_g5a_native_feasibility.observe` examina read-only los mount IDs de
checkout, HOME, RUNNER_TEMP y tool cache, el dispositivo de bloque real, ext4
RW y `Q_GETFMT` si puede abrir exactamente el dispositivo. Ext4, bytes libres
y un `/sys/module` ausente nunca demuestran enforcement. Un `Q_GETFMT` positivo
permite sólo considerar un control nativo pequeño tras autorización separada.
`quotactl_fd` puede investigarse como alternativa técnica, pero no omite
Source, owner, cuota, EDQUOT, EACCES, FIN ni reserva contractual.

## Autoridad provisional por etapa: contrato exacto

La autoridad provisional sólo permite **recoger evidencia de una etapa**.
Debe ser un objeto vivo, no reconstruible desde `prepared.json`, y existir
sólo en la custodia original tras todos estos controles:

1. Recibo inmutable de material para ese SHA/tree/Source, WRITE_OWNER vigente,
   aceptación de arquitectura vinculada al documento exacto y presupuesto
   de recursos. Esta instrucción de desarrollo no aporta ese recibo.
2. Guardián ROOT y gestor original, namespace de montaje privado, proyecto
   ext4 fresco y exclusivo. `TrustedQuotaSetup` asigna 20 GiB/100.000 inodos
   y control de 1 MiB con project ID distinto; autentica `Q_GETFMT`,
   `FSGETXATTR`, `Q_SETQUOTA`/`Q_GETQUOTA`, mount ID, inodo y block device.
   Antes de `Q_SETQUOTA`, ambos IDs deben carecer de uso/límites previos.
3. Source exacta y su árbol, owner y proyecto enlazados en la custodia ROOT;
   reserva física inicial de 4 GiB y al menos 10 % de inodos libres. Un
   recibo escrito por el worker no sustituye el FIN opaco del gestor.
4. Después de `Seal`, NONROOT con UID/GID reales, capabilities cero,
   NoNewPrivs, seccomp y Landlock. Cerrar FDs adicionales, validar stdio
   regular dentro del proyecto, comprobar EACCES real fuera, y rechazar
   symlinks/mounts/project IDs/inodos cambiados. El proyecto cubre archivos
   abiertos y desvinculados; medir directorios no los cubre.
5. Antes de cada etapa: owner/Source renovados, cuota kernel y reservas leídas
   de nuevo por el custodio ROOT; conjunto cerrado de procesos/destinos;
   permiso consumible sólo para `preparation`, `reader-collection` o
   `reader-execution`, en ese orden. El gestor original emite el FIN opaco
   sobre el mismo comando sellado. Después del FIN, ROOT lee de nuevo cuota,
   ID/mount/inodo y reservas; ambos FIN quedan enlazados en memoria con el
   stage y SHA/tree. Revocación, timeout o interrupción destruye el permiso
   sin borrar RAW ni FIN.

La **certificación final** requiere los tres pares FIN/readback, el control
EDQUOT pequeño, EDQUOT real del proyecto agregado de 20 GiB sobre FD abierto
y desvinculado, EACCES fuera, ausencia de escritores externos y conservación
de Source/RAW/FIN. Debe ser una decisión separada e inmutable del propietario
en el artefacto exacto. `require_host_stage_admission` sigue bloqueando la
certificación; usarlo como requisito de preparation sería circular. La ruta
actual todavía no tiene un custodio ROOT vivo y autenticado antes del primer
checkout, ni ese enlace de FIN/readback entre procesos. Por eso ningún
permiso provisional se emite en este SHA.

## Amenazas y regresiones

| Ataque/falla | Guard económico | Prueba nativa aún requerida |
|---|---|---|
| Path externo/symlink/reemplazo de inodo o project ID | `require_stage_topology`, inventario AST y tests de rebinding | EACCES, mount/inode/project ID antes y después de cada FIN |
| FD heredado, incluido archivo abierto y desvinculado | Verificación de fd 0–2 y cierre de fd >2 en wrapper | EDQUOT sobre FD abierto y desvinculado; denegación con FD externo |
| Mount nuevo/overlay/bind o block device ajeno | Mount ID y dispositivo exactos, preflight read-only | Negativa real de mount y readback ROOT |
| Cuota alterada, ID reutilizado o reserva agotada | ROOT rechaza IDs con uso/límites y relee 20 GiB/100k, 4 GiB/10 % | Q_GETQUOTA y reserva tras cada FIN |
| Owner revocado, Source sustituida, FIN falso | Decisión API y gestor original; ninguna estructura serializada concede autoridad | Revocación durante etapa y doble FIN original |
| ROOT/NONROOT fallan o se interrumpen | Error preservado, etapa no certificada, cleanup propio post FIN | FIN/RAW de fracaso y ausencia de cleanup prematuro |
| V4 escribe antes de aprobación | `ComponentArchive.build` efectivo V3 y `build_v4`/publicación V4 bloqueadas | Artefacto futuro exacto, aprobación y G5B propios |

Los tests económicos prueban rechazo de entrada y semántica de guard. No
simulan un PASS de kernel. La preparación actual queda detenida antes de Git.

## Admisión material mínima, en orden

1. Una única CI de desarrollo del SHA consolidado, con recibos frescos, sin
   repetir runs GREEN/RED anteriores. Leer RAW, FIN, hashes y el JSON read-only
   de factibilidad del **runner de esa ejecución**. Estado máximo:
   `PASS_DEVELOPMENT_ONLY`.
2. **NO-GO** si Source/checkouts/tooling o un proceso del inventario siguen
   fuera del proyecto; si ext4 RW, block device exacto o `Q_GETFMT` no se
   comprueban; o si el runner no ofrece reserva inicial. Registrar el primer
   error nativo sin probar otro mecanismo ni solicitar 20 GiB.
3. Sólo tras corregir el grafo y con autorización material separada: control
   ROOT pequeño en un proyecto fresco de la misma topología, sellado NONROOT,
   EDQUOT 1 MiB, EACCES y FIN originales. Si falla, cerrar el error con RCA.
4. Sólo con ese control positivo y nueva admisión de recursos: una prueba
   agregada de 20 GiB sobre el mismo SHA/Source en las tres etapas, lectura
   ROOT tras cada FIN, EDQUOT real 20 GiB y reserva física/inodos intactos.
5. Sólo entonces evaluar G5A y G5B; G0 y los demás gates conservan sus
   pruebas propias. Ninguna prueba de desarrollo, control pequeño o documento
   habilita `G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED`.

El futuro artefacto puede certificar el escritor **V3 efectivo** y la
capacidad de lectura V3/V4 sin activar escritura V4. V4 exige posteriormente
una decisión nueva, G5B nativo completo y el artefacto exacto; no se usa una
pretendida certificación V4 como prerrequisito de G5A. Permanecen 1.202 ciclos
y cinco payloads, archivo 512 MiB, depth32, retención 9h+1h, capability
5 GiB/512 MiB, bootstrap 26 GiB/20 GiB, PAPER/SHADOW ONLY,
`real_orders_sent=0` y costo recurrente adicional USD 0.
