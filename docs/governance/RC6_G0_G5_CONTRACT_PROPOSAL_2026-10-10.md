# RC6: una propuesta contractual G0/G5 para PR #483

Estado: **DESARROLLADO; ACEPTACIÓN DEL PROPIETARIO PENDIENTE**. Base revisada:
`093c72f898074a76e6daea8f2fdc8ac84a69ecc3`, árbol
`bcfa48485184690ba5d0ad328c6c7fd69203a846`. El SHA/árbol del candidato final
se vinculan en los recibos inmutables y artifacts de Actions; este documento
no intenta incluir su propio hash Git. No hay merge, deploy ni aprobación
contractual automática. PAPER/SHADOW; `real_orders_sent=0`; rutas reales
bloqueadas/no llamadas; PPI Watch intacto; costo recurrente adicional USD 0.

## Decisión única solicitada, después de revisar código y pruebas

Aceptar la revisión **PRIVATE_PID_BOUNDARY_V2** y la división **G5A/G5B**,
incluido **V4 como escritor nativo del candidato V2** y la topología propia
**G5A_HOST_STAGE_AGGREGATE_20GIB_V1** descrita abajo,
con las obligaciones siguientes. La aceptación habilita solicitar ejecuciones
acotadas y autenticadas; no declara equivalencia, no convierte evidencia de
desarrollo en GREEN y no habilita G0–G8, merge o Deploy V2. La autorización
de ejecución conserva Source/árbol, propietario, intento, alcance, contrato
y etapa exactos, con custodia real y lease operativo vigente.

Los contratos canónicos son `scripts.rc6_private_pid_boundary.contract()`
y `scripts.rc6_g5_contract.CONTRACT`; sus SHA256 se calculan sobre el JSON
canónico completo, no sobre una explicación resumida. El lector
`rc6_contract_revisions.authenticate_revision` exige una decisión explícita,
inmutable y emparejada en Issues #471/#473 con el digest completo. No escribe
decisiones. JSON, variables de entorno, constructors públicos, decisiones
editadas, alcances distintos y aprobaciones caducadas no crean autoridad.
El origen `EXPLICIT_OWNER_DECISION` debe provenir de la decisión del usuario;
el agente no puede convertir esta propuesta en ese recibo por sí mismo.

## G0: propiedad preservada y demostración adversarial

La custodia ROOT certificada sigue usando el PID1 existente, una unidad
transitoria propia y el gestor nativo original byte a byte. Conserva éxito
ROOT, error antes del drop, hang ROOT y hang NONROOT, TERM 2 s, FIN 5 s,
wait4/ECHILD, cgroup propio, captura sólo después del FIN y retiro exclusivo.
La observación nueva es opcional; el contrato EACCES anterior sigue siendo
el comportamiento por defecto.

Se sustituye únicamente la dependencia
`issuer_root_escape_probe:/proc/HOST_PID/root:EACCES`. Un PID del host oculto
por private-PID puede devolver ENOENT sin representar EACCES. **ENOENT solo
no es prueba**. La revisión exige conjuntamente:

* guardián ROOT externo con pidfd, PID/birth/boot/cgroup/namespace del issuer
  vivos antes y después, desafíos con nonce distintos y ACK de kernel;
* un único procfs privado y de sólo lectura; namespace distinto del host;
  veto de colisión del número host con un PID local;
* ausencia de procfs host, submontajes/magiclinks, FDs de procfs, block devices
  o raíces privilegiadas heredados; dirfd propio autenticado y cerrado antes
  de NONROOT; cierre del bridge antes de entregar la carga;
* NONROOT real, grupos/capabilities cero, NNP, seccomp actual y denegaciones
  adversariales nativas sobre blancos existentes;
* rechazo de PID inexistente arbitrario, birth reciclado, colisión, namespace
  incorrecto, procfs host, magiclink, bridge incorrecto, FD privilegiado,
  recibo falsificado y guardián perdido. Los controles de parser no se
  presentan como ataques nativos ejecutados;
* conservación de la denegación local
  `quota_probe:/proc/LOCAL_SUPERVISOR/fd/1:EACCES` y de la custodia/FIN original.

Un `NativeBoundaryWitness` se obtiene de observaciones capturadas por la
custodia real, en el mismo proceso y con binding exacto. Un archivo de
evidencia serializado nunca puede reconstruir ese witness ni autorizar ROOT.
La observación previa de esta propuesta no consume su aprobación pendiente.

## Quota: TrustedQuotaSetup → Seal → NONROOT → Custody/FIN

El supervisor ROOT no hereda privilegios de preparación durante la carga.
El hijo fijo de preparación nace antes de sellar al supervisor y separa su
mount namespace; el supervisor verifica esa separación, sella montajes,
FDs/capabilities/seccomp y sólo entonces permite la preparación propia.
TrustedQuotaSetup aplica y lee de vuelta project ID/PROJINHERIT, formato
`Q_GETFMT`, límites `Q_GETQUOTA` y filesystem/mount/device reales. Después
cierra sus FDs privilegiados, instala las prohibiciones de mutación y baja a
NONROOT. El gestor original supervisa la carga y produce el FIN auténtico.

No se interpreta un módulo no observado como filesystem incompatible. Esa
observación se conserva como metadato; soporte se decide por syscalls,
readbacks y EDQUOT reales sobre el filesystem seleccionado. No se autoriza
carga explícita de módulos ni cambios de infraestructura globales.

Orden obligatorio: capability **5 GiB de backing / 512 MiB de project**;
aceptar sus bytes nativos, cuota efectiva, denegaciones, custodia/FIN y retiro;
después bootstrap **26 GiB / 20 GiB**. Continúan las reservas originales
de 4 GiB, control contabilizado, mismo filesystem y al menos 10 % de inodes
libres. Desconocidos, límites parciales, fracaso de montaje/readback o FIN
incompleto bloquean sin acreditar recursos recuperados.

Los leases operativos originales siguen acotados a 20 minutos. El lifecycle
propuesto `IMMUTABLE_DECISION_LIVE_OWNER_REVOCATION_V1` debe aceptarse
explícitamente en ambos recibos: aceptación tipada fresca antes de cada
launch; durante la carga, lectura de esa misma decisión inmutable, revocaciones
y leases operativos renovados, siempre sobre la misma Source/etapa. Después
del FIN real sólo se admite captura; ese recibo no autoriza otro launch.
Así se preserva el presupuesto original de bootstrap de hasta 10.800 s sin
ampliar leases, reutilizar una aprobación caducada para otro lanzamiento ni
fabricar nuevas decisiones del propietario.

## G5A: adquisición acotada, sin GREEN

Se elimina la circularidad sólo para una autoridad G5A autenticada. El hold
legacy `HORIZON_MODEL_CLOSURE` sigue rechazando llamadas ordinarias. G5A
necesita aceptación de la revisión, G0–G4 nativos autenticados, capability
5/512 y bootstrap26/20 aceptados y autenticados en ese orden, contexto
ROOT de custodia actual, quota de project real y NONROOT sellado. El writer
recibe una autoridad viva en el mismo proceso; no hay `--force`, flag PASS
ni reconstrucción desde JSON a través de subprocess.

La admisión inicial G5A no exige el modelo completo que precisamente falta
obtener. Su recibo sólo enumera obligaciones pendientes: antes de crear la
imagen, el carrier verifica G0–G4, ambos artifacts de capacidad y el espacio
físico simultáneo real; el worker ROOT repite la autenticación antes de quota.
Ninguna llamada ordinaria puede consumir ese recibo como capacidad completa
ni como autorización nativa. El contrato aceptado, los handles nativos y las
reservas por escritura siguen siendo barreras independientes.

Se conservan exactamente **1.202 cortes/ciclos originales**, dos catálogos,
cinco payloads exactos por corte, **512 MiB**, profundidad **32**, retención
**9 h + 1 h** y sus datos originales. Antes de cada publicación, ACK, GC o
recuperación se reserva el máximo simultáneo de packs completos, recipes,
receipts, controles, temporales, metadatos y demás allocations propias.
Se compara con allocations físicas actuales, inodes y quota. Futuro GC
acredita **0 bytes**. Costos concurrentes desconocidos, falta de envelope
autenticado o insuficiencia producen un RED discriminante antes de escribir.

La prueba nativa de enforcement usa un project pequeño separado en el mismo
filesystem, EDQUOT efectivo y denegación de reasignación/mutación después
del drop. Ese control no sustituye el ledger completo del archivo de 512 MiB.
Un recibo G5A sólo prueba adquisición o su bloqueo; nunca certifica G5.

El lease operativo sigue durando como máximo20min. La continuación G5A
conserva sólo la decisión inicial inmutable para la misma operación nativa:
pulsos Source/owner/OPS de API cada60s, observación local cada5s, revocaciones
y vencimientos bloquean nuevas escrituras. El worker sólo lee el directorio
propio montado de sólo lectura, después de cerrar el oracle ROOT; no retiene
bearer, cliente HTTP ni autoridad reconstruida desde JSON. Un pulso viejo,
editado, reordenado o perteneciente a otro nacimiento/Source no autoriza nada.

La fase inicial contabiliza imagen26GiB y hasta20GiB de RAW más controles,
reservas y metadatos; no presupone su copia simultánea. Después de ambos FIN,
verifica íntegramente el stream fijado, demuestra que el loop AUTOCLEAR
original desapareció y retira sólo su propia imagen por dev/inode autenticado.
Mide entonces el espacio real y admite la copia con las reservas originales.
Un FIN desconocido, stream truncado o loop activo conserva la imagen. No
desconecta loops, toca recursos ajenos ni acredita un borrado futuro.

## G5B: stream completo y escritor del artifact

### Barrera que necesita esta decisión del propietario

La cuota privada del escritor no cubre las instalaciones PRODUCT157, sus cuatro
builds de fuentes ni los lectores que corren después de ambos FIN. Un census de
árbol cada 5 segundos tampoco cubre bursts entre muestras ni archivos abiertos
ya desvinculados. La regresión NONROOT crea realmente ese archivo propio y
demuestra que su asignación desaparece del árbol antes de cerrar su FD; no
simula una cuota ni aporta una certificación nativa.

La propuesta única amplía la **cobertura** del límite original, sin aumentarlo:
instalador completo, subprocesses/builds, temporales, exportaciones Git/Source y
los cuatro módulos lectores originales deben vivir en un filesystem propio con
**límite agregado real de 20 GiB**, readback/EDQUOT reales y denegaciones después
del sellado. Conserva filesystem original exigido por las fixtures, ruta máxima
50 caracteres, UID/NONROOT, FIN del gestor original, reserva 4 GiB y 10 % de
inodes. Se debe demostrar el cobro de archivos abiertos desvinculados y de todos
los escritores antes de habilitar preparación o lectores. No habilita montajes
del host, PPI Watch ni recursos ajenos.

`rc6_g5a_host_capacity.require_host_stage_admission` rechaza hoy esas tres etapas
con **G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED**. No hay todavía un
emisor nativo de esta prueba: aprobar el contrato no elimina ese rechazo. La
implementación y prueba de la topología aceptada serán obligatorias antes de
G5A; polling, JSON de aprobación, RLIMIT por archivo y la cuota privada del
escritor no sustituyen ese emisor. Esta es una barrera precisa de cobertura de
seguridad, no una declaración de incompatibilidad del kernel.

La certificación exige los 1.202 cortes completos y sus cinco bytes originales,
ledger físico simultáneo, límite/depth/retención, ACK, GC por pack completo,
restore, pruebas de corrupción/interrupción/recovery y FIN/custodia auténticos.
Un stream parcial, metadata de 1.049 cortes o evidencia de desarrollo privada
siguen bloqueados. La reducción sintética histórica **69,91 %** no aparece
en fórmulas de admisión, predicción ni aceptación.

El candidato propone `ComponentArchive.build → build_v4` y el formato
`COMPONENT_V4` en la fábrica/dispatch nativos. G5A llama a ese mismo build
sobre la generación original; la reproducción V3 usa `build_v3` explícito.
Los readers V3/V4 verifican dependencias, tipos, hashes,
bytes y restauración antes del ACK; GC mantiene dependencias transitivas y
retira packs completos. La aceptación del cambio de escritor y su prueba
nativa completa siguen pendientes; este desarrollo no modifica productiva.
`archive_format_contract` liga los bytes completos y entrypoints reales del
writer, planner, codecs, readers, ACK, GC y restorer con los mismos miembros
del futuro artifact inmutable. Hashes iguales prueban correspondencia de
bytes; las pruebas nativas completas siguen siendo una obligación separada.

## Release y evidencia

Se mantiene un solo candidato en PR #483. Primero CI económica exacta en
3.11.16/3.12.14; después observaciones nativas positivas. Tras esa CI se
ejecutan en paralelo la auditoría READ_ONLY real del Droplet por Actions y
la observación del candidato bajo cgroup **1 CPU / 1 GiB / swap 0 / 64 PIDs**
dentro de la custodia ROOT certificada. La observación de funciones originales
sin fixtures/ticks no certifica el perfil representativo BIG/Horizon ni el
host completo; el perfil físico V2 conserva los consumidores originales de
riesgo/salidas/EOD y requiere admisión material aparte.

La auditoría del host obtiene CPU/RAM/swap, filesystems/inodes, cgroups,
identidades de containers, PPI/DB/WAL/SHM y estado PAPER de sólo lectura.
No modifica containers, servicios, bases, archivos remotos ni configuración.
El lector SQLite usa sandbox de denegación de escritura antes de abrir la
conexión: `mode=ro` solo no basta ante SHM/WAL. Credenciales o stderr SSH
privados no se incluyen en el artifact; fallos emiten un discriminante cerrado.

G0–G8 siguen secuenciales, únicamente con admisión autenticada y una sola
Source. Se construye un Deploy V2 una vez; G7 prueba ese tar exacto, G8
verifica el alcance y sólo después se solicita autorización final separada.
Cada RED real conserva primer error, RCA, fix-forward, guard, regresión y
evidencia original. Un intento idéntico se rechaza; no se reescribe un RED
histórico ni se atribuye un resultado nativo a fixtures unitarias.
