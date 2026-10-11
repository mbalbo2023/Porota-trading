# RC6 G5A: estado del emisor agregado

La aceptación del propietario se refiere al diseño de
`RC6_G0_G5_CONTRACT_PROPOSAL_2026-10-10.md` (SHA256
`d284123f5a4d8133575793bd78fae411f42eb308e44eb6899ff5011ec06cc450`).
No certifica un host ni autoriza un lanzamiento material. Ese documento queda
inalterado para conservar la referencia de la decisión.

## Escritor efectivo

El escritor nativo de release y el dispatch de `ShadowRuntime` permanecen en
`COMPONENT_V3`. `COMPONENT_V4` es un candidato de arquitectura con comparación
económica privada. `SLICE_WRITE_CAPABILITY_REVIEW.status=BLOCKED` y la ausencia
de aprobación, certificación nativa completa y artefacto exacto impiden la
escritura V4. Las menciones a V4 como escritor candidato en la propuesta y en
los contratos G5 describen el futuro diseño, no la selección operacional.

## Emisor y etapas

`rc6_g5a_aggregate_issuer` configura proyectos ext4 con límite kernel agregado
de 20 GiB/100.000 inodos y control EDQUOT separado de 1 MiB. Conserva el
readback ROOT, el control EDQUOT real sobre descriptor abierto y desvinculado,
el sellado NONROOT, Landlock con denegación EACCES fuera del proyecto y el
readback ROOT posterior. El control de 20 GiB sobre un descriptor desvinculado
es una operación **material** distinta y sigue sin ejecución autorizada.

La preparación y los lectores de collection/execution tienen ahora rutas de
lanzamiento que verifican en vivo los inodos, mount ID y project ID de sus
salidas, y envuelven cada comando bajo el gestor nativo original. Los logs del
gestor se aceptan sólo cuando su descriptor regular pertenece al mismo
proyecto. HOME, TMP, caches, compilación, exportación y lectores deben quedar
dentro del proyecto. El proceso hijo instala el filtro seccomp original y
Landlock antes de `exec`; el gestor conserva el FIN original. La ruta no se
ejecuta con la admisión actual: `require_host_stage_admission` sigue levantando
`G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED` antes de cualquier comando.

Falta la operación ROOT admitida que cree el proyecto fresco en el ext4
original **antes** de la preparación y retenga su custodia, y falta el mismo
readback enlazado a FIN auténticos en las tres etapas. Un `prepared.json`, un
flag, una prueba de desarrollo o un muestreo de directorios no pueden suplir
estos hechos. El proyecto debe conservar Source exacta, 4 GiB físicos libres,
10 % de inodos libres, los cinco payloads y 1.202 ciclos originales, archivo
máximo 512 MiB, profundidad 32, retención 9h + 1h y los límites 5 GiB/512 MiB
y 26 GiB/20 GiB de bootstrap. Ningún crédito de borrado o GC anticipado entra
en la admisión.

Estado: **desarrollo de enlace, NO CERTIFICADO**. G5A y G0–G8 permanecen
bloqueados; PAPER/SHADOW ONLY, `real_orders_sent=0`.

## Auditoría sucesora ROOT/cuota/FIN

La [auditoría de cierre ROOT/cuota/FIN](RC6_G5A_ROOT_QUOTA_FIN_CLOSURE_2026-10-11.md)
registra el primer NO-GO del grafo de escritores: el checkout y tooling de
Actions, Git del padre y Source todavía preceden o quedan fuera del proyecto
cuotado. Un inventario AST cierra la preparación antes de su primer Git y un
preflight read-only de mount/block device/Q_GETFMT entra en la próxima CI de
desarrollo. `horizon.preflight` ya se ejecuta bajo gestor y FIN original,
pero los Git del padre y el setup ROOT previo a checkout siguen pendientes.
No hay aún autoridad provisional emitible ni readbacks ROOT unidos a los tres
FIN. La propuesta aceptada como diseño y el escritor V3 efectivo no cambian.
