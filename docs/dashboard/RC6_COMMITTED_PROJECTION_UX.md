# Proyección SHADOW comprometida y paginación UX

El dashboard consume `read_committed_projection` del productor canónico. Su
cuarto miembro derivado pertenece al mismo CURRENT, manifest, generation ID,
sequence, corte, watermark, configuración y custodia local que report,
checkpoint y status. La proyección contiene filas y agregados de presentación;
no crea un ledger, una autoridad financiera ni un cache que cambie el corte.

El adaptador exige `rc6.shadow-ui-committed-projection.v1`, los cuatro digests
verificados y las tres derivaciones originales. Verifica tipos de safety,
headers, cardinalidades, límites y pertenencia de las filas visibles a los
filtros publicados. Una contradicción rechaza la página completa. Nunca
descarta filas para ajustar artificialmente el total entregado por el lector.

El nivel expuesto es `WIRE_AND_PROJECTION_SEMANTICS`, con
`LOCAL_DURABLE_CUSTODY_NOT_EXTERNAL_AUTHENTICATION`. El lector nativo verifica
todos los bytes/hash/CRC y contratos de los cuatro miembros antes de consultar
el índice privado en memoria. La validación lógica completa de los originales
corresponde al productor antes del sello y al lector completo de auditoría; el
render no afirma expandir/recomputar esos originales. El contrato del productor
está en [RC6_COMMITTED_PROJECTION_CODEC_CONTRACT.md](../audits/RC6_COMMITTED_PROJECTION_CODEC_CONTRACT.md).

Cada dataset mantiene su total exacto sobre todos los matches y muestra hasta
diez filas. Currency, market, family, settlement, estrategia, búsqueda literal
y full identity siguen el contrato publicado por cada fuente. Una dimensión
sin asociación publicada devuelve `FILTER_NOT_PUBLISHED_FOR_DATASET`: la UI
muestra total desconocido y la población original declarada por el productor,
sin presentar un cero factual. State filtra planner; cohort y session filtran
el embudo. Identidades de planner y labs conservan sus órdenes nativos y se
normalizan sólo para el selector de presentación.

El embudo pagina todos sus grupos en bloques de diez con un offset independiente
de las mesas. Elegir la página siguiente conserva selected, counts y todos los
denominadores. Elegir una cohorte usa su digest nativo y reinicia ese offset.
Las tarjetas y el widget utilizan la misma selección. Etapas ausentes en una
cohorte factual permanecen desconocidas: por ejemplo, una decisión nativa puede
publicar PAPER_OPENED sin publicar CATALOG_READY. El guard no convierte esa
ausencia en cero ni infiere READY de una señal o una ejecución histórica.
Un offset fuera de la población muestra grupos 0–0, conserva el total real y
mantiene las tarjetas de la selección.

Store y la API comparten un deadline monotónico absoluto de un segundo; el
adapter rechaza deadlines no finitos o booleanos y limita el resultado a
4 MiB. Si la lectura vence, el cuerpo se descarta y el shell no vuelve a abrir
la generación. `readonly_copy(validate=False)` conserva la fuente SQLite y
sus sidecars; el índice derivado abre bytes verificados sólo en memoria.

La revisión financiera independiente añadió un caso importante: un índice SQL
con currency diferente de la identidad del payload podría seleccionar la fila
equivocada si su derivación no liga esos campos. El productor `99bcd846` liga las
17 columnas del índice mediante el digest Merkle; UX agrega rechazo acotado para las filas visibles y
selected/groups del embudo. Este guard UX no sustituye la corrección del
productor ni autentica externamente la custodia local.

Las pruebas positivas usan los escritores PAPER/SHADOW y lectores reales.
Sólo las pruebas negativas alteran copias de sus resultados. El navegador
verifica las 49 subvistas por seis anchos, los 22 aliases, foco/refresh/nombres
accesibles para Voice Access, la selección y paginación de cohortes y el alcance de verificación
visible. El receipt separado identifica base integrada, source blobs, pruebas,
SHA de dependencias y limitaciones; no reemplaza el receipt del primer
incremento ni declara un artefacto productivo congelado.

`tests/ci_rc6_projection_large_reader.py` está preparado para el artefacto nativo
terminado de 12000 identidades/60000 observaciones. Compara la población completa
del índice con las full identities de una copia privada de la DB; prueba las
páginas inicial, siguiente y final, búsqueda y full key final, grupos finales y
selección de la última cohorte. Mide consultas y render completo, bloquea red y
decode lógico de report/checkpoint, y compara bytes/hash/metadata/atime de DB y
custodia antes/después. Distingue población completa de consultas UI muestreadas;
no afirma haber medido todas las páginas secuencialmente.

El harness rechaza un corte cuya fase publicada no sea OPEN y exige al menos
12000 identidades y 60000 observaciones para una conclusión positiva. Su guard
SQLite bloquea la ruta fuente, URIs con parámetros o escapes, enlaces y sidecars;
las conexiones a copias privadas y memoria siguen habilitadas. Una apertura
fuente bloqueada también invalida la conclusión aunque el caller capture la
excepción. El receipt `harness-source-guard-local-receipt.json` identifica las
24 pruebas focales y el rechazo esperado del fixture pequeño, cuya DB y custodia
permanecieron idénticas. Es un control negativo del harness, no evidencia de
capacidad del productor grande.

`tests/ci_rc6_projection_large_browser.py` recibe esa misma DB y raíz congeladas;
no crea un fixture, no supervisa posiciones ni llama a un writer. Está preparado
para las 49 subvistas por seis anchos, 22 aliases, foco/refresh/interacciones,
páginas y cohorte finales, y búsqueda de la última identidad. Mide cada render
completo y rechaza un cuerpo descartado por deadline o contrato inválido.
Su salida debe ser un directorio nuevo fuera de la fuente y la custodia.
Las siete pruebas de preparación verifican el rechazo del corte pequeño antes
de importar Playwright y de destinos inseguros antes de SQLite. El receipt
`large-browser-preparation-local-receipt.json` marca explícitamente que
Chromium todavía no fue ejecutado sobre el artefacto grande.

El gate grande sigue abierto hasta ejecutar ese harness sobre el productor
final y obtener ≤4 MiB por solicitud y ≤1 s por consulta/render. Los tiempos
del fixture pequeño y el deadline de falla no prueban esa capacidad. La validación
integrada ejecutará además imports, mounts/imagen y navegador sobre un único SHA/tree y
artefacto congelados. Este incremento no modifica los doce tests protegidos
de #344, el enganche o_dashboard, fuentes de PPI Watch/#107 ni runtime productivo.
