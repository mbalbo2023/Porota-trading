# Recuperación controlada RC6 — 2026-10-06

Este paquete conserva la continuidad del mismo candidato de
`integration/rc6-convergence-468-469-470-20261005`. La recuperación del ownership
se registra en [#471, comentario 6014000727](https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-6014000727)
y su checkpoint en [comentario 6014551029](https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-6014551029).
La sesión original informada como colgada es `01a1097d-b864-7649-b326-05657cbe101a`.
Estos enlaces son referencias de coordinación suministradas por Root; el
preservador no efectuó operaciones remotas ni otra adquisición del ownership.

**Estado de entrega: BLOQUEADO para aceptación final y despliegue.** PAPER/SHADOW
ONLY, `real_orders_sent=0`, rutas reales `NOT_CALLED`, PPI Watch sin acceso.
Este documento no publica `RELEASED`; esa liberación corresponde al comentario
final explícito de Root en #471. No autoriza merge, deploy, rollback ni force-push.

## Fuente y alcance

El candidato remoto observado por Root es
`9a9b0f3860453ce47bee520d70b1957201679966`, árbol
`96f7ae24d75abc2a6c27866bb7e3fdfad6e9f18a`. Los recibos focales actuales están
ligados a esos valores y a 2630 archivos de fuente. El checkout local `85c03c8`,
árbol con prefijo `c913`, es una referencia histórica informada por Root, no
el HEAD actual. Las correcciones posteriores siguen uncommitted en los
diagnósticos preservados y requieren cualificación del SHA final real. No se
trasladan resultados de 9a9 a esas correcciones ni a un futuro commit documental.

`RECOVERY_STATUS.json` contiene el estado derivado. `MANIFEST.json` identifica
61 originales preservados, sus paths, tamaños, SHA256 y once campos stat antes
y después de la copia mediante `O_NOATIME/O_NOFOLLOW/O_NONBLOCK`. Los 10.774.238
bytes originales se conservaron sin compresión ni modificaciones. Los archivos
destino tienen modo 0644; el manifest conserva por separado el modo original.
Las comprobaciones acreditan el momento de preservación, sin afirmar continuidad
de metadata desde la ejecución original. No se restauró ni retrodató metadata.

## Evidencia conservada

| Época | Python | Casos únicos | Resultado original |
| --- | --- | ---: | --- |
| b6c2 inicial | 3.11.16 | 50 | 49 PASS, 1 FAIL; Root atribuye el fallo al harness sin main guard |
| b6c2 sucesor v2 | 3.11.16 | 50 | 50 PASS |
| b6c2 sucesor v2 | 3.12.14 | 50 | 7 PASS, 43 FAIL; incompatibilidad de custodia O_NOATIME del intérprete rootowned informada por Root |
| d56a | 3.11.16 | 92 | 92 PASS |
| d56a | 3.12.14 | 92 | 92 PASS |
| 92c3 | 3.11.16 | 93 | 93 PASS |
| 763e | 3.11.16 | 93 | 93 PASS |
| 9a9b | 3.11.16 | 93 | 93 PASS |
| 9a9b | 3.12.14 | 93 | 93 PASS |

Cada época conserva `receipt.json`, `focal.xml`,
`collection-before-fixtures.json` y `environment-before-fixtures.json`. La
preservación comprobó los digests JUnit y la correspondencia de los nombres
coleccionados/ejecutados sin lanzar pruebas. Los recibos 9a9 declaran 157
distribuciones y cero fallos, errores, skips o duplicados. Sus 93 casos son el
mismo alcance en dos intérpretes, nunca 186 escenarios independientes; tampoco
se suman las épocas anteriores. Son focales, sin aceptación Gov completa,
horizonte, imagen, artefacto o runtime.

La preparación inicial b6c2/Python 3.12 dejó un directorio vacío. Root informó
un rechazo de modos 0600/0700 frente a Git 0644/0755 antes de ejecutar pruebas.
No existe recibo de archivo para copiar: el manifest conserva esa ausencia y
la atribución como reporte de Root, no como resultado de producto demostrado.

El BIG canónico 9a9 conserva resultado y log originales en `raw/big-9a9b/`.
`import_proof_complete=true` acredita su ventana local observada;
`business_resource_complete=false` conserva el RED independiente. Falló en
familias con `TIME_BUDGET_EXHAUSTED` al salir de `readonly_copy`, antes de fsync
y de completar PREOPEN/OPEN: familias tomó 0.5422399569997651 segundos frente
al deadline original de 0.5. El recibo conserva fuente, cuotas, RSS y reap
verificados; esos positivos no convierten el ciclo en COMPLETE. Los cinco EXIT
PAPER finalizaron después del fallo: no prueban aislamiento durante slow-fsync.

Gov311 terminó sobre Source9a9, árbol `96f7ae24d75abc2a6c27866bb7e3fdfad6e9f18a`.
Los originales terminales en `raw/governed311-9a9b/` conservan JUnit, logs,
launch, recibos kernel y error de cualificación. JUnit registra 5890 casos,
23 failures, 120 errors y 0 skips, con 2075.700 segundos; el parse documental
confirmó esos totales y los 5890 elementos testcase sin ejecutar pruebas.
El PID principal fue reaped a los 2081.00466046 segundos y el RSS registrado
fue 1.725.100.032 bytes. El manager permanece RED:
`OWN_CHILD_EXHAUSTION_NOT_PROVED_WITHIN_BOUND`,
`process_group_absent_at_main_reap=false` y
`owned_children_exhaustion_verified=false`. El reap del PID principal no
acredita agotamiento de todos los hijos. También se preserva el RED
`PHYSICAL_SOURCE_EXTRA_MISSING_IGNORED_OR_EMPTY_DIRECTORY_FORBIDDEN`.
La corrida declara 157 distribuciones por metadata, sin autenticar sus bytes.
Estos resultados conservan el rechazo; no constituyen Gov final aceptado.

`raw/governed311-9a9b/post-execution-diagnostic.json` conserva un diagnóstico
posterior, no un recibo source-after Gov exitoso. Comparó los 2630 archivos
tracked con el índice previo: cero cambios de bytes/modos y 172 diferencias
sólo de atime, que V3 registra aparte de sus diez campos estables de código.
Conserva 331 archivos extra y seis directorios de `.hypothesis`/`data`, con
namespace RED. Agrupa los 120 errores en 110 `CHECKOUT_MODE_MISMATCH` y diez
`NATIVE_PROFILE_CODE_DIRECTORY_MODE_MISMATCH`. Identifica que `diagnostic.main`
dejó umask 077 al rechazar preflight; Root informó una restauración mediante
try/finally y un guard permanente con la misma ID de prueba, aún sin
cualificación trasladable a la ejecución 9a9. Los RED de deadline BIG, RSS del
tiny smoke, snapshot SQLite busy y agotamiento de hijos siguen abiertos.
`diagnostic_only=true` y `acceptance_complete=false` permanecen intactos.

`raw/governed311-9a9b/local-fixture-cleanup.json` conserva el recibo de limpieza
local de los fixtures privados de la corrida ya terminada, exclusivamente
`/workspace/scratch/porota-governed311-9a9b/pytest-private`. Registra
20.658.622.464 bytes libres recuperados, fuente congelada preservada y RAW
sin cambios. Los hashes del RAW Gov copiado coinciden con el recibo. Esta
preservación no ejecutó la limpieza y no eliminó la raíz congelada; el alcance
del recibo no es limpieza de runtime ni convierte el namespace RED en GREEN.

Las siguientes épocas locales se conservan aparte, sin SHA/tree Git
autenticado y sin sumar sus casos:

| Época uncommitted | Casos JUnit | Resultado original |
| --- | ---: | --- |
| Path guards inicial, dos módulos | 234 | 50 PASS, 3 FAIL, 181 ERROR; umask 077, fixture histórica de representación y falta de espacio |
| Path guards FIXED, selección reducida | 143 | 143 PASS, 0 FAIL/ERROR/SKIP; 33 provenance y 110 audit gate |
| Regresiones de recuperación | 12 | 12 PASS, 0 FAIL/ERROR/SKIP |

`raw/path-guards-uncommitted311/` conserva ambos XML y el manifest de épocas.
`failures-from-junit.log` extrae failure.text del XML RED: no es stdout original,
que no se capturó. FIXED conserva stdout/stderr completo y recibo de ejecución:
31.102 segundos JUnit y 31.347689867 segundos de ejecución, exit 0. Sus cuatro
hashes de fuente antes/después coinciden, sin acreditar inmovilidad continua
ni un SHA/tree Git congelado. Es diagnóstico de representación y validadores,
sin Gov final, FIP nuevo del candidato ni cierre de los cinco gates materiales.

`raw/regressions-uncommitted311/` conserva XML y stdout de los doce casos,
9.882 segundos JUnit: restauración de umask, decode público y adversariales,
custodia del root-atime, fixture IOL quiescent y assertions del path real
gestionado de Predeploy. Estos PASS acotados requieren binding y cualificación
del SHA final; no sustituyen la corrida gobernada RED sobre 9a9.

`helpers/*.py.source` archiva los bytes actuales del runner focal y del
exporter, del diagnóstico de importación y del runner externo Gov311. Se conserva su alcance y se evita
añadir módulos pytest ejecutables.
Esta copia no demuestra que cada época histórica ejecutara exactamente estos
bytes del helper; tampoco ejecutó esos programas ni autentica distribuciones
instaladas o un artefacto. El SHA del runner externo Gov311 conservado coincide
con el SHA del launch original, sin acreditar continuidad de metadata desde
la ejecución hasta esta copia.

El diagnóstico terminal `raw/import-probe-9a9b/urllib3-import-probe.json`
importó `ba_data912_history` sin crear fixtures. Registró un único `socket.bind`
de INET, bloqueado antes del syscall, en `urllib3/util/connection.py:127`,
`_has_ipv6`. La importación completó; permanecen `diagnostic_only=true`,
`acceptance_complete=false` y 157 distribuciones declaradas por el control de
metadata. No cambió el guard Gov ni recuperó un GREEN. El RAW identifica
`source_root` y no contiene SHA/tree: Source9a9 es la asociación suministrada
por Root, no una autenticación Git o binaria emitida por este diagnóstico.
Su script se conserva como `helpers/porota_rc6_import_probe.py.source`, sin
ejecutarlo durante la preservación ni otorgarle autoridad para excepciones Gov.

El inventory 9a9 permanece externo: 77.651.021 bytes, SHA256
`a9cb5ea44a34938cb50c2b3467b28b8f649907d00812487ecd53519d46ddc4de`.
Supera el límite de 2 MiB autorizado para su copia. Su clasificación sigue
`INVENTORY_NOT_EXECUTED`, con `final_candidate_eligible=false` y cinco gates
materiales abiertos. El hash y los once stat actuales figuran en el manifest;
no se copian índices completos de fuente, DB ni datadirs.

## Continuación del supervisor

Los gates materiales U14 y UX470-I01/I02/I03/I05 permanecen abiertos. Falta BIG
completo con los límites originales; horizonte V3 completo y recuperación;
browser LARGE sobre OPEN completo; Gov final de ambos intérpretes; binding
SHA/tree/JUnit/FIP; un Predeploy V2 y artefacto inmutable del candidato final.
Los cierres focales terminados se conservan y no se reaplican ramas separadas
de #466/#470.

Gov311 fue añadido después de su terminación; no se copiaron salidas activas,
observations extensas, índices de fuente ni árboles de pytest-private. Root
informó correcciones locales de assertions y paths temporales posteriores a
la corrida 9a9. Esos cambios siguen pendientes de cualificación; no convierten
el RED original en GREEN. Root debe revalidar la fuente final, Gov311/Gov312 y
ownership antes de una entrega aceptada. La aprobación o ejecución de
merge/deploy no forma parte de esta sesión Codex.
