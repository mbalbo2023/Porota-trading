# Decisión concreta antes de calificación RC6

Recomendación: autorizar un **perfil preparado aislado para GitHub Actions** y
su presupuesto; mantener bloqueadas contratación, heavy y deploy hasta entonces.
El runner público ubuntu24.04 sigue siendo el contrato canónico vigente; no se
modificó a self-hosted ni se retiró ningún control. La autorización debe nombrar
expresamente el nuevo perfil y su equivalencia verificable, además del gasto.

## Contradicción física reproducible

`scripts/rc6_capacity_comparison.evaluate_bootstrap_storage` lee los límites
originales de `limits_for("bootstrap")` y el costo exterior de controles:

| Obligación simultánea | Bytes |
|---|---:|
| Backing físico26GiB |27917287424|
| Reserva residual4GiB separada |4294967296|
| Cota de controles exteriores, bloque4096B |384040960|
| Mínimo necesario, todavía no el grafo completo |**32596295680**|
| Garantía14GiB del runner canónico |15032385536|

La garantía no cubre ese mínimo. Tener más espacio en **una** medición no lo
convierte en garantía contractual; el preflight vivo siempre es obligatorio.
La quota hard20GiB no reemplaza el backing26GiB ni permite consumir su reserva.
Los tests usan estos valores originales, rechazan el caso14GiB y separan un
caso con espacio suficiente de una certificación de cuota/kernel/custodia.

`readonly_runner_observation` registra filesystem, unidad/dev/mount/free/inodes,
kernel metadata y CPU/RAM sin cargar módulos, montar, crear backing ni lanzarROOT.
QFMT=m/no observado live no prueba que todo GitHub-hosted sea incompatible.
Las anteriores fallas mount/errno y la custodia de señal permanecen abiertas.
El native manager original conserva SHA256
`55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8`.

## Perfil propuesto y costo

Catálogo DigitalOcean consultado read-only el 2026-10-09T18:37Z:
`s-4vcpu-16gb-amd`, nyc1,4vCPU,16GiBRAM,200GiBSSD, disponible,
USD84/mes o USD0.125/h según el catálogo. Es un runner de calificación
independiente; **no** modifica el Droplet productivo ni PPI Watch.
No se provisionó, contrató ni conectó nada. CPU compartida no garantiza BIG75s.

Requisitos verificables del perfil, antes del primer productor:

- Ubuntu24.04/CPython exactos, ambiente efímero dedicado, Source/file modes
  canónicos y namespace con custodia medida; sin credenciales de trading reales.
- Ext4 o XFS y cuota agregada física nativa con project inheritance y límites
  leídos del kernel, herramientas selladas y prueba real de escape denegado;
  kernel/config/format/module live comprobados, sin inferir soporte del catálogo.
- Supervisión del actorROOT **desde su primera instrucción**, señal y terminación
  realmente permitidas, UIDdrop/FDs cerrados, ECHILD/FIN/recovery genuinos. El hold
  actual no se retira hasta tener la implementación/proof adversarial auténtica.
- >=32596295680B libres como piso,4GiB residuales y >=10% inodes; además grafo
  compuesto completo de tooling dual/157/four sdists/19Git/fullSource/directorios/
  productores/RAW/temporales/recovery/Docker.200GiB nominales no prueban ese grafo.
- Custodia recuperable de RAW y controles externos; autorizaciones por gate,
  candidato único, receipts exactos y ownership supervisado durante gates largos.

Preparar el perfil requiere código/probes de custodia ROOT y aceptación real de
cuota: tener más disco por sí solo no cierraG0. Luego se deben derivar envelopes
auténticos G5 para1202 prefijos y probar todo el schedule512MiB/9h+1h/depth32,
sin introducir otro codec, variar universo ni adoptar el forecast de siete cortes.

## Alternativas y alcance de la decisión

1. **Recomendada:** aprobar perfil preparado dedicado y hasta USD84/mes de
   runner, manteniendo las garantías originales; aceptar sólo después de pruebas
   nativas deG0, modeloG5 y calificación íntegra de un único candidato.
2. Mantener exclusivamente GitHub-hosted con14GiB garantizados: RC6 continúa
   bloqueado bajo el contrato físico actual. Sólo otro perfil hosted con capacidad
   garantizada suficiente y custodia nativa probada podría ser equivalente.

El host actual ID594077619 sigue activo con1vCPU/1GiBRAM/25GiB. La consulta API
no mide cgroups, carga ni RSS del candidato. La medición37656549281 es histórica
de af08 y no se reutiliza como certificación del candidato nuevo. Después de su
calificación y antes de promoción se necesita la verificación canónica read-only
del host y, si falla, autorización separada de capacidad/migración productiva.
La decisión de runner no autoriza despliegue ni tocar PPI Watch.
