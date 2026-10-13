# Decisión del propietario: certificación con costo recurrente cero

El 2026-10-10 el propietario rechazó infraestructura adicional de USD 84/mes y
la ampliación del Droplet. Esa recomendación anterior queda retirada. El perfil
de certificación sigue siendo el runner público canónico `ubuntu-24.04`; no se
provisiona infraestructura, no se cambia producción y no se autorizan merge,
deploy ni heavy mientras exista un bloqueo de admisión.

El candidato conserva el trabajo del PR #481, base
`aee4261c58d63ca1cec54c32719878affe1d0f4f`. La revisión y las regresiones económicas
no convierten un gate pendiente en GREEN. PAPER/SHADOW ONLY,
`real_orders_sent=0`, PPI Watch intacto y FIX-FORWARD ONLY siguen obligatorios.

## Garantía nominal y capacidad de una ejecución

Los **14 GB nominales** documentados por GitHub son información del perfil y no
un techo físico por ejecución. GB decimal equivale aquí a 14.000.000.000 bytes;
no se lo transforma en 14 GiB. La garantía no asegura que el contrato actual
quepa, pero tampoco prueba que sea imposible usar runners gratuitos. La decisión
se toma sobre `f_bavail`, filesystem, unidad de asignación, dispositivo, mount,
inodos, cuota y custodia observados en **esa ejecución**, con recheck vivo justo
antes de cada productor. Un runner con espacio suficiente puede continuar hasta
la siguiente admisión; ni ese espacio ni un receipt nominal conceden seguridad.

Referencia del perfil, cuya disponibilidad debe revalidarse:
[GitHub-hosted runners](https://docs.github.com/actions/reference/runners/github-hosted-runners).
Los runners estándar de repositorios públicos no requieren contratar un runner
persistente. La revisión del workflow debe conservar el repositorio público y
el perfil estándar; cambiar a un perfil facturado requiere otra autorización.

**Capacidad física verificada de una ejecución gratuita:** el run
[37988736476](https://github.com/mbalbo2023/Porota-trading/actions/runs/37988736476),
attempt1, job114017192418, label `ubuntu-24.04`, ejecutó el candidato #481 exacto.
Su `runner-readonly.json` original, observado el2026-10-09T20:42:27Z, registró
ext4, bloque4096B, mount27, **91.698.405.376B libres** y18.423.046 inodos libres
de19.529.728. Ese valor supera el piso legacy32.596.295.680B. El ZIP original
11644630741 fue descargado, verificado por SHA256
`dd858cb57c7b83153c17a5b7514690e35a6447f234622c65004ad9cf3f863ea9`
y CRC de sus16 miembros, sin modificarlo. El miembro readonly tiene SHA256
`b86f596f2186fcf2ea695041892e458be852c445e3ef483028d75b91c4afc958`.

Esto refuta la exclusión de runners gratuitos basada únicamente en14GB. No
autoriza otra ejecución ni demuestra su cuota/custodia o pico compuesto:
el mismo control dejó `G0_status=BLOQUEADO`, ROOT no probado y falta de
`QFMT_VFS_V1_MODULE_NOT_ALREADY_LIVE`. La prioridad es cerrar custodia/readiness
y medir el runner gratuito siguiente; la alternativa de quota directa queda
opcional y bloqueada hasta su demostración, sin compras ni limpieza ajena.

## Qué exige realmente el bootstrap existente

`evaluate_bootstrap_storage` conserva estos valores históricos y muestra el
piso necesario, todavía distinto del grafo completo:

| Obligación exterior simultánea | Bytes |
|---|---:|
| Backing físicamente preasignado de 26 GiB | 27.917.287.424 |
| Reserva exterior de 4 GiB | 4.294.967.296 |
| Controles exteriores acotados, bloque de 4096 B | 384.040.960 |
| Piso exterior actual | **32.596.295.680** |

Los 26 GiB son el contenedor de un proyecto de cuota hard 20 GiB; el código
requiere **otros 4 GiB dentro** de ese contenedor, más el proyecto EDQUOT de
1 MiB y metadata del filesystem. Por eso reemplazar sin revisión 26 GiB por
20 GiB eliminaría una reserva interior. No existe en los archivos revisados una
derivación que pruebe que 20 GiB sean el mínimo del producto. Son un envelope
del harness que instala dos entornos Product157, conserva seed/fullGit y dos
fullSource, y permite temporales/RAW/recuperación bajo cuota agregada. La cuota
es un control real; el tamaño fijo del contenedor y las duplicaciones son
decisiones de implementación de la certificación. Ningún valor histórico se
reduce en esta propuesta.

## Alternativa desarrollada y límites de su evidencia

El contrato propuesto usa project quota **nativa sobre un filesystem efímero ya
existente** cuando sus capacidades sean auténticamente comprobadas. Evita crear
y formatear un loop de 26 GiB. Conserva cuota 20 GiB, límite de 100.000 inodos,
proyecto EDQUOT original y, conservadoramente, **las dos reservas de 4 GiB**.
Su piso aritmético es:

`20 GiB + 1 MiB probe + 8 GiB reservas + controles + metadata incremental acotada`.

El ahorro máximo frente al loop es `2 GiB - 1 MiB - metadata incremental`.
Fusionar las reservas o bajar la calibración necesitaría una revisión adicional
y pruebas; esos ahorros no se contabilizan. Usar sólo el máximo de bytes
efectivamente escritos exige asimismo demostrar toda la reserva física y los
escritores externos. Una quota hard no reserva espacio ni protege de ENOSPC
causado por otro escritor. La alternativa permanece **PROPUESTO**, con
equivalencia **NO_VERIFICADO**; no es un nuevo camino de admisión ejecutable.

`readonly_runner_observation` incorpora `FSGETXATTR`, readbacks exclusivos
`quotactl_fd/Q_GETFMT` y `Q_GETQUOTA` cuando libc/kernel los permiten, flags de
proyecto, mount/dev/inodo y UID/GID/capabilities/namespaces del observador. No
asigna proyectos, no cambia cuotas, no inicia ROOT y no monta nada. Rechaza
statfs de un directorio con vista proyectada por cuota como capacidad física.
Un syscall ausente o denegado queda `NO_VERIFICADO`; no significa que todo
GitHub-hosted sea incompatible.

Siguen intactos `PRIVILEGED_SIGNAL_CUSTODY` y el native manager SHA256
`55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8`.
El bloqueo actual es custodia ROOT desde la primera instrucción, además de
cuota/costos compuestos y capacidad viva del próximo productor no demostrados.
El piso de almacenamiento sí cabía en el runner original verificado. Comprar disco no corrige ese
bloqueo. La aceptación del contrato, obligaciones equivalentes y secuencia
finita se detallan en [ZERO_COST_CONTRACT.md](ZERO_COST_CONTRACT.md).

## Compatibilidad con el Droplet

La infraestructura productiva actual permanece en 1 vCPU, 1 GiB de RAM y
25 GB de disco. Hace falta probar cargas representativas bajo esos límites en
CI y medir capacidad real antes de promoción. Un PASS en cuatro CPU no prueba
esa compatibilidad. Los límites históricos BIG/Horizon continúan siendo límites
de certificación; no se trasladan como requisitos de infraestructura productiva.
Toda promoción futura requiere el mismo artefacto inmutable aprobado y evidencia
del host actual; no se reutiliza una medición histórica de otro candidato.
