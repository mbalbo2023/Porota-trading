# Retención de evidencia RC6 SHADOW — Issue #465 F-05

Esta política admite escrituras exclusivamente en el directorio privado de
evidencia SHADOW. El motor PAPER factual, sus posiciones, supervisor de salidas,
DB de trading y PPI Watch conservan sus rutas de ejecución independientes.
`real_orders_sent=0`; las rutas reales permanecen `NOT_CALLED`.

## Cuotas y presión

`EvidenceRetention` aplica estos valores predeterminados, sin cambiar la
configuración de ningún runtime productivo:

| Control | Valor / efecto |
|---|---|
| Umbral soft | 80% de bytes o entradas proyectadas; `RETENTION_PRESSURE` y `RETENTION_SOFT_THRESHOLD`; la persistencia íntegra puede continuar. |
| Umbral hard | 128 MiB de bytes lógicos y 512 entradas recursivas; superar cualquiera deniega la escritura nueva. |
| Pico admitido | Evidencia anterior + generación/staging nuevos + temporales del pointer + metadatos de directorio. No descontar la generación anterior antes del commit. |
| Reserva física | 64 KiB libres adicionales al pico nuevo para control/estado; `statvfs.f_bavail`, no tamaño aparente del volumen. |
| Alias | Symlinks, hardlinks y archivos especiales son rechazados antes de limpiar o rotar. |
| Inventario | Profundidad máxima 4 e inspección acotada; sobrecapacidad produce un motivo explícito, sin recorrido ilimitado. |

La cuota de entradas cuenta archivos **y directorios**, incluidos generaciones,
staging, `writer.lock`, ACKs y registros de pins. Los bytes incluyen tamaño
lógico de archivos y metadatos de directorios; un archivo sparse no evade la
cuota. El caller debe reservar el tamaño nuevo de forma pesimista, incluida la
telemetría de retención que se incorporará a los payloads.

La cantidad de cortes que cabe depende del tamaño de cada corte y la cadencia.
Con generaciones de cuatro miembros más un directorio, el límite de 512
entradas puede alcanzarse dentro de una rueda si no se exporta evidencia. El
80% es una alerta anticipada, **no una promesa de retención de una rueda
completa**. Esta misión no habilita un destino de archivo productivo. Si falta
archivo autorizado, conservar la evidencia y degradar SHADOW es el resultado
seguro y explícito.

## Evidencia protegida y temporales

Nunca se elimina automáticamente:

- `CURRENT.json`, la generación que apunta, y las generaciones actuales/previas
  suministradas por el protocolo de commit/recovery;
- generaciones del registro durable `retention-pins.json`;
- freezes inmutables `preopen-YYYY-MM-DD.json.gz`, que conservan provenance de
  sesión;
- generaciones sin ACK de exportación verificable, aunque sean antiguas;
- ACKs de archivo, que conservan el ledger de rotación;
- archivos o directorios de nombre/contenido desconocido.

El registro de pins tiene esta forma y se valida de forma acotada:

```json
{"schema":"RC6_SHADOW_EVIDENCE_PINS_V1","generation_ids":["0123456789abcdef0123456789abcdef"]}
```

Los temporales propios admitidos son `.generation-<UUIDhex>.tmp/` con sólo los
miembros `report.json.gz`, `checkpoint.json.gz`, `status.json`, `manifest.json`;
`.CURRENT.<UUIDhex>.tmp`; `.independent-<UUIDhex>.tmp`; y temporales exclusivos
del protocolo anterior cuyo basename exacto coincide con la allowlist del
módulo. No se usa `rmtree`, glob general ni edad/mtime como autoridad.

La limpieza requiere ownership de `writer.lock` exclusivo y no bloqueante.
`EvidenceFiles` entrega su propio `writer_fd`; un caller independiente intenta
adquirir el mismo lock y devuelve `RETENTION_WRITER_BUSY` si existe escritor
activo. El helper nunca libera el lock heredado del caller. Contenido desconocido
en staging provoca `RETENTION_TEMP_CLEANUP_UNSAFE` y se preserva.

## ACK de archivo y rotación

La rotación sólo considera `gen-<UUIDhex>/` bajo presión. Antes de autorizarla,
un proceso de archivo **autorizado**, mediante el control plane existente, debe:

1. Exportar la generación íntegra a un destino privado durable autorizado.
2. Rehashear el archivo exportado, el manifest original y los tres miembros.
3. Confirmar que el export no vence ni depende de temporales de esta sesión.
4. Registrar el ACK durable en la raíz privada; jamás commitear datos de
   trading, payloads privados o credenciales al repositorio público.

Formato de `archive-ack-<UUIDhex>.json`:

```json
{
  "schema": "RC6_SHADOW_ARCHIVE_ACK_V1",
  "generation_id": "0123456789abcdef0123456789abcdef",
  "manifest_sha256": "<SHA256 del archivo manifest.json exacto>",
  "archive_sha256": "<SHA256 del export íntegro verificado>",
  "archive_uri": "<identificador privado durable sin credenciales>",
  "archive_verified": true,
  "durable": true,
  "acknowledged_at": "<timestamp aware de la verificación>"
}
```

El helper valida UUID, hash exacto del manifest, schema del manifest, los nombres
de los tres miembros y **cada SHA256 de bytes codificados** contra ese manifest.
Rechaza ACKs ausentes, inválidos, futuros, no verificados o no durables. No
reinterpreta un ACK como prueba de rentabilidad ni de runtime. La verificación
del destino externo pertenece al proceso de archivo autorizado; el módulo no
accede a red, proveedores ni secretos. La auditoría independiente puede volver
a verificar el export con su digest.

Un pin tiene precedencia sobre cualquier ACK. Un miembro cambiado desde el
export o un archivo adicional desconocido impide rotación. Los ACKs permanecen
después de borrar una generación archivada y hacen la operación auditable e
idempotente. Ni freezes ni ledger de ACK se liberan automáticamente: cualquier
futura extensión de esa política deberá preservar provenance durable y ser
autorizada explícitamente.

CURRENT, pins, manifests y ACKs rechazan claves JSON duplicadas, incluso si el
último valor parece válido. Una atestación contradictoria nunca libera un pin o
convierte `durable=false` en autorización de borrado.

## Estado, degradación y recuperación

`prepare` retorna `status`, `reason`, `shadow_degraded`, bytes/entradas actuales
y proyectadas, límites, espacio físico, temporales limpiados, generaciones
archivadas rotadas y cantidad de pins. Los errores `RetentionPressure` contienen
las mismas métricas que estaban disponibles al fallo; nunca se presentan como
éxito silencioso.

Con soft pressure el caller publica la advertencia en report/status committed.
Con hard pressure, ENOSPC/EDQUOT o permiso denegado, el caller debe detener la
publicación de un nuevo corte íntegro, comunicar el motivo de degradación y
enlazar la última generación committed cuando exista. Si ni el estado puede
persistirse, el log/estado de proceso comunica la falla; el pointer anterior no
se reemplaza por un éxito ficticio. Un estado de falla no cuenta como nueva
observación de mercado ni como checkpoint actualizado.

Recuperación operativa, dentro de una autorización futura del control plane:

1. Leer el estado privado y los digests del último corte committed.
2. Verificar el motivo: cuota, espacio físico, permisos, aliases, pin/current
   inválido o escritor activo.
3. Archivar y verificar evidencia elegible; registrar ACKs sin quitar pins de
   evidencia auditada. Conservar la evidencia que no pueda archivarse.
4. Restaurar capacidad/permisos del directorio privado si ésa fue la causa.
5. Reintentar un ciclo SHADOW: el protocolo limpia sólo temporales propios,
   valida el corte anterior y admite un nuevo pico íntegro si cabe.
6. Verificar report/checkpoint/status de la nueva generación por manifest y
   hashes, y confirmar que el motivo de degradación se resolvió.

No se requieren comandos de terminal del usuario. Esta misión deja política,
guards y tests offline; no ejecuta archivo, escritura de runtime, merge ni
deploy productivos.
