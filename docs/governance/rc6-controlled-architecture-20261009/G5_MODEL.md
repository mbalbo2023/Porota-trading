# G5: modelo global, fallo histórico y límite de conocimiento del WIP

Estado: **HISTÓRICO_AF08_RED_DEMOSTRADO / WIP_BLOQUEADO_GLOBAL_BOUND**.
Límite original536.870.912 bytes. No se ejecutó Horizon ni se modificaron codecs,
dictionary, stride, anchors, retención, datos financieros o relojes de mercado.
`analysis.json` deja `global_complete_physical_upper_bound_bytes=null` porque no
existe una cota global certificable del WIP; rellenarla con una proyección sería
falsear la evidencia. No se afirma imposibilidad matemática del WIP.

## Los1202 cortes y las cohortes reales

El schedule original es un PREOPEN a13:20UTC y1201 cortes desde13:35UTC cada30s,
incluyendo23:35UTC: **1PREOPEN +770OPEN +431CLOSED =1202**. Las fases no se
modelan como un CLOSED uniforme. `horizon-schedule.csv` enumera todos los relojes;
es aritmética del schedule, no un payload financiero reconstruido ni una carga.
El catálogo operativo original es1200 instrumentos/6000 observaciones; las
cohortes conservadas no equivalen al catálogo del último tick.

| Corte histórico | Efecto | projection / checkpoint / report, bytes |
|---|---|---|
|771,19:59:30 OPEN|Último OPEN|2.207.744 /1.016.273 /880.323|
|772,20:00:00 CLOSED|observe-only abre next-session|1.904.640 /891.031 /392.222|
|773,20:00:30 CLOSED|primer universo SHADOW next-session más cohortes anteriores|4.136.960 /1.799.941 /977.113|
|774,20:01:00 CLOSED|6000 cohortes relevantes después del segundo borde|4.050.944 /1.799.768 /1.027.057|

Los4800 cohortes de un diagnóstico reducido omiten estos bordes; no son input
de este modelo. Los valores de la tabla son mediciones históricas de af08, no
mediciones de ecad. La cardinalidad exacta por miembro/corte que falta queda
NO_VERIFICADO, no se sustituye por un fixture reducido.

## Retención: GC no puede rescatar los ciclos operativos

`retention.py::_maintain_archive` usa
`cutoff = latest - (9h +1h)` y conserva `as_of >= cutoff`. Al último corte,
cutoff=13:35UTC. **Los1201 cortes operativos siguen retenidos**; ninguno es
elegible por edad en el horizonte completo. Sólo PREOPEN puede expirar.
Su primera elegibilidad estricta es23:20:30; a23:20:00 está en igualdad y se
conserva. Esto precisa el23:20 aproximado del análisis anterior. El intervalo
GC de15min, pins, bases y packs compartidos pueden postergar su retiro.

Por tanto el modelo usa **cero bytes de crédito GC** en todos los prefijos.
No cambia la política9h+1h ni permite borrar una base aún referida. GC=0 antes
de expiry era correcto; no explica por sí mismo el fallo histórico.

## Ecuación conservadora y obligación de prueba

Para cada corte k=1..1202 y cada estado s de admisión/publication/recovery:

```
alloc(x) = 4096 * ceil(bytes(x)/4096)   # sólo FS canónico autenticado
Live(k)  = generaciones retenidas + pins + cierre transitivo de bases
P(k)     = packs CAS íntegros alcanzables desde Live(k)
A(k,s)   = A0
         + sum(alloc(pack) for pack in P(k))
         + sum(alloc(recipe) + alloc(receipt) for retained generation)
         + alloc(HEAD/locks/intents/manifiestos/otros controles persistentes)
         + DIRECTORY_PHYSICAL(k,s)
         + CONCURRENT_TEMP_AND_RECOVERY_PHYSICAL(k,s)
         - AUTHENTICATED_WHOLE_OBJECT_GC(k,s)
U        = max(A(k,s) for all1202 k and all reachable s)
```

Un pack con componentes compartidos permanece entero hasta no tener referencias;
eliminar una receta o contar bytes lógicos repetidos no libera bloques del pack.
La profundidad máxima32 no acota los bytes acumulados ni el número de anchors.
Debe probarse además el bound lógico y la restauración exacta de cada miembro.
Los temporales en memoria no se cuentan como disco, pero todo staging/recovery
materializado concurrentemente sí; los límites RAM/RSS siguen independientes.
Si el filesystem real no es el4096 canónico, usar su allocunit autenticada,
revalidar directorios y no heredar este round-up como medición local.

Datos de formato del código existente:

| Elemento | Overhead exacto / límite a integrar |
|---|---|
|CAS pack|header12; record40 por componente; cap128MiB por pack|
|Recipe component index|40 por componente, además referencias, metadata y su representación comprimida; recipe<=64MiB|
|Page pack|header92; índice72 por página; cuerpo según codec efectivo|
|Binary pack|header88; COPY9; LITERAL5 más literal; operaciones/bounds existentes|
|Catálogo|MAX_COMPONENTS524288; miembro original<=64MiB|
|Controles concurrentes admitidos|4*65536 +6*4096 +2*262144 +8192 =819200 bytes|

Los819200 bytes son una reserva fija de admisión del código; no demuestran que
todos los temporales, directorios y recuperación estén acotados por esa cifra.
No se suman dos veces a los mismos controles físicos medidos. Las recetas y
manifiestos tienen metadatos cambiantes; su compresión no se supone constante.

El bound debe considerar todas las ramas: anchor por sequence%32, anchor por
common_depth32, ausencia de base, cambio de tamaño, delta no beneficioso y
fallback a full; page/raw-gzip, binary COPY/LITERAL y opciones XOR WIP existentes
sin promover otro codec. La elección compara costo físico **y** lógico con la
alternativa baseline. No basta acotar sólo la opción que ganó en siete cortes.

El dictionary depende de reloj/cohortes: zlib incluye DICTID, por lo que filas
decodificadas idénticas no implican wire/CID idéntico. El contraejemplo8/8 de
#479 demuestra esa implicación falsa. La observación previa5980/6000 cuerpos
CLOSED estables es antecedente del owner anterior; no fue regenerada aquí.

## Dos resultados que no se deben confundir

**Histórico af08**: run37633310327 terminó nativamente
RETENTION_ARCHIVE_CAPACITY_REACHED tras1049/1202, con535.801.856 bytes committed,
535.810.048 peak y dos temporales/intents. Los1049 incrementos y sus
timestamps/fases se verificaron contra el CSV completo de #479; suman535.801.856.
El mismo candidato ya falló antes de completar el contrato: se descarta sin
repetirlo. No se traslada ese fallo como medición de ecad.

**Forecast WIP**:499.376.128 +819.200 =500.195.328 es condicional. Da36.675.584
de margen. Con los costos reportados442.368 regular/1.777.664 anchor, el premium
es1.335.296; **28 anchors adicionales** exceden el límite. Los13 anchors CLOSED
por módulo32 son un piso programado, no techo de fallbacks.431 anchors CLOSED
a ese costo serían766.173.184 bytes: contraejemplo a la certificación por esa
proyección, **no** predicción de la carga ni prueba de imposibilidad del WIP.
Una cota superior holgada>512MiB tampoco constituye una cota inferior.

Faltan cinco inputs para U: envelopes/entropía de miembros originales de todos
los1202 cortes; ramas efectivas/fallbacks; reuse CAS e índices/recipes persistentes;
bound físico de directorios; temporales/recovery concurrentes. Se documenta el
bloqueo matemático de **certificación**, sin inventar esos valores ni ejecutar la
carga para descubrirlos. El hold ALL1202 existente permanece cerrado.

## Una propuesta correctiva acotada, sin iteración de codecs

El page-pack exacto se guarda actualmente como un único componente CAS. La única
propuesta a evaluar por el owner legítimo es **factorizar ese mismo wire mediante
las referencias CAS/recipe ya soportadas**; `_member` concatena partes antes del
decoder existente. Así podría reutilizar bytes estables sin normalizar DICTID,
recomprimir datos financieros ni cambiar el contrato de restauración.

La partición ciega de una página por componente se descarta para certificación:
4.050.944/4096=989 páginas;1202*(989+2)=1.191.182 componentes posibles antes de
otros miembros, frente a524288. Es un techo sin prueba de dedup, no una afirmación
de cardinalidad real. Tampoco certifica ahorro de bloques/índices/CPU.

Salida finita permitida antes de escribir core: una sola propuesta de factoring
con boundaries derivados del formato existente, concatenación/hash byte exactos,
reuse mínimo demostrado para todo el schedule y bound de catálogo, packs,
recipes, peak físico y profundidad. Si no cierra U<=512MiB para **todos** los
prefijos, se descarta antes de Horizon. No modificar tamaño/stride/anchors/retención
para obtener PASS, no explorar variantes indefinidas. Si faltan los inputs,
la decisión pendiente es proporcionar ese envelope auténtico o aprobar una
revisión explícita de arquitectura/contrato. En esta misión se detiene aquí.

Aceptación binaria futura: U<=536870912, ningún término UNKNOWN, catálogo dentro
del cap, restauración byte exacta/CRC/hash de todos los originales y depth<=32,
sin datos/tiempos/retención alterados. La aritmética local no acredita ese PASS.
