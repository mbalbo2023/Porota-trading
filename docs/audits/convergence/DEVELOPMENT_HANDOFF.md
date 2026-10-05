# Convergencia RC6 #468 / #469 / #470

La orden original define una entrega de desarrollo con un candidato, un
Predeploy V2 y un artefacto. La aprobación del despliegue corresponde al usuario
después de revisar ese resultado concreto. Este documento explica los contratos
que debe verificar la entrega; el resultado ejecutado se obtiene de
`FINAL_INPUT_PROVENANCE.json`, los recibos del run canónico y la API de GitHub.
Su presencia dentro del repositorio no acredita ejecución ni autoriza promoción.

## Fuente y conservación

La base operacional fijada por el paquete es
`deploy/rc6-pr69-isolated-20260915` en
`da697c6e6c2274579f9e4a112fabc4327475dd35`, árbol
`96a112a55ed779df3f7056b1e30d81aac8d0b791`. Debe revalidarse antes de promoción.
El candidato se prepara exclusivamente en
`integration/rc6-convergence-468-469-470-20261005`. Integra el sucesor #466 y
el delta completo de #470, conservando las instrucciones de entrada de #451.

El manifest original fija quince heads y sus bases. La reconciliación verifica
sus blobs y modos, los 140 paths de #466 y los 30 de #470, y la evolución de cada
archivo mediante guards del caller correspondiente. Presencia y ancestry por
sí solas no demuestran conservación. La matriz documenta las diferencias y
no reaplica indiscriminadamente los PR históricos.

Los seis archivos originales están anclados por SHA256 literal. Se conservan
sus bytes, incluidos CRLF del registro y espacios de hard-break Markdown.
Esos espacios originales son evidencia congelada; el código y la documentación
nuevos deben superar su revisión de whitespace independientemente.

`REQUIREMENT_CLOSURE_MATRIX.json` conserva los 29 U, los 21 AUD y las cinco
incompatibilidades UX. Vincula además los 80 R originales, las 35 cláusulas F01,
las 20 F02 y las 35 cláusulas conjuntas F03–F05 a guards declarados en Git.
Mantiene el texto original de cada exigencia y escenario sin reinterpretarlo.
Las referencias compartidas o parametrizadas no se cuentan como nuevos ataques.

Los seis controles omitidos del harness anterior se verifican explícitamente:
colisión de familia histórica, etiqueta anual de renta fija, cadencia de
muestras main, crash tras INSERT, heartbeat sin progreso y liquidez IOL
archivada. Los doce módulos protegidos de #344 mantienen diez archivos
idénticos y dos adaptaciones literales exclusivas: razón de identidad completa
y moneda ARS explícita en el fixture de Candle. No se permiten otros cambios
en esos archivos.

## Contratos que convergen

Las identidades de libros, posiciones, catálogo e historia tienen cinco partes.
La selección causal toma la revisión más reciente conocida al corte antes de
evaluar su calidad: una revisión inválida posterior no revive un dato anterior.
Los instantes se comparan en UTC exacto, incluyendo microsegundos y folds.
Una corrección de unidad, multiplicador o procedencia también es una revisión;
no se reescriben hashes históricos ni su primer instante conocido.

SCALPING exige observaciones distintas posteriores a recovery, duración y
continuidad reales. Main declara muestras de eventos y su horizonte observado.
No convierte veinte eventos en veinte minutos. Volumen exige un contrato
fechado de unidad y acumulación; una forma monotónica no acredita cantidad.

La admisión PAPER comparte un único modelo BINDING de costos y economics.
El fill financiero congela sus inputs dentro de la misma transacción. La fase
`ATOMIC_PAPER_ADMISSION` es distinta de `NATIVE_DECISION`; las dos se vinculan
por clave, hash, identidad y clocks. El funnel no cuenta el recibo atómico como
otra decisión. STOP, EOD y permanencia de futuros conservan intención durable
y primera causa; ausencia de libro o profundidad no fabrica un fill.

El presupuesto global conserva lease, START, deuda y límites por endpoint.
La demanda crítica abarca todas las familias supervisables. Una ronda completa
incluye su trabajo de estado y salud. Incumplimiento, ledger desconocido o
capacidad insuficiente suspenden actividad secundaria y nueva admisión.
La retención de recibos debe reservar también filas y páginas para EXIT; cupo
de requests y capacidad de almacenamiento son contratos separados.
El controlador consulta esa ocupación antes de activar selección dinámica.
Ledger ausente con sidecars o locks huérfanos significa fuente desconocida,
no capacidad libre. El envejecimiento legal conserva deuda activa y tokens
START en vuelo. La ocupación es telemetría fuera del fingerprint estático;
un cambio legítimo de ocupación no revoca por sí solo la configuración aprobada.

History Store y preopen usan bases explícitas, selección point-in-time e intentos
reanudables. Export/preopen copian main/WAL coherentemente sin abrir SQLite en
la fuente ni crear su SHM. Su prueba de inventario y metadata no se generaliza
a todos los lectores de una base financiera viva. La lectura grande tiene
límite total y cierre sin historia parcial cuando lo agota.
La inicialización valida primero una copia de la fuente y su schema, antes
de negociar WAL o escribir DDL. La prueba queda ligada al Store e inode;
FULL y CLOSE_ONLY antiguos se rechazan conservando fuente y sidecars. El plan
de copia/migración probado no autoriza una migración operativa ni acredita
el ABI del master actual.

El runtime usa scratch privado de disco bajo el artifact root primario,
incluso cuando `HIST_DB_PATH` elige otro dataset dentro de `/app/data`.
Sus variables explícitas son `POROTA_SQLITE_SCRATCH_ROOT`,
`POROTA_SQLITE_SCRATCH_MAX_BYTES`, `POROTA_SQLITE_SCRATCH_RESERVE_BYTES` y
`POROTA_SQLITE_SCRATCH_MIN_FREE_INODE_PERCENT`: 512 MiB, 2 GiB residuales y
10 por ciento de inodes libres. Los residuos identificados de un crash
cuentan contra esa cuota; contenido desconocido o custodia alterada bloquean
la lectura. No se aumenta el tmpfs de 32 MiB. El preflight antes de transferencia
usa el mismo estimador de main/WAL/SHM y la misma configuración del launcher.
Los modos explícitos OFF/DISABLED del contract runner llegan al observador;
su guard verifica que ese runner no abra fuente ni proveedor.

## Publicación y lectores

Report, checkpoint, status y `projection.sqlite` pertenecen al mismo CURRENT,
generation ID, sequence, watermark, configuración, safety y custodia local.
La cuarta pieza es una proyección derivada; no tiene autoridad financiera.
Sus columnas de filtros/orden/cohorte deben quedar ligadas a su derivación,
además de los payloads, y cada página debe conservar la pertenencia declarada.

La lectura completa declara `FULL_LOGICAL_SEMANTICS`. La lectura de páginas
declara `WIRE_AND_PROJECTION_SEMANTICS`, con hashes de las cuatro piezas,
CRC/expansión acotada, headers y derivación de los tres originales. No afirma
haber materializado el report completo. Los consumidores comparten un deadline
absoluto: un segundo para UX y dos para salud, sin publicar un resultado parcial
como COMPLETE. Es un límite cooperativo, no una garantía de scheduling realtime.

El codec conserva todos los registros. Sus límites explícitos incluyen
64 MiB por miembro durable, 512 MiB de expansión de generación, 32 millones de
nodos, profundidad 64 y salida de proyección de 4 MiB. Las páginas visibles
tienen hasta diez filas y sus totales cuentan el universo coincidente completo.
La consulta de cohortes tiene paginación propia y denominadores del mismo corte.

La custodia tiene high-water y fases recuperables fuera de CURRENT. Archivar
requiere un objeto durable verificable y un receipt ligado a la cadena; un URI
autoafirmado no autoriza borrar evidencia. Rotación y ACK son reentrantes tras
fallos físicos. El horizonte y las cuotas deben verificarse nuevamente con
cuatro piezas. Un horizon de fixture pequeño no demuestra nueve horas a 12.000
identidades: el costo de cada carga y su límite deben informarse por separado.
Compromiso simultáneo del writer y todos los anclajes locales está fuera de la
autenticidad demostrada; esta custodia no es WORM.

## Ejecución exigida antes de publicar el resultado final

La suite gobernada descubre desde repository-root. Conserva una única exclusión
preexistente: el módulo raíz duplicado de A3 con sucesor en `tests/`. Debe haber
igualdad descubierto/ejecutado y cero fail, error, skip o xfail. El SHA y el árbol
de la fuente se congelan antes y se comprueban después. Los dos runtimes
revisados son CPython 3.11 y 3.12; ambos requieren las mismas 157 distribuciones
aprobadas: 154 runtime y tres herramientas de build con hashes verificados.
APT, runner y compilación de los cuatro sdists siguen como límites explícitos
de reproducibilidad; no se afirma hermeticidad completa.

La prueba grande debe publicar PRE y OPEN completos con 12.000 identidades,
60.000 observaciones, entrada real al fsync intervenido y cinco salidas
financieras concurrentes verificadas. Deadline de ciclo, RSS, bytes, etapas y
stats de fuentes se informan desde la ejecución. Fallar antes de fsync no
acredita slow-fsync. Las rutas, aliases, resoluciones y foco durante fetch del
navegador se ejercitan con el productor nativo y clocks/monedas reales del fixture.

Los RED previos se conservan con su alcance. El supuesto fallo de bootstrap
de futuros del receipt mixto fue un error de harness: importó BM de otro checkout.
La repetición por `git archive` íntegro pasó y el receipt original quedó invalidado
para claims productivos. La autocorrección no introdujo DDL innecesario ni
cambió assertions para ocultar una falla.

El replay independiente de los drivers originales sobre el archive íntegro
de #466 conserva sus resultados caso por caso: warmup prestado, admisión con
R/R rechazado y selección parcial de libro se reprodujeron en el caller antiguo.
Stale-tail sigue como control preservado; la semántica real de volumen y la
capacidad del proveedor siguen como límites externos. La ausencia de duración
del modelo main se informa como hipótesis de eventos. No se convierte cada
observación del auditor en un nuevo error matemático ni se atribuye una ejecución
final a estos recibos históricos.

## Artefacto y despliegue posterior

Sólo después de cerrar integración se publica un PR DRAFT único y se ejecuta
Predeploy V2 sobre su head exacto. Git, manifest, bundle, config/layers/rootfs y
modos 0644/0755 deben coincidir. El runner descarga el ZIP primario ya publicado,
verifica su digest API/size/CRC, lo carga y prueba el ImageID exacto en un container
efímero offline sin credenciales ni mounts operativos. No reconstruye otra imagen.
Los recibos secundarios acotados se verifican por sus bytes y origen del run;
no son un artefacto promotable ni pueden otorgarse confianza a sí mismos.

Se deniegan como candidatos finales los artifacts parciales 11315198085,
11317509383 y 11293625514. La identidad nueva se liga por workflow, repo, evento
pull_request, head, run, attempt, artifact único, expiración y SHA256 externo.
La futura aprobación de merge contiene los cinco trailers exactos del binding.
Deploy V2 conserva su mutex y no reconstruye en el Droplet.

El disco exige image tar comprimido + tamaño Docker exacto + dos bundles +
2 GiB residuales e inodes libres según policy. El costo adicional de una copia
o migración se mide por separado antes de ejecutarla. No se reinstala un piso
arbitrario de 6 GiB. Cleanup preserva imágenes referenciadas, evidencia única,
volúmenes y DB; no usa prune global ni inspecciona assets de PPI Watch.
La admisión del disco suma también el crecimiento pendiente de scratch,
`max(0, 512 MiB - residuos identificados)`, conservando una sola reserva común
de 2 GiB cuando comparten filesystem. La migración condicional exige inventario,
ABI, destino revisado y costo real propios; no se reemplaza por un estimate
de un fixture ni por el espacio histórico del host.

La orden final se genera después del artefacto y contiene sus inputs concretos,
guards, postchecks y contingencia fix-forward. Requiere la aprobación explícita
establecida en la sección12 de la orden original. Ninguna autorización inferida
desde un comentario externo cambia el alcance de esta entrega.

Postchecks deben comprobar identidad de imagen/source/tree/artifact, configuración,
PAPER, rutas reales NOT_CALLED, órdenes0, children/generaciones, schemas/readers,
budget/EXIT, clocks/cut, UX read-only y cleanup medido. Un preopen saludable
no certifica mercado OPEN. Capacidad factual PPI OPEN, términos comerciales de
cuenta, master de veinte ruedas y edge OOS siguen NO_VERIFICADO/NO_DEMOSTRADO
hasta recibir evidencia independiente. Los componentes dependientes permanecen
cerrados; un deploy no produce esa evidencia.
