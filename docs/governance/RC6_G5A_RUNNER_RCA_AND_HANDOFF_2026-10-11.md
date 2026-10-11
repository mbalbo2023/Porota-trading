# RC6 G5A ROOT/cuotas/FIN: RCA y handoff de desarrollo

Alcance: PR #483, `WS-RC6-G5A-ROOTQUOTA-RUNNER-CLOSURE`. PAPER/SHADOW ONLY.
Este documento es desarrollo y auditoría; no concede operaciones ROOT, cuotas
mutantes, EDQUOT, G0–G8 materiales, merge, deploy ni escritura V4.
`G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED` sigue BLOQUEADO.

## Identidad y hechos originales

| Evidencia | Hecho |
|---|---|
| PR | #483 OPEN/DRAFT; base `fix/rc6-zero-cost-certification-20261010` @ `2bf97dde54b4f960e105d2397d9b80012ba97aab` |
| Source original | `afe3ba3a61b43e63bb7d61b7662bc3bb2a830657`, tree `fb9279f5fd96ac08e5ba610bc66cd9a879a7edf2` |
| Producto | `deploy/rc6-pr69-isolated-20260915` @ `da697c6e6c2274579f9e4a112fabc4327475dd35`; no deploy de este PR |
| [Run original](https://github.com/mbalbo2023/Porota-trading/actions/runs/38105317601) | attempt 1, `PASS_DEVELOPMENT_ONLY`; desarrollo 3.264 casos por Python 3.11/3.12, cero fallas/errores/skips; seis jobs no materiales o materiales restantes omitidos |
| [ZIP original](https://github.com/mbalbo2023/Porota-trading/actions/runs/38105317601/artifacts/11689980847) | 1.238.194 bytes, SHA256 `81c7e6d06b4c8ccbc47be6bab932b97af5e7241bbfd041a834b52c565d0b136a`; 31 miembros, CRC válidos |
| Diseño/gestor original | SHA256 `d284123f5a4d8133575793bd78fae411f42eb308e44eb6899ff5011ec06cc450` / `55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8`; bytes conservados |

La lectura directa de `g5a-native-feasibility.json` del ZIP muestra el kernel
`6.17.0-1022-azure`, namespace de montaje inode `4026531832`, ext4 RW,
mount ID `27`, dispositivo `/dev/root` (`st_dev=2049`) para Source, HOME,
RUNNER_TEMP y tool cache. La captura dispone de 91.480.403.968 bytes y
18.422.939 inodos libres de 19.529.728. La llamada exacta
`quotactl(Q_GETFMT, PRJQUOTA=2, /dev/root)` usa command `2147484674`,
retorna `-1`, `errno=3 ESRCH`. El observador es UID/GID 1001, CapEff=0.
`FSGETXATTR` del directorio observado da project ID 0, PROJINHERIT falso.
Ningún setup ROOT, `Q_SETQUOTA`, EDQUOT o prueba 20 GiB ocurrió.

## RCA de ESRCH: hecho, límite y discriminante

En [Linux v6.17 `fs/quota/quota.c`](https://github.com/torvalds/linux/blob/v6.17/fs/quota/quota.c),
`do_quotactl` rechaza antes con `ENOSYS` si no hay `s_qcop`, con `EINVAL` si
`s_quota_types` excluye PRJQUOTA, y ejecuta la comprobación de permiso antes
de `quota_getfmt`. Esta última devuelve `ESRCH` si
`!sb_has_quota_active(sb, type)` (líneas 106–113 del upstream). Por tanto,
**causa inmediata demostrada en la semántica upstream**: la cuota de proyecto
no estaba activa en el superblock que resolvió `/dev/root` durante esa llamada.
El kernel Azure exacto no fue comparado byte a byte con ese upstream; su
posible parche downstream sigue `NO_VERIFICADO`. El match de `st_rdev` y mount
ID en el artefacto reduce la hipótesis de dispositivo equivocado, sin probar
todo el namespace de host fuera de la captura.

`/boot/config-6.17.0-1022-azure` en el mismo artefacto registra
`CONFIG_QUOTA=y`, `CONFIG_QUOTACTL=y` y `CONFIG_EXT4_FS=y`: hay soporte general
compilado, no activación de PRJQUOTA. Las opciones de mount capturadas no
muestran `prjquota`; tampoco bastan por sí solas para probar los bits del
superblock. Faltan `EXT4_FEATURE_RO_COMPAT_QUOTA=0x0100` y
`EXT4_FEATURE_RO_COMPAT_PROJECT=0x2000` del superblock exacto y un readback de
estado de cuota sin mutación. Sus constantes provienen de
[e2fsprogs `ext2_fs.h`](https://github.com/tytso/e2fsprogs/blob/master/lib/ext2fs/ext2_fs.h).
`ESRCH` no distingue entre features ausentes, cuota desactivada y condiciones
de habilitación del montaje. No se atribuye a Azure, a todo ext4 ni a falta
universal de soporte del kernel.

La nueva lectura hace `open(O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK)` y
`pread` de 1024 bytes del superblock del block device autenticado contra
`st_dev`; registra bits o errno sin `sudo`, remount, `tune2fs`, `quotaon` ni
escrituras. Si el runner niega abrir el dispositivo, el resultado será
`NO_VERIFICADO` con el errno real. La siguiente CI de desarrollo de un SHA
nuevo **no repite Q_GETFMT**: conserva la referencia inmutable al primer
ESRCH y sólo busca ese discriminante nuevo. Un bit positivo no demuestra
cuota activa ni EDQUOT. Un bit requerido ausente demostraría que ese
filesystem precisa intervención material, fuera de esta orden.

`quotactl_fd` toma el superblock desde un FD, pero termina en el mismo
`do_quotactl` y `quota_getfmt` del upstream. Cambiar la referencia del
filesystem no activa una cuota. No se usará como bypass ni se repetirá
`Q_GETFMT` con condiciones idénticas.

**Clasificación del runner original:** `RUNNER_QUOTA_UNAVAILABLE_WITH_CAUSE`
para esa captura: PRJQUOTA inactiva en el superblock consultado. La razón
subyacente de la inactividad es `NO_VERIFICADO`. El mismo estado no se
extrapola a un runner futuro sin identidad y lecturas nuevas.

## Secuencia pre-checkout y límite del workflow actual

Cadena exigida: evento e identidad Source → bootstrap confiable → ROOT
cuotado → checkout, HOME, cache, temporales, Source y tooling bajo ese project
ID → NONROOT sellado → `preparation` → `reader-collection` →
`reader-execution` → FIN/readback ROOT de cada etapa.

El job vigente de `.github/workflows/rc6-unified-candidate-tests.yml` hace
`actions/checkout` antes de ejecutar código del candidato y luego
`actions/setup-python`, pip y uploaders. `TrustedQuotaSetup` está **dentro**
del checkout, por lo que no puede autenticar ni crear el proyecto antes de
ese primer escritor. En `scripts/rc6_g5a_actions.py`, el guard de
`prepare` devuelve `PROJECT_ROOT_NOT_ESTABLISHED` antes de Git. Declarar una
Source externa devuelve `SOURCE_REPOSITORY_OUTSIDE_PROJECT`. Los siete Git y
source-pin del padre aún no están atados a un gestor/cuota vivos. Éste es un
NO-GO demostrado de la topología actual, independiente de ESRCH.

La arquitectura propuesta exige un controlador de bootstrap ya fijado en un
ref revisado y confiable antes de que el candidato se ejecute. Ese control
debería autenticar `repository_id`, run/attempt, SHA/tree del evento frente a
GitHub y el digest del propio bootstrap, y verificar bytes de cada ejecutable
ROOT antes de usarlo. Sólo tras readback nativo del proyecto fresco podría
descargar y verificar el commit exacto, hacer checkout **dentro** del proyecto,
definir HOME, TMPDIR, XDG, tool cache, pip, compiladores y output bajo el
mismo project ID, y ejecutar el gestor original. Ningún JSON de checkout o
`prepared.json` concede custodia.

Todavía no hay prueba de que GitHub-hosted Actions permita controlar todos
los escritores del runner **antes del primer step**, incluidos sus logs,
`RUNNER_TEMP` y la inicialización de workspace/tool cache. Esa frontera es
`UNRESOLVED`. Una exclusión de esos escritores requeriría revisión contractual
expresa; no se cambia silenciosamente. El workflow actual tampoco tiene un
bootstrap ROOT autenticado previo al checkout. No se añade un step ROOT a
esta CI de desarrollo ni se modifica `main` para registrar uno.

## Inventario de procesos y escrituras

[Inventario CSV de Source](RC6_G5A_STATIC_WRITER_SITES_2026-10-11.csv):
30 sitios de proceso y 141 sitios de posible escritura en diez módulos,
con miembro/hash, función, línea, llamada y argumentos de Source. Cada fila
incluye los campos de padre/origen, destino, etapa, gestor, protección y
evidencia. La clase permanece `UNRESOLVED` cuando sólo existe AST. Un `open`
de lectura puede ser un falso positivo; una llamada indirecta puede faltar.
El CSV no es una traza runtime ni demuestra cierre de hijos, FDs o mounts.

| Origen/llamadas | Etapa y destino posible | Responsable y estado |
|---|---|---|
| `actions/checkout`, Git fetch/objects/packs, `setup-python`, pip de wheels, uploader | Antes de ROOT, workspace, tool cache, RUNNER_TEMP y caches | Actions; `UNRESOLVED`, escritores pre-cuota en workflow actual |
| `rc6_g5a_actions.exact_repo` → `original.git` dos veces | Preparation; checkout/.git | Padre NONROOT; `UNRESOLVED`, dos subprocess sin gestor |
| `rc6_material_carrier.fetch_originals` → `subprocess.run` y `git` | Preparation; `.git`, askpass, objetos, packs | Padre y gestor mezclados; `UNRESOLVED` hasta cerrar ambos |
| `complete_full_git`, fsck y export whole Source | Preparation; `.git`, Source export y RAW | Gestor original para ciertos hijos; `UNRESOLVED` por Source externa y writes indirectos |
| PRODUCT157, `msgpack`, `ppi-client`, `signalrcoreppi`, `ta`, pip/build backends/compiladores | Preparation; venv, HOME, TMP, XDG, caches, logs | Gestor original sólo para hijos conocidos; transitorios `UNRESOLVED` |
| `horizon.preflight` y cuatro módulos lectores originales | Reader collection/execution; control, logs, JUnit y RAW | Preflight ya usa gestor/FIN; Git/source-pin y writers transitivos `UNRESOLVED` |
| `governed.source_pin` dos veces, `inventory_from_source_pin` | Reader collection/execution; `.git` y metadatos | Padre NONROOT; `UNRESOLVED`, tres llamadas directas |
| Uploaders de controles y partes; logs/FDs heredados | Después de lectores y durante cada etapa | Actions/gestor; `UNRESOLVED`, cobertura de cuota y FIN no demostrada |

Los destinos por variables `HOME`, `TMPDIR`, `XDG_*`, `PIP_*`, `CARGO_HOME`,
`RUSTUP_HOME`, `CCACHE_DIR`, `PYTHONPYCACHEPREFIX`, Git config/alternates y
transportes alternos deben observarse en runtime. Symlinks, hardlinks,
bind mounts, mnt_id, project ID, inode rebinding y FDs abiertos/desvinculados
requieren custodia nativa. El AST sólo establece presencia de llamadas. El
guard `require_closed` nunca acepta una bandera documental
`all_writers_enclosed=true` como prueba runtime.

## ROOT, NONROOT, autoridad por etapa y FIN

El código de `rc6_g5a_aggregate_issuer` ya expresa límites 20 GiB/100.000
inodos, project ID separado 1 MiB, `FSGETXATTR`, `Q_GETFMT`, `Q_SETQUOTA`,
`Q_GETQUOTA`, reserva física 4 GiB, >=10% inodos libres, UID/GID reales,
efectivos y salvados, capabilities cero, NNP, seccomp y Landlock. Es
**implementación no ejecutada**, dependiente de ROOT y admisión material.
El gestor original conserva el digest citado arriba.

Para una etapa futura, la custodia ROOT deberá emitir en memoria un permiso
vivo consumible una vez, ligado a SHA/tree, recibo/lease del owner, run/attempt,
block device, mount namespace e ID, inode/project ID, etapa exacta,
gestor/PID/deadline y revocación. Orden único: preparation → reader-collection
→ reader-execution. Antes de cada lanzamiento: reautenticar owner/Source,
cuota, reserva y rutas; tras el FIN opaco real de proceso y descendientes:
readback ROOT de límites, uso, mount, project ID, inodo y reservas, enlazado
al mismo permiso en custodia viva. Timeout, replay, revocación, FIN parcial o
falso y cambio de identidad son RED con RAW preservado, sin cleanup prematuro.
No existe hoy esa custodia conectada antes del checkout ni tres pares FIN /
readback, por lo que no se fabrica un permiso en esta misión.

## Amenazas, guard y prueba pendiente

| Falla | Guard económico actual/nuevo | Prueba nativa pendiente |
|---|---|---|
| Pre-checkout fuera de cuota; Source externa | `PROJECT_ROOT_NOT_ESTABLISHED`, `PRE_QUOTA_ACTIONS_WRITERS`, `SOURCE_REPOSITORY_OUTSIDE_PROJECT` | bootstrap previo a todos los escritores cubiertos |
| Git/subprocess del padre, build transitivo, uploader | inventario Source + `G5A_RUNTIME_WRITER_COVERAGE_NOT_VERIFIED` | grafo/FD/destinos real bajo gestor y project ID |
| Cuota ausente, formato equivocado, readback ambiguo | ESRCH original preservado, lectura superblock, no repetición | `Q_GETFMT` tras condición nueva; pequeño control material autorizado |
| Project/mount/device/inode rebound, ID reutilizado | `trusted_setup` y `require_stage_topology` | ROOT pre/post FIN en mismo proyecto |
| Symlink/hardlink/bind mount, destino/caches externos | paths/FDs/entorno fail-closed | EACCES real y censo de todos los destinos |
| FD heredado abierto/desvinculado | verificación de FD regular y cierre extra; sin GREEN | EDQUOT real del pequeño control y, luego, del agregado |
| Owner falso/expirado/revocado, HEAD rebound | recibos inmutables/leases y Source pin; sin permiso serializable | revocación durante etapa y relectura de Source |
| Etapa fuera de orden, FIN falsificado/truncado, timeout | gestor original y barrera G5A | tres FIN/readbacks auténticos en custodia viva |
| Cleanup ajeno/prematuro; V4 | lifecycle fail-closed; writer V3 efectivo | FIN/captura antes de retiro propio; V4 requiere decisión nueva |

Los tests simulados verifican rechazos, no simulan GREEN nativo. El error
repetible sigue `ERROR → RCA → FIX → GUARD → TEST/CI/PREFLIGHT → EVIDENCIA`.

## Matriz de factibilidad y decisión

| Opción | Clasificación, equivalencia y coste | GO |
|---|---|---|
| ext4 `/dev/root` del run original tal como está | `INCOMPATIBLE_DEMOSTRADO` para G5A actual: PRJQUOTA inactiva por ESRCH y checkout antes de ROOT; no coste adicional, pero no enforcement | NO |
| Habilitar features/cuota en el mismo ext4 y mover bootstrap antes de Source | `REQUIERE_PERMISO_O_COSTO_NO_AUTORIZADO`: podría preservar filesystem original, pero precisa mutación ROOT/host prohibida y prueba de escritores del runner | NO |
| ext4 efímero privado/loop | `REQUIERE_CAMBIO_CONTRACTUAL`: nueva identidad de filesystem/mount para Source/HOME y backing; requiere loop/mkfs/mount materiales prohibidos; espacio físico simultáneo y coste sin demostrar | NO |
| Otro runner existente sin coste recurrente | `POTENCIALMENTE_COMPATIBLE_FALTA_PRUEBA`: necesita block device, project quota activa, bootstrap previo y aislamiento/FIN equivalentes; disponibilidad y coste `NO_VERIFICADO` | NO |
| Cgroup, polling, RLIMIT_FSIZE, Docker o conteo de árbol | `INCOMPATIBLE_DEMOSTRADO` como sustituto de límite kernel agregado y EDQUOT de FD abierto/desvinculado | NO |

**Recomendación única:** mantener el candidato en NO-GO material. Para una
admisión futura, exigir un runner/topología ya capaz de activar y autenticar
cuota ext4 antes del primer escritor contractual y revisar formalmente el
bootstrap de Actions, con coste real USD 0 adicional demostrado. Sólo después
de la decisión de infraestructura/contrato y recibos específicos correspondería
el control 1 MiB, y únicamente tras su PASS nativo una prueba 20 GiB. Esta
misión no ejecuta ninguna de esas operaciones.

## Handoff y semáforo

| Elemento | Estado y siguiente discriminante |
|---|---|
| RCA inmediato ESRCH | `DESARROLLADO`: PRJQUOTA inactiva en superblock observado por semántica Linux v6.17; causa de inactividad `NO_VERIFICADO` hasta bits ext4/readback exactos |
| Inventario | `DESARROLLADO`, 30 procesos/141 posibles escrituras Source; runtime `UNRESOLVED` |
| Bootstrap pre-checkout y tres etapas ROOT/FIN | `PROPUESTO`, `BLOQUEADO` por orden actual y falta de custodia nativa |
| G5A material/G5B/G0–G8 | `BLOQUEADO`, no autorizados ni ejecutados |
| Writer | V3 efectivo; V4 `BLOQUEADO` |
| Run que no repetir | `38105317601` sobre `afe3ba3a...`; primer ESRCH y PASS de desarrollo se conservan |

El primer gate restante es autenticar una topología que encierre checkout,
tooling, Source, lectores, logs, FDs y uploaders en una cuota kernel activa.
El discriminante seguro siguiente es la lectura de features del block device
exacto; si está prohibida por permisos, informar errno y solicitar una
observación ROOT **de sólo lectura** con bootstrap ya autenticado, nunca
convertir la denegación en GREEN. El estado del Droplet y `real_orders_sent`
runtime no se infieren de esta CI: no hubo acceso operativo al host.
