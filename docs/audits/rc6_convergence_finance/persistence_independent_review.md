# Revisión independiente: publicación, archivo y recuperación de ACK

Revisión READ_ONLY solicitada por el integrador sobre la línea f2e6d6bf/2fb29369.
No se editaron archivos de persistencia. Los únicos procesos detenidos fueron
hijos creados por el probe en directorios temporales sintéticos; sin host,
provider, GitHub, órdenes o PPI Watch.

La revisión identificó un bloqueo de liveness en una frontera no cubierta por
los tests de rotación. El archivo1 había quedado verificado y durable antes de
SIGKILL en `archive_before_publish_ack`, con objeto+receipt1+checkpoint1 pero sin
ACK local. Un archivo2 legítimo avanzaba la cadena a checkpoint2. Reintentar
`archive_generation(gen1)` rechazaba para siempre el receipt antiguo con
`RETENTION_ARCHIVE_RECEIPT_LINEAGE_CONFLICT`, porque `_advance_archive_checkpoint`
sólo aceptaba el head actual o su sucesor. Se preservaban gen1, archivo/recibo1
y CURRENT3; no fue pérdida de bytes ni aceptación de un cut falso.

El owner de persistencia corrigió el caso en
`b82666db11093806bb27badab65f498bb11d7a89`: verifica pertenencia del receipt
antiguo a toda la cadena sellada, con metadata acotada por cuota, y regenera su
ACK exacto sin retroceder ni reutilizar el high-water. Acknowledged/rotate/
recover/compact prueban la pertenencia antes de autorizar borrado físico.
El owner agregó su propio negativo de receipt antiguo alterado y pruebas de
errores/cortes reales de syscalls. Esta revisión no sustituye esas suites.

La misma reproducción independiente usa APIs públicos nativos:
[rc6_retention_ack_resume_probe.py](../../../tests/probes/rc6_retention_ack_resume_probe.py),
SHA256 `308d6e20b34a39ecb3c6e6ea09b3142f46c9f6901b86a88ebb135234a72b22e7`.

| Evidencia | SHA/tree de git archive | Resultado |
| --- | --- | --- |
| [RED](retention_ack_resume_red_2fb29369.json) | 2fb293694843c07b8dda0a54b2b192236d1ca923 / 74c22aa31bdf765e29a2836357c7a3f267f42bfc | Retry gen1 rechaza LINEAGE_CONFLICT después de receipt2; datos conservados |
| [GREEN exacto](retention_ack_resume_green_b82666db.json) | b82666db11093806bb27badab65f498bb11d7a89 / 775ba49f7dbd091ed3deccc499e9b0e953103df1 | ACK1 regenerado,2 receipts, checkpoint digest=receipt2, CURRENT3 idéntico |
| [GREEN previo WIP](retention_ack_resume_green_worktree.json) | HEAD2fb29369 con delta posterior/hash por archivo | WORKTREE_NOT_FROZEN, sólo trazabilidad; no prueba final |

Ambos probes de `git archive` usan el mismo script, registran fuente antes/
después sin cambios y network_attempts=0. El receipt GREEN exacto tiene SHA256
`34b9fe26024749ea8838e05e5462b6d6eb204cf0073f16ee11186ce619305e88`.
La presencia del archivo/recibo1 se comprueba; su ACK se regenera una vez;
el contador no retrocede y no se crea un receipt3 espurio. El cut de CURRENT
se compara completo; no se denomina fresco por coherencia o mtime.

También se ejecutaron12 tests nativos focales de publicación SIGKILL/PUBLISHING,
ACK/objeto inválido y checkpoint ausente:12/12, sin cambios de source. La revisión
confirmó que el reader rechaza publicación no sellada; la recuperación writer
valida el prepared cut y publica hacia delante. El archivo verifica bytes/
miembros/hash/manifest/CRC de gzip reales antes de autorizar rotación; delete
intent y tombstone permiten resumir una eliminación parcial. No se encontró
otra ventana material en esos caminos después del fix.

La autoridad y el archivo mantienen custodia local durable
`LOCAL_PRIVATE_FSYNC_NOT_WORM`, separada del árbol de CURRENT. Esta garantía
no se presenta como autenticación externa ni WORM frente a quien puede
reescribir ambos roots. Fuente/freshness siguen siendo requisitos distintos
del consumer; tampoco se certifica capacidad de producción. El SHA integrado
final requiere sus propios ensayos/receipt tras freeze, además de estos RED/
GREEN independientes por subsistema.
