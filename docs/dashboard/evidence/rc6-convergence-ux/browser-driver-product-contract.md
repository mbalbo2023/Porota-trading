# Browser DRIVER / PRODUCT — SOURCEOWN

Base local: ROOT `d9e7fb7d`. El cambio pertenece exclusivamente a runners,
transporte stdio, guards y evidencia. No modifica dashboard, writers, codec,
contratos financieros, políticas, GC del producto ni PPI Watch.

El DRIVER importa stdlib y Playwright. El PRODUCT es un proceso distinto con
el intérprete declarado en `--product-python`. Antes de cualquier fixture o
import financiero valida `sys.executable`, su prefix, Python soportado y las
157 distribuciones/versiones exactas de ambos locks. Los guards admiten 3.11
y 3.12; el comando final declara `--product-python-version 3.11`. El driver
puede tener 158 distribuciones, incluida Playwright, y no ejecuta el producto.

El protocolo `rc6.browser-product-ipc.v1` usa un ID secuencial y una respuesta
UTF-8 JSON menor que 4 MiB por solicitud. Rechaza claves duplicadas, NaN,
framing incompleto, IDs incorrectos y campos/operaciones no publicados. Las
respuestas sobredimensionadas se rechazan completas. Cada render invoca el
`build_page` nativo; no guarda HTML/JSON ni snapshots financieros entre calls.
La observación del retorno del reader exige el mismo CURRENT, cut,
configuration fingerprint y los cuatro digests antes de exponer el cuerpo.

El driver mide el roundtrip desde antes de serializar la solicitud hasta
después de recibir/decodificar la respuesta. La aceptación mantiene un segundo
total, incluido IPC, captura, handler, HTML y JSON. El deadline y cleanup del
Store nativo permanecen intactos. Un exceso descarta el cuerpo aunque el
transporte espere el cierre nativo para conservar evidencia. El gate de salud
mantiene dos segundos. Timeouts, muerte del hijo o errores no recuperan un
cuerpo anterior ni reabren la fuente. Se cierran/join procesos, pipes y el
drain de stderr en las salidas de éxito y de error.

LARGE usa exclusivamente DB/root externos de un OPEN grande completo. NORMAL
construye la fixture canónica dentro del child y conserva su supervisión y
`gc.collect` original antes de medir lectura. El NOGATE V2 instrumenta sólo el
child nativo; sus wrappers y callback se restauran. Su ventana sigue siendo
60 segundos y siempre mantiene `acceptance_complete=false`.

El pin completo RAW `rc6.complete-archive-source-pin.v1` verifica todos los
SHA256, blob IDs y modos 644/755 y reconstruye el tree de Git real. Los imports
del producto deben proceder de ese mismo source sin overlays. Ambos procesos
comparan hashes/modos antes/después; el child verifica la custodia/DB externas
y bloquea network/SQLite sobre la fuente. El driver bloquea toda SQLite y red
Python; las peticiones del navegador siguen interceptadas y sin proveedores.
El índice legacy se identifica como parcial y nunca satisface el pin FINAL;
los CLI finales exigen el RAW completo antes de iniciar el proceso nativo.
La comprobación de imports acepta sólo ROOT, el prefix declarado y stdlib,
independientemente de que una fuente ajena esté en `/tmp` o `/workspace`.

Las rutas de salida se validan antes del bloque que guarda un fallo: rechazar
una ruta bajo SOURCE, existente o con un parent symlink no crea ningún JSON.
El envío usa escritura no bloqueante con la misma deadline absoluta desde
antes de serializar. Un stream desincronizado se descarta y se cierra; no se
intenta recuperar una respuesta. El proof de finish permanece parcial hasta
que el hijo termina con exit code 0 y cleanup no forzado. Exit 1 o timeout de
cleanup produce un fallo tipado aunque el frame de finish declare proof true.

Los nuevos negativos de transporte emplean un peer de protocolo con entorno
157 real, sin fixture ni payload financiero. Los positivos de handlers usan
el caller/publisher canónico dentro del child. Ambos alcances se registran
por separado; ni la revisión de fuente ni esos negativos prueban latencia
del catálogo grande.

El comando LARGE preparado, después de integrar/congelar la fuente, obtener
un nuevo BIG OPEN y liberar SLOT, es:

```sh
/workspace/venv_rc6/bin/python -I -B "$FINAL_SOURCE/tests/ci_rc6_projection_large_browser.py" \
  --product-python /workspace/venv_rc6_frozen311/bin/python \
  --product-python-version 3.11 --index "$FINAL_RAW_INDEX" \
  --database "$FRESH_BIG_DB" --root "$FRESH_BIG_ROOT" --output "$NEW_PRIVATE_OUTPUT"
```

Los 49 paths por seis tamaños, 22 aliases, Voice Access, foco, scroll,
deep links, refresh pendiente, auto-refresh, tres páginas de cohortes,
selección final e identidad final mantienen sus checks nativos. Los filtros
y denominadores conservan el alcance homogéneo documentado de la fixture
grande. Este documento describe fuente preparada: no afirma BIG/browser
aprobados, medición temporal ni final freeze.
