# Custodia sin pérdidas y distribución mínima

Esta política es prospectiva. **No se borra/mueve evidencia histórica ni se
reescribe Git**. La misión conserva las ramas, los blobs y los artifacts originales.
Un índice con SHA256 identifica evidencia; no sustituye su custodia ni acredita
que un destino aún no inspeccionado contiene los bytes.

## Inventario completo, denominadores y roles

#476:3127 rutas modificadas,1.400.512 adiciones,2158 eliminaciones. El diff Git
completo fue revalidado frente a productiva da697, sin depender del tope API3000.
El piso903.213 líneas de auditoría/documentación no es software financiero ni
almacenamiento físico. Se reutiliza el inventario3127/3127 de #479:
`../rc6-systemic-review-20261008/pr476-complete-inventory.csv`.

`pr476-blob-sha256.csv` añade SHA256 a los2683 blobs de HEAD distintos, verifica
su pertenencia al tree auténtico de #476 y el hash Git de cada blob, y conserva
el multiplicador de copias por ruta. Se une mediante `head_blob_sha1` al inventario
previo. No contiene otra copia de RAW. Las rutas eliminadas, si existieran, se
recuperan de la base original; no se les inventa un blob de HEAD.
El índice anterior de artifacts conserva SHA256 por ZIP y miembros/manifest,
Source/run/attempt, tamaño y expiry. Sus comprobaciones históricas no se vuelven
a presentar como descargas nuevas de esta misión.

| Rol | Decisión de custodia/distribución |
|---|---|
|Software operativo y sus helpers importables|Git y paquete cuando integra el closure real. Extensión/directorio no decide por sí solo.|
|Contratos,157closure,19objetos originales,tests/fixtures fuente indispensables|Git/fullSource íntegros. `.source`, wrappers y originales referidos por tests no se purgan por parecer evidencia.|
|RCA e índices compactos|Git, con origen, hashes y límites de conocimiento; sin copiar RAW de nuevo.|
|Evidencia generada con receta y todos los inputs auténticos|Nueva evidencia masiva fuera de Git bajo custodia existente verificada. Sólo es replicable si se puede reconstruir y comparar exactos hashes.|
|RAW/Source/log/JUnit único o unicidad no determinada|PRESERVE/HOLD. El histórico en Git permanece. Un único log remoto no se declara replicable por tener el script.|

Las categorías del inventario son clasificación de rutas, no prueba automática
de replay ni unicidad. Todo `NO_VERIFICADO` conserva los bytes. Cada elemento
obligatorio requiere path/member o artifact-ID, bytes, SHA256, SourceSHA/tree,
run/attempt/job o blob original, recipe/input graph, rol, custodio, fecha de
retención y mecanismo de recuperación. Los miembros obligatorios de manifests
mantienen sus relaciones, no sólo un hash agregado.

## Las36 rutas todavía elegibles: dependencia real encontrada

`evidence-roots.json` enumera path/size/SHA256 y aplica los matchers **nativos**
`is_bundle_path` y `dockerignore_exclusion` sobre ecad.36 rutas/6.658.367 bytes
siguen elegibles tanto para bundle como para contexto. Dockerfile tiene `COPY . .`.
Esto prueba elegibilidad estática, **no contenido de una imagen**: no se construyó
ni inspeccionó imagen ni artifact de deploy en esta misión.

| Scope | Archivos / bytes | Distribución propuesta |
|---|---:|---|
|`notes/rc6-evidence/`|7 /4.668.268|Excluir sólo de deploybundle/context; histórico conservado|
|`rc6_audit_evidence/history_convergence/`|22 /1.842.675|Excluir sólo de deploybundle/context; probe fuente conserva fullSource/tests|
|`rc6_audit_evidence/HISTORY_CONVERGENCE.md`|1 /30.745|RCA en Git; no payload runtime|
|Paquete Python restante|6 /116.679|Conservar completo|

Los seis módulos son `__init__.py`, `sqlite_snapshot.py`, `sqlite_scratch.py`,
`package.py`, `recompute.py`, `history_probe.py`. Source/preopen/dashboard,
annual/historical/observer importan `sqlite_snapshot`; cargar ese submódulo
ejecuta `__init__`, que importa `package` y `recompute`. El probe/scratch también
se usa en tooling. Se conserva el paquete completo y su facade; no se practica
una poda parcial del closure. **Excluir la raíz `rc6_audit_evidence/` sería un
defecto operativo nuevo.** La inspección de imports es estática, no un import
financiero ejecutado para esta misión.

El fix mínimo futuro del owner core es añadir esos dos prefijos y ese Markdown
a la selección de deploybundle y `.dockerignore`, preservando los seis módulos,
los tests y el manifest fullSource.30 archivos/6.541.688 bytes dejan de ser
candidatos de distribución. Incluyen27 generados/6.490.951 bytes, dos RCA
Markdown/35.472 y un probe fuente/15.265: ese probe conserva su rol fuente en
Git/fullSource aunque no sea necesario en la imagen de runtime.
**Fix propuesto, no aplicado** por ownership. Probar imagen real sólo en G8.

## Copias y crecimiento recurrente

WIP tiene385.742.505 bytes lógicos;365.477.187 bajo docs ya están excluidos de
deploybundle/context por código. FullSource debe seguir conteniendo el árbol
auténtico. Tres rutas de `whole-source.tar.gz` comparten un blob de21.685.383
bytes: Git deduplica el objeto, pero el checkout materializa tres archivos.
Source, clones fullGit/fullSource, índices del repositorio y artifacts sirven
roles diferentes; no se suma una copia lógica como consumo físico ni se elimina
una copia contractual sin probar su último consumidor.

La inflación de checkout/transporte es real; el tamaño de una imagen no se
infiere. Este hallazgo no agrega un gate material G0–G8 basado en líneas/archivos.
Sólo un impacto medido o una dependencia concreta puede hacerlo bloqueante.
La propuesta de packaging no cambia el fullSource ni arregla por sí sola G0/G5.

## Retención explícita y recuperación

1. **Histórico ya en Git**: HOLD sin fecha de eliminación en esta misión. No
   rebase/forcepush/purge/migración; Git SHA+path permite recuperar bytes y unir
   SHA256 al ledger. Para un blob `git show SHA:path` debe producir el mismo hash.
2. **RAW obligatorio aún sólo en Actions**: los vencimientos originales son
  14/21/22-oct-2026 según `../rc6-systemic-review-20261008/evidence-index.json`.
   El primer vencimiento conocido es14-oct19:37:34Z. Expiry no es custodia durable.
   Mantener un HOLD de conservación; el owner debe copiar los bytes exactos a
   custodia GitHub durable ya disponible y leerlos/hash-verificarlos **antes**
   de expiry. No se ha realizado ni se simula esa transferencia aquí.
3. **Nuevo RAW**: conservar mientras el RCA esté abierto y hasta90 días después
   del handoff de calificación correspondiente. Evidencia financiera/contractual
   única permanece bajo HOLD hasta decisión expresa de retención;90 días no
   autoriza eliminarla. Actions puede servir de transporte con retention explícita,
   nunca de única copia si su expiry precede el HOLD. Una release/asset existente
   verificada es un posible destino GitHub; no se crean servicios ni costos aquí.
4. **Recovery**: descargar el ID/destino indexado, verificar SHA256/tamaño,
   descomprimir bajo límites originales, cotejar miembro/manifest/input graph y
   SHA256 de originales. Replay requiere script+closure+Source+inputs auténticos.
   Si falta un input, conservar estado NO_REPLICABLE/NO_VERIFICADO, nunca borrar
   el único RAW por haber producido otro log parecido.

La unicidad remota y la copia durable siguen pendientes; esta política no declara
“custodia externa completa”. Ninguna evidencia fue eliminada por la misión y no
se copia masivamente a Git para salvar una fecha. La conservación pendiente tiene
responsable core/custodio legítimo y fecha concreta, separada de G0–G8.

## Guard barato y prevención permanente propuesta

`reproduce.py::evidence_guard` contiene una comprobación prospectiva del diff:
rechaza payloads nuevos/modificados en los dos roots de evidencia declarados y
el RCA histórico de packaging; acepta helpers runtime y fixtures fuente.
Los tres ejemplos in-memory pasan; el guard **no está instalado en CI**, no borra
la baseline y no es un veto de calificación material.

La integración mínima futura añade el mismo chequeo al preflight barato existente,
sin framework/wrapper nuevo. Nuevas evidencias obligatorias requieren un índice
compacto con destino recuperable/hash verificado; índices/RCA nuevos van bajo
governance. Un nuevo RAW bajo otra ruta requiere revisión del rol/destino, no
una excepción silenciosa. Ningún límite de líneas o tamaño sustituye esa revisión.
La política del diff evita reincidencia en las rutas conocidas; no se vende como
clasificador universal de contenido ni permiso para quitar tests fuente.
