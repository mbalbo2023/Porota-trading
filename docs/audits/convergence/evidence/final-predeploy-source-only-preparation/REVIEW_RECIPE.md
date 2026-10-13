# Preparación de Source C y de revisión del único Predeploy V2

Estado: **SOURCE_ONLY / NO EJECUTADO**. Esta receta interna es para Codex y los revisores; no requiere que el usuario opere una terminal. No autoriza Gov, publicación, creación de PR, workflows, Docker, merge, deploy ni cambios del host. Los gates materiales BIG90, horizonte y browser siguen siendo condiciones previas. Ningún SHA, run, attempt, artifact o digest histórico se transforma en candidato final.

## Cierre de fuente y Gov 3.11 / 3.12

1. ROOT fija Source C cuando las correcciones y los gates materiales estén cerrados. Se registra su SHA y tree literales, commit Git raw y checkout completo y limpio. Cada intérprete usa un worktree Git separado, creado del mismo SHA; un `git archive` sin `.git` no satisface el guard de maximum member. No se permite un overlay, archivo ignored adicional, caché, enlace, directorio vacío ni venv bajo el checkout.
2. Los quince heads originales, su base y los objetos históricos requeridos deben existir antes de las suites. `fetch-depth: 0` del checkout no garantiza los refs de PR cerradas. El inventario existente `--fetch-source-refs` verifica/fetchea los objetos originales antes de Gov; su salida sin JUnit es inventario de fuente, no ejecución ni elegibilidad final.
3. Los intérpretes deben ser los aprobados 3.11.16 y 3.12.14, Linux, UID no-root habitual y custodia del checkout igual al UID efectivo: local1000 y CI1001 son ejemplos, no identidades forzadas. El runner exige los157 nombres/versiones exactos y ausencia de duplicados antes de fixtures, además de hashes de lock, política de acciones y plataforma. Esto no autentica todos los bytes de las distribuciones instaladas. No instala paquetes ni usa un pip compartido.
4. Cada ejecución recibe un output inexistente, absoluto y externo al checkout, en su mismo filesystem de disco. `basetemp`, venv y logs quedan externos. No `.env` ni variables operativas POROTA/PAPER/DATA_DIR/HIST_DB_PATH. `umask022`, `-I -B`, plugins automáticos y cacheprovider deshabilitados. El runner deriva `.` y la única exclusión A3 de la política actual y del blob original Git verificado, sin allowlist de nodos.
5. ROOT reserva el slot de CPU para cada suite. El runner inicia collection y execution reales en procesos nuevos del mismo intérprete, registra PID/wait4/CPU/RSS, compara los nodes exactos contra el JUnit capturado una sola vez por el parser actual y exige cero fallos, errores, skips, xfail o identidades duplicadas. Mantiene los thresholds y assertions de todos los tests originales. No hereda el conteo4802 del precedente.
6. El pin compara todos los bytes, modos y blobIDs Git con un inventario físico completo antes/después y antes de cada fase. Sólo excluye `.git` después de validar la metadata real, incluidos los pointers de worktree. Rechaza archivos untracked/ignored y directorios vacíos, aliases, mounts y miembros no regulares. Los diez campos estables de custodia de código se comparan; atime se observa por separado. El runner no afirma once stats inmutables de las bases de datos: eso lo prueban los guards nativos realmente ejecutados.

Los comandos siguientes son una receta futura y **no se ejecutaron**. `$SOURCE_C_SHA`, `$SOURCE_C_TREE`, `$CHECKOUT_C`, `$RAW_C` y `$PREPARATION` son valores que ROOT fijará; los outputs deben ser nuevos. El paquete contiene el runner externo y sus hashes, no el entorno ni la fuente final C.

```bash
umask 022
python -B "$CHECKOUT_C/scripts/rc6_convergence_provenance.py" \
  --repo-root "$CHECKOUT_C" --candidate-sha "$SOURCE_C_SHA" \
  --out "$RAW_INVENTORY" --fetch-source-refs

/workspace/venv_rc6_frozen311/bin/python -I -B \
  "$PREPARATION/rc6_run_frozen_governed_v2.py" \
  --repo-root "$CHECKOUT_C_311" --source-sha "$SOURCE_C_SHA" \
  --source-tree "$SOURCE_C_TREE" --output-root "$RAW_C_311" --timeout-seconds 5400

/workspace/venv_rc6_py312/bin/python -I -B \
  "$PREPARATION/rc6_run_frozen_governed_v2.py" \
  --repo-root "$CHECKOUT_C_312" --source-sha "$SOURCE_C_SHA" \
  --source-tree "$SOURCE_C_TREE" --output-root "$RAW_C_312" --timeout-seconds 5400
```

Las rutas de intérprete son las disponibles al preparar esta receta; antes del slot se vuelven a comprobar identidad, versión y157. No se usa el runner viejo `/tmp/rc6_run_frozen_governed.py`: su Gov JSON omitía candidateSHA/tree/source_unchanged/JUnitSHA/bytes que el binder actual exige, aunque existían en otro receipt. El runner anterior preparado SHA31cef48c también se conserva sólo como SOURCE_ONLY: no probaba el namespace físico completo.

Después de cada Gov se vuelven a ejecutar, sobre la misma fuente C y los mismos JSON/XML raw, los CLIs comprometidos `scripts/rc6_issue465_audit_gate.py verify --repo-root ... --junit ... --governed ... --out ...` y `scripts/rc6_convergence_provenance.py --repo-root ... --candidate-sha ... --junit ... --out ...`. Finalmente se llama a `final_material_receipt_binding(root, SHA, TREE, rawFIP, rawGov, actualJUnitPath)`. Exige software_status `EXECUTED_NATIVE_GREEN`, eligible/materialclosed tipos True exactos, pending[] exacto, candidateSHA/tree, int zero, hash/bytes/conteo del mismo JUnit y exclusión original c27. No se modifica el XML ni se elimina un test para cerrar el gate.

La auditoría de red del runner cubre DNS y sockets INET del proceso pytest y executables conocidos, desde que instala su hook antes de importar pytest. Permite AF_UNIX. No es una atestación kernel de procesos hijos. La revisión de fuente encontró los tests de DNS/sockets con sus propios parches que bloquean antes del syscall y no encontró listeners localhost reales en tests/conftest; esto es **SOURCE_REVIEW_ONLY**, no una suite ejecutada ni prueba final de compatibilidad. Si Gov expone un uso local legítimo o un bloqueo de auditoría, se conserva el RED y se resuelve su causa, sin ignorar tests ni reetiquetar intentos de red como cero.

## Única construcción oficial

ROOT confirma nuevamente el producto, ownership y autorizaciones cuando Source C cierre. El workflow canónico es `.github/workflows/porota-predeploy-v2.yml`, evento `pull_request` al branch `deploy/rc6-pr69-isolated-20260915`; no tiene workflow_dispatch. La creación/sincronización del PR dispara ese evento, por lo que no se publica antes de cerrar los gates.

El workflow checkouta el head literal con Git completo, trae refs originales, instala con `--require-hashes`, descubre desde repo-root y registra el JUnit raw. El Gate original y el FIP/material join se ejecutan **antes** del único `docker build`. La auditoría installed/hashlock está después de Gov, pero antes de build y falla cerrado; el preflight local157 se hace antes de fixtures. El workflow oficial usa3.11.16; el cierre local3.12 es un gate separado, no una segunda construcción.

El import smoke y el codec/archive smoke de once casos corren sobre el ImageID real de esa construcción. El segundo exige UID1000, read-only, cap-drop ALL, no-new-privileges, network none, sin mounts del host, tmpfs privado32MiB, plazo30s y salida64KiB. Los fixtures legacy y el script están ligados a blobs/source hashes. Los receipts focales Python no se presentan como ejecución dentro de la imagen.

Se exportan el bundle y el tar de la misma imagen. Frozen liga SHA/tree, SourceManifest, imagen, bytes, origen externo, raw finalFIP y tiny receipt. El primary tiene nombre exacto `porota-predeploy-v2-${SHA}-run-${RUN_ID}-attempt-${ATTEMPT}` y es el único paquete promotable. El upload `if: always()` puede publicar diagnóstico de un run fallido: **presencia o nombre no lo valida**. Sólo un run/attempt final SUCCESS y los once miembros obligatorios permiten la revisión final.

## Revisión independiente del primary

1. Capturar API GitHub fresca del workflow canónico, run, attempt, artifact y lista completa de artifacts. Rechazar los denied heads/runs/artifacts del binder y cualquier selección por `latest` o nombre ambiguo. Verificar repository IDs, workflowID/path activo, evento pull_request, único PR/base/head, SHA C, attempt literal, completed/success, nombre exacto, ID, digest, tamaño, fecha dentro del attempt y expiración. Registrar definición efectiva del workflow y jobs/logs oficiales; su revision puede ser el merge ref del PR, y no se asume que sea el head C.
2. `validate_binding(..., require_completed=True)` produce el binding sólo de revisión desde ese tuple independiente. No usar `locate` ni inventar trailers: ese CLI lee una aprobación futura del merge commit y pertenece a promoción. La revisión read-only no autoriza un merge ni deploy.
3. Descargar exactamente `/repos/mbalbo2023/Porota-trading/actions/artifacts/${PRIMARY_ID}/zip` a un archivo nuevo de disco. Preferir `porota_artifact_http.download_artifact()` con expected_size/digest de API y deadline total acotado: token sólo en env, HTTPS, auth removida en redirects y sin URL/headers/tokens en logs. `gh api` desde executor es una alternativa de transporte futura sólo si funciona; no se descargaron los408MB históricos para probarla. El límite32MiB de transferencias MCP no se interpreta como límite del artifact oficial.
4. Validar size y digest SHA256 del ZIP externo **antes** de usar sus manifiestos. `safe_extract` comprueba CRC completo, duplicados físicos, paths, aliases, cifrado, bounds y reserva; exige exactamente una copia de cada uno de los once required files. El output es externo e inexistente y el checkout de revisión es el Git íntegro de C con sus refs originales.
5. El CLI existente revalida source→bundle→tar/config/layers/diffIDs, CRC y modos, rawGov/JUnit/FIP original y el receipt tiny ligado a fixtures/ImageID. No ejecuta Docker:

```bash
python -B "$CHECKOUT_C/scripts/porota_predeploy_binding.py" verify \
  --candidate-sha "$SOURCE_C_SHA" --tree-sha "$SOURCE_C_TREE" \
  --binding "$REVIEW_BINDING" --zip "$PRIMARY_ZIP" \
  --output-root "$PRIMARY_EXTRACT_NEW" --repo-root "$CHECKOUT_C" \
  --receipt "$PRIMARY_BYTE_REVIEW_RECEIPT"
```

El texto del CLI usa `approved` por compatibilidad; este paso no sustituye aprobación de merge. Un reviewer autorizado para el replay Docker usará luego el `verify_loaded_image_and_imports` actual: `docker load` del tar ya publicado, inspección que iguala **actualLoadedID==frozenExpectedID antes de ejecutar**, imports offline y once casos sobre ese mismo ID, sin rebuild. Esta receta no ejecutó Docker.

## Evidence-only <=32MiB y su límite de confianza

Después del upload primary, el **mismo run** ya contiene el paso `porota_published_artifact_evidence.py`: obtiene ID/digest externos, vuelve a descargar el ZIP exacto, valida los once miembros, aplica capas ordenadas/whiteouts y compara todos los archivos y modos `/app` contra SourceManifest, carga la imagen real y repite imports+tiny sobre el ImageID exacto. No hay segundo build. El deadline del paso es600s; actualmente su helper `remaining()` limita también cada subprocess genérico a30s, incluyendo `docker load`. Es un riesgo de duración inferido por fuente, **no un fallo medido**. El límite30s propio del tiny sigue siendo contrato independiente.

El secondary `porota-predeploy-v2-evidence-only-${SHA}-run-${RUN_ID}-attempt-${ATTEMPT}` contiene exactamente once archivos: los nueve JSON/XML de REQUIRED_FILES, `published-primary-verification.json` y `evidence-inventory.json`. Omite ambos tar. Los globs de upload son exclusivamente `*.json` y `*.xml`. Su inventario cubre diez payloads anteriores al propio index; no se exige el hash recursivo del index. El límite raw total es24MiB y el tamaño API ZIP es32MiB.

La revisión del secondary debe verificar su API ID/name/digest/size/origen/run/head/attempt/expiry, ZIP size/digest/CRC/paths/aliases/duplicados y la whitelist exacta. Debe recomputar los diez hashes y sizes del index, preservar raw XML, unir Gov/FIP/JUnit contra Git C y comparar los manifests y lista rootfs al Git literal. También valida el raw replay_report tiny con el script/fixtures de C y la igualdad de todos los ImageIDs. El primary binding anidado declara `IN_PROGRESS_EVIDENCE_ONLY` porque se generó dentro del job: la revisión externa debe refrescar completed SUCCESS del run/attempt, no tratar ese estado anidado como aprobación final.

El secondary nunca es promotable ni bootstrap de confianza. Sin los tar, ROOT no puede recomputar localmente CRC de imagen, capas ni filesystem completo sólo a partir de él. Esa parte queda como `RUNNER_VERIFIED_PRIMARY_BYTES` ligada al tuple API y a la definición/jobs/logs oficiales verificados; no como `ROOT_INDEPENDENTLY_REPLAYED_PRIMARY_BYTES`. Si ROOT logra descargar el primary completo, revalida directamente sus bytes y reporta ese alcance real. Ningún receipt JSON autoafirmado sustituye la autoridad externa.

## Paquete y cierre

El MANIFEST del paquete liga SHA256/bytes/modos de cada fuente preparada y snapshot Git revisado. SOURCE_PLAN registra tree/blobs raw, los14 bloques Python del workflow con compilación AST solamente, la única construcción declarada y todos los límites de evidencia. No contiene una ejecución Gov, ImageID observado, artifact aprobado, Source C final ni permiso operativo. Tras cambios de fuente/runtime o workflow, ROOT rebindeará el snapshot preparado al pin C y ejecutará los gates correspondientes en su slot.

La positiva health pequeña queda separada del caso grande: esta preparación no cierra health12k ni transforma un receipt Python en salud de la imagen. La imagen final necesita su prueba real sobre el ImageID observado; se registrará el alcance y el cut realmente usado, sin tomar un GREEN pequeño como validación del caso grande.
