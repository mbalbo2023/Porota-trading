# Diagnóstico V2 de componentes — SOURCEOWN

Base de desarrollo: ROOT `003dccb5`, que integra el codec V2 `397447fa`.
Scope: el probe `tests/ci_rc6_projection_browser_diagnostic.py` y sus guards
específicos. Ningún cambio en producto dashboard, codec, persistencia, broker,
política financiera, PPI Watch ni runtime. El checkpoint de este probe todavía
no certifica un productor grande, un archivo de nueve horas ni latencia UI.

El diagnóstico ejecuta el runner nativo de navegador sobre un archive completo
y un corte externo ya publicado. Mantiene su deadline absoluto de un segundo,
su cuota, filtros, verificación de los cuatro roles, GC, retornos y errores.
No construye un writer. La ventana de diagnóstico es de 60 segundos y termina
al observar el primer fallo real o al alcanzar el control siguiente de ventana;
la llamada nativa ya iniciada y el cierre del navegador conservan sus propios
límites y cleanup. Un fallo real tiene precedencia sobre el fin de la ventana.

La salida usa `rc6.native-browser-components-diagnostic.v2`. Siempre declara
`acceptance_complete=false` y `native_gate_acceptance_claim=false`. El código
de salida cero significa que el diagnóstico y su prueba de integridad de fuente
concluyeron; no significa aceptación del producto. Los RED anteriores de 594,
af71 y 88df mantienen sus bytes y alcance originales.

`stage_aggregates` contiene llamadas, terminaciones, errores por clase, tiempo
acumulado/máximo y CPU del hilo para `fresh_unpack_wire`, V1 si corresponde,
slots, expansión de instancias, arrays, inflate, captura SQLite, generación,
query y lecturas de cada miembro. El contexto de rol/digest es local al hilo;
report y checkpoint no se mezclan cuando el verificador los procesa en paralelo.
Los contadores de paquetes, templates, literales, instancias, bindings y refs
son declaraciones anteriores a la validación nativa. Se informan por separado
de las llamadas completadas y errores. No se retienen sus bytes, literales ni
el JSON expandido, y no existe un evento de diagnóstico por slot, binding o ref.

Las llamadas `_bytes` de los cuatro miembros se cuentan por rol/digest y tamaño
devuelto. Es observación de lecturas; no constituye una prueba independiente de
ausencia de cache ni de semántica lógica completa. La verificación sigue a cargo
del reader canónico y conserva `WIRE_AND_PROJECTION_SEMANTICS`. El GC se observa
mediante un callback agregado por hilo, generación y render. Las funciones y
callbacks originales se restauran al salir, también ante errores.

Los tiempos de etapas anidadas o concurrentes no se suman. La instrumentación
añade costo; los contadores de cgroup incluyen otros procesos del contenedor.
Las dos preflight del runner siguen capturando SQL y wire en serie, mientras
`build_page` usa el overlap real por request. Los registros con render nulo
se identifican como `PREFLIGHT_OR_OUTSIDE_RENDER`; no deben presentarse como
latencia de un render bajo Chrome.

El próximo uso grande requiere un nuevo OPEN completo producido con la fábrica
canónica y ARCH V3 del candidato integrado/frozen. El codec V2 puede estar sólo
en algunos roles: status pequeño puede ser `PLAIN_JSON`; no se fuerza el formato
de un corte real. El probe anterior V1 y los tiempos de 18e no prueban el costo
del nuevo código ni de sus nuevos bytes. Antes y después se verifican todos los
archivos del archive, la custodia/DB externa, imports del archive, cero red y
cero apertura SQLite de la fuente.

Los guards nuevos usan el PAPER caller y publisher/reader canónicos en un
dataset privado. Sólo fuerzan el umbral de representación soportado para
ejercitar V2 en los tres roles; no inventan reports, identidades, shapes,
valores financieros ni clocks. El CLI pequeño debe preservar su rechazo antes
de importar/arrancar Playwright y mantener ambos campos de aceptación en false.
Guards locales: 20/20 PASS, cero skip/failure/error, 11.282 segundos. Receipt:
`diagnostico-v2-local-receipt.json`; JUnit original
`/tmp/rc6-ux-diagnostic-v2-guards.xml`, SHA256
`91a06e2ab0a77d387ae1f589c38e6e6f0c929b41e949df8df0ed49c288841393`.
El CLI pequeño quedó preservado en `diagnostico-v2-small-native-cli.json`, con
rechazo `NATIVE_LARGE_REPORT_NOT_EXERCISED`, fuente/custodia intactas y ambos
campos de aceptación en false. BIG, medición V2 bajo Chrome, archivo de nueve
horas y browser final continúan pendientes.
