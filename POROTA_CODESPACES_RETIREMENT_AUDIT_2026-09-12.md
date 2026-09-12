# Retiro de referencias a Codespaces — 2026-09-12

## Hallazgo

El baseline operativo RC6 se identifica en el checkpoint canónico del 11/09 como `17.0.0-rc6`, SHA desplegado/certificado `f8adec8a02b2f9f0ef2dffbee75458c958bf711e` y árbol `656a5a77f7e65baefe0ee0b0f1ed9e8c84ab61b1`. En ese source no existen `.devcontainer/` ni `codespace-test.sh`; su README y `ci.yml` tampoco contienen referencias a Codespaces. El bot desplegado no depende de Codespaces.

La configuración sí sigue viva en `main`:
- `.devcontainer/devcontainer.json` se titula “Porota Trading v16.1 — Codespaces”, configura una imagen de desarrollo, Docker-in-Docker, Python, hooks post-create/post-attach y usuario `codespace`.
- `.devcontainer/post-create.sh` y `.devcontainer/welcome.sh` presentan el entorno como v16.1 y de testing/desarrollo.
- `README.md` aún recomienda abrir un Codespace y ejecutar `codespace-test.sh`.
- `codespace-test.sh` construye y levanta Compose para una prueba local, consulta health y luego baja el stack; es un test manual del entorno, no el despliegue del bot.

Por eso GitHub todavía ofrece una configuración de Codespaces al partir de `main`. No es una dependencia de producción ni se inicia automáticamente.

## Ramas

| Referencia | Estado | Recomendación |
|---|---|---|
| `codespace-setup`, `codespace-setup-v2`…`v6`, `infra/v16.1-codespaces-ready` | Siete referencias al mismo commit `f39cadaac8bff62f7176e10b80ffda80f6ee6f9b`; árbol vacío, 1144 commits detrás de RC6; sin PR ni ejecuciones observadas. | Conservar una referencia canónica temporal y retirar las otras seis luego de revisar reglas de repositorio. |
| `codespace-config` | Diverge de `main` (11 commits propios / 15 de `main`); añade siete archivos de configuración/desarrollo; sin PR. | No es una dependencia activa de RC6. Retirar o archivar después de comprobar las diferencias genéricas de Docker y requirements. |
| `chore/disable-codespaces-ci` | Una modificación única de `.github/workflows/ci.yml`; sin PR. | No está integrada ni activa por sí sola. Revisar el cambio y después retirar la referencia si resulta redundante. |
| `infra/production-paper-dispatcher-main-20260905` | Cabeza del PR #13, abierto y en borrador hacia `main`; no es una rama de Codespaces. | Mantener mientras el PR siga abierto. |

## Limpieza sugerida, aún no ejecutada

1. Abrir un PR de housekeeping a `main` que quite `.devcontainer/`, `codespace-test.sh` y el bloque de instrucciones de Codespaces en `README.md`. Mantener intactos archivos genéricos de Docker/requirements si tienen uso fuera de Codespaces.
2. Verificar que la eliminación no cambie el CI normal de `main`; no desplegar la aplicación por este cambio.
3. Retirar los seis alias de rama exactos y conservar por ahora una sola referencia.
4. Resolver `codespace-config` y `chore/disable-codespaces-ci` tras revisar sus cambios únicos.
5. Mantener historial de commits/tags; no reescribir historia.

En las ramas consultadas GitHub devuelve `protected: false`, pero la lectura de rulesets no está disponible. No se borró ni modificó ninguna referencia. Este checkpoint no incluye el circuito paralelo expresamente excluido.

## Checkpoint de ejecución — 2026-09-12

Se abrió el PR borrador [#56](https://github.com/mbalbo2023/Porota-trading/pull/56) desde `chore/retire-codespaces-config-20260912` hacia `main`. HEAD: `0eb29dde4c4771e85a504b52e54f5220e445c415`; base: `20f2365a87d896acf0c215931e043b185210706f`.

El diff contiene únicamente cinco rutas: eliminación de `.devcontainer/devcontainer.json`, `.devcontainer/post-create.sh`, `.devcontainer/welcome.sh` y `codespace-test.sh`; además, actualización de `README.md` para quitar las instrucciones de Codespaces y la referencia v16.1, dejando RC6 como baseline operativa.

El PR permanece abierto como borrador, sin fusión ni despliegue. No se tocaron código runtime, workflows, RC6 ni el trabajo paralelo de ingesta. La comparación contra `main` confirmó cinco rutas y cero commits de diferencia detrás de la base al momento de crear el PR.

### CI del PR #56

Run `34709256011` (`CI — Porota Trading`, run #251): finalizó `success`. `Infrastructure preflight` pasó `Validate repository safety` y `Validate Docker Compose syntax`. En `Application CI`, la detección confirmó que el paquete de aplicación no está instalado y los pasos de dependencias, pruebas y stack quedaron `skipped`. No hubo paso de despliegue ni de ingesta.

## Checkpoint de gobierno de trabajo paralelo — 2026-09-12

Se preparó el PR borrador [#57](https://github.com/mbalbo2023/Porota-trading/pull/57), desde `docs/parallel-chat-policy-20260912`, con exactamente dos archivos nuevos: `AGENTS.md` (política raíz v`2026-09-12/1`) y `.github/pull_request_template.md` (lectura/acuse, checkpoint, responsable, rama/base SHA, rutas, validaciones e impacto). HEAD: `b0ca7b4bb45c37c3ebae41aaa4265f15bd1261fc`. El CI run #252 estaba `in_progress` al revisar; no se modificaron workflows.

La política requiere leer el checkpoint canónico y confirmar desde allí el SHA/tag RC6; prohíbe que otros chats consulten o modifiquen recursos del workstream `pipeline ppi watch`; exige ramas separadas y reserva integración/merge/deploy a un coordinador. El PR-template deja un acuse visible. Esto es una instrucción automática para agentes compatibles con `AGENTS.md` y una barrera procedimental en PRs, no una prueba criptográfica de que cada chat leyó el archivo.

Inspección de permisos: `GET branches/main/protection` respondió 403 (`Resource not accessible by integration`); `GET rulesets` respondió 403 indicando que rulesets no están disponibles para este repositorio/plan. Por tanto no se puede confirmar desde esta conexión una protección obligatoria de `main` ni un required-review gate.

Se listaron tres workflows en `main`: `ci.yml`, `deploy.yml` y `promote-to-production.yml`. Los dos últimos declaran solo `workflow_dispatch`; el CI de PR #56 pasó (run #251). PR #56 continúa abierto como borrador, mergeable/clean en GitHub, sin fusión ni despliegue. Antes de integrar, falta resolver el gate de protección/una revisión del responsable; no se accedió a ejecuciones ni recursos del workstream PPI.

## Integración en main — 2026-09-12

La política de trabajo paralelo entró primero mediante squash del PR [#57](https://github.com/mbalbo2023/Porota-trading/pull/57), commit `049deebc99fdb4c8c90566a59a31df2b44da97d8`. Después de revalidar su alcance y acuse conforme a esa política, se integró por squash la limpieza de Codespaces del PR [#56](https://github.com/mbalbo2023/Porota-trading/pull/56), commit `82750cc0b97df69f9936bdd5d65360cd8f9020f0`. Este es el HEAD actual de `main` al registrar el checkpoint.

Verificación en `main`: `AGENTS.md` y `.github/pull_request_template.md` existen; las cuatro rutas dedicadas a Codespaces ya no existen; el README conserva RC6 como baseline y no tiene menciones de Codespaces ni v16.1.

El CI de push a `main`, run #254, terminó `success`: preflight de seguridad y Compose correctos; CI de aplicación omitido porque el paquete no está instalado. No se disparó ningún despliegue: `deploy.yml` y `promote-to-production.yml` son manuales (`workflow_dispatch`). No se tocó el runtime RC6 ni recursos del workstream PPI.

Sigue pendiente verificar protecciones de rama desde una conexión con permisos adecuados y continuar la clasificación de ramas históricas. No borrar ramas hasta comprobar propietario, PRs y dependencias de workflows.

## Inventario de ramas Codespaces posterior a la limpieza

La búsqueda de refs todavía encuentra siete alias históricos: `codespace-setup`, `codespace-setup-v2` a `-v6` e `infra/v16.1-codespaces-ready`. Los seis alias sin prefijo `infra/` apuntan al mismo SHA vacío `f39cadaac8bff62f7176e10b80ffda80f6ee6f9b`; la comparación actual de `codespace-setup` e `infra/v16.1-codespaces-ready` contra `main` los deja en cero commits ahead y 17 behind. Los seis branches consultables devuelven `protected:false`; la política de rulesets global no pudo verificarse por las limitaciones de GitHub descritas arriba.

`codespace-config` continúa divergente (11 commits ahead, 17 behind); su lado contiene historial de configuración/desarrollo, por lo que no debe borrarse por equivalencia con los alias vacíos. `chore/disable-codespaces-ci` continúa divergente (1 ahead, 3 behind) y su commit pendiente toca `.github/workflows/ci.yml`; no tocar ni borrar hasta revisar la diferencia con la política CI vigente y confirmar dueño/dependencias.

El conector GitHub disponible no expone una operación de borrado de ramas. No se borró ninguna rama histórica. Las ramas de trabajo de los PRs ya integrados también permanecen como refs; se podrán retirar cuando exista una operación compatible y se cierre su checkpoint. Próximo paso: revisión de workflow/PR de la rama CI histórica y clasificación de ramas RC antiguas, siempre sin entrar en el workstream PPI.

## Aclaración de la rama testing y permisos — 2026-09-12

### Baseline RC6 reconciliado

El checkpoint canónico `POROTA_TRADING_RC6_CHECKPOINT_AUDITORIA_2026-09-11.md` identifica `17.0.0-rc6`, runtime `PRODUCTION_PAPER`, ejecución `SIMULATED`, órdenes reales `BLOCKED`, source desplegado/certificado `f8adec8a02b2f9f0ef2dffbee75458c958bf711e` y árbol `656a5a77f7e65baefe0ee0b0f1ed9e8c84ab61b1`.

La certificación final fue `RC6 Final Postdeploy Certify 2026-09-10`, ejecutada el 2026-09-11 entre 21:55:48Z y 21:57:23Z en `fix/rc6-w10-sector-map-binding-20260910`. El HEAD de ese workflow era `0eb114c950ef6c5f04ac99beb34ad175498756e0`, pero el checkpoint aclara que no es el source desplegado: `DEPLOY_EXPECTED_SHA` y `HOST_SHA` coinciden en `f8adec8a...`; el resultado fue `RC6_FINAL_POSTDEPLOY=GREEN`.

La consulta de GitHub confirmó que `f8adec8...` es un commit existente y que su `_version.py` declara `17.0.0-rc6`, `PRODUCTION_PAPER`, `SIMULATED` y `BLOCKED`. El commit `a47f3339...` es otro commit y su cambio propio es únicamente el archivo `.github/swing-options-telemetry-after-eodonly-deploy-request.txt`; no sustituye el SHA de despliegue certificado. La rama `release-candidate/v17.0.0-rc6-deploy3-20260906` apunta a `5bdad270...`, una referencia anterior a la certificación del 11/09. La consulta previa de tags encontró `v17.0.0-rc3-hf6`, sin un tag RC6. El SHA inmutable que debe usarse para identificar el baseline es `f8adec8...`; queda pendiente dar a RC6 una referencia Git inmutable clara, sin crearla en esta auditoría.

El commit `f8adec8...` contiene `.github/workflows/rc6-cp4-a3-zero-candle-forwardfix-20260911.yml`. La inspección estática encontró `workflow_dispatch`, acceso SSH, una ruta de promoción de overlay y el inicio de un servicio; fija `TARGET_HOST_SHA=e47eeff...`, ya superado por el checkpoint canónico. Es un flujo operacionalmente sensible y con una referencia obsoleta. No se ejecutó ni se modificó; requiere revisión específica antes de retirarlo o conservarlo como herramienta activa.

### Ramas de desarrollo y promoción

- La rama exacta `testing` existe en SHA `612b0431a33909af3eeaaaa909db648c165a4ac9`; su último commit identificado es del 2026-08-27 y se titula `feat: dashboard 24x7 y observabilidad v16.3.5`. Compara como 102 commits ahead y 2 behind de `main` (`82750cc0b97df69f9936bdd5d65360cd8f9020f0`). No representa el baseline operativo RC6.
- La búsqueda de refs no encontró una rama `develop`, aunque CI y el workflow de promoción todavía la nombran.
- `promote-to-production.yml` solo se activa manualmente, elige `testing` por defecto y permite `testing/develop`. Su job de integración tiene permiso `contents: write` y ejecuta `git push origin main` directamente, además de crear un tag temporal. Esto contradice la política ahora presente en `AGENTS.md`, que reserva integración al coordinador mediante PR. Ningún workflow se ejecutó.
- `deploy.yml` también es manual, toma `main` como default y acepta un tag o rama arbitrarios como versión de despliegue; no fija el SHA RC6 certificado.
- Conclusión: `testing` está configurada como entrada manual de promoción, pero no hay evidencia de que sea el código vivo o RC6. `develop` es una opción de configuración sin rama encontrada. No borrar ni modificar `testing` ni reemplazar los workflows hasta definir una ruta compatible con el proceso operativo y revisar el efecto del gate actual.

### Permisos e integración

La integración de GitHub sí tiene escritura/fusión en `main`, demostrado por los PRs #57 y #56 fusionados. El 403 corresponde a la consulta administrativa de branch protection; la lectura de rulesets tampoco está disponible por el plan/permisos. La Deploy Key del servidor es otra credencial y su carácter read-only no implica falta de permisos de escritura de la integración GitHub.

### Próximo paso de auditoría

Mantener el trabajo en lectura estática: clasificar referencias históricas y entradas de despliegue/promoción sin entrar en el workstream `pipeline ppi watch`. La siguiente modificación debe ser un PR aislado que primero cierre el bypass de promoción directa y permita promover únicamente una referencia RC6 aprobada, después de revisar con el responsable el workflow RC6 de promoción de overlay. No borrar ramas históricas ni ejecutar workflows hasta cerrar esas dependencias.

### Rama histórica de CI revisada — 2026-09-12

`chore/disable-codespaces-ci` apunta a `c564c45d9da41837f0b26aa4f9766c75505eb940` (2026-08-21, `ci: remove Codespaces dependency from CI`), sin PR asociado. Su `.github/workflows/ci.yml` tiene el mismo blob SHA que el archivo actual de `main` (`dc8864cc254fa7a6910d66aae92950a52d6e3b36`): funcionalmente, el cambio de CI ya está integrado y la rama no aporta una versión distinta del workflow. La comparación de historial muestra divergencia porque la rama conserva su línea de commits aparte. Está reportada como no protegida; no se eliminó. El conector actual no expone borrado de ramas, por lo que queda como candidata a retiro cuando el canal de administración de refs esté disponible y se cierre el checkpoint.

## Prioridad actual: promoción y limpieza — 2026-09-12

El usuario prioriza en paralelo un paso a producción prolijo y la remoción de basura/inconsistencias. Secuencia acordada para minimizar riesgo:

1. Definir un flujo de promoción trazable: validar el SHA exacto aprobado, exigir CI del mismo SHA, pasar por PR revisado y bloquear el `git push origin main` directo del workflow. El despliegue debe consumir una referencia RC6 inmutable aprobada, con confirmación humana y registro de evidencia.
2. Auditar y retirar referencias obsoletas en configuración y documentación de `main`, de forma aislada por PR y con verificaciones antes/después.
3. Solo después de cerrar dependencias de flujos, clasificar ramas históricas para retiro. No tocar `testing` mientras siga ofrecida como origen de promoción; no usar ni consultar el workstream `pipeline ppi watch`.
4. Revisar en un alcance operacional separado el workflow one-off incluido en el source RC6, dado que conserva el target `e47eeff...`. No ejecutarlo ni borrarlo desde el baseline certificado.

El paso 1 es una prioridad de diseño/auditoría, no una autorización para ejecutar un despliegue o cambiar una rama ajena. La primera implementación debe presentarse como PR aislado desde una rama propia, sin modificar `main` directamente ni fusionar/desplegar sin la autorización aplicable.

## Barrido documental de main — 2026-09-12

Lectura estática de `README.md`, `AGENTS.md`, `.github/pull_request_template.md`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`, `.github/workflows/promote-to-production.yml` y `.gitignore` en `main` (`82750cc0b97df69f9936bdd5d65360cd8f9020f0`). No se modificó `main`.

Hallazgo documental concreto: el README conserva `develop → testing → main → DigitalOcean` y describe `testing` como validación del paquete para SANDBOX. Esto no concuerda con `AGENTS.md` (integración por PR y coordinador único) ni con la semántica real del workflow de promoción. Es el primer candidato de depuración documental; su sustitución debe reflejar el proceso aprobado y el SHA certificado RC6, no presentar `testing` como baseline operativo.

Los workflows mantienen referencias a `develop` y `testing`; la promoción permite push directo a `main`, y el despliegue admite cualquier ref como entrada. Son controles operativos que primero deben reemplazarse con una ruta trazable, no borrarse como si fueran basura inerte. El barrido no encontró referencias restantes de Codespaces o v16.x en los archivos leídos. `AGENTS.md`, la plantilla de PR y `.gitignore` no mostraron candidatos adicionales en este alcance.

Próximo lote: preparar una propuesta acotada para corregir el README y abrir un PR en rama propia tras confirmar que las rutas documentales no compiten con trabajo activo. La auditoría continúa sin consultar el workstream `pipeline ppi watch`.

## PR #58 — corrección documental de flujo — 2026-09-12

Se abrió como borrador el PR [#58](https://github.com/mbalbo2023/Porota-trading/pull/58), rama `docs/align-release-flow-rc6-20260912`, base `main` @ `82750cc0b97df69f9936bdd5d65360cd8f9020f0`, HEAD `8e2ecd9b82687b7fc905d6dfd0dfb0cfdb3094c3`. El diff contiene solo `README.md`: elimina el flujo textual obsoleto `develop → testing → main`, fija el SHA RC6 certificado e instruye integración por PR. Está abierto como draft, mergeable, sin fusión.

CI run `34712067098` (#255) terminó `success`: `Infrastructure preflight` pasó. `Application CI` detectó que el paquete de aplicación no está instalado; dependencias, compilación, pruebas y stack quedaron `skipped`. Este resultado valida documentación/infraestructura del repositorio, no la operatoria ni runtime RC6. No se ejecutó ningún workflow manual ni despliegue.

## PR #59 — propuesta de retiro de workflows heredados — 2026-09-12

Se abrió como borrador el PR [#59](https://github.com/mbalbo2023/Porota-trading/pull/59), rama `chore/retire-legacy-production-workflows-20260912`, base `main` @ `82750cc0b97df69f9936bdd5d65360cd8f9020f0`, HEAD `750e2e88044ea52625356fc7bcbc96236f8b52a4`. El diff confirmado contiene exactamente dos eliminaciones: `.github/workflows/deploy.yml` y `.github/workflows/promote-to-production.yml`. El PR está abierto, draft y mergeable; no fue fusionado.

La revisión estática encontró que `deploy.yml` acepta un ref arbitrario, apunta a `/home/tradingbot/app` y exige `requirements.txt`/`entrypoint.py`, que CI no detecta en `main`. `promote-to-production.yml` permite `testing/develop`, tiene permiso `contents: write` y hace push directo a `main`. Ambos usan solo `workflow_dispatch`. No constituyen el procedimiento certificado de RC6. La propuesta retira los botones heredados; no añade reemplazo de despliegue RC6.

CI run `34714819048` (#256) terminó `success`: `Infrastructure preflight` pasó. `Application CI` detectó el paquete ausente; compilación, pruebas y stack quedaron `skipped`. El conector GitHub rechazó las consultas de historial de ejecuciones de esos dos workflows con `400 INVALID_ARGUMENT`; no se pudo verificar si tuvieron usos anteriores. Por ello, el PR sigue en borrador hasta revisión humana de dependencias/uso histórico. No se ejecutó ningún workflow manual, despliegue ni acción sobre el runtime.


## Avance de limpieza del repositorio — 2026-09-12 (continuación)

### Paso 1 — baseline y flujo documental: COMPLETADO

El PR [#58](https://github.com/mbalbo2023/Porota-trading/pull/58) se marcó listo y se integró por squash, con SHA de merge `8115a795161499670443ad5fa5b6faab93d1bf1d`. Su diff es solo `README.md`: declara RC6 `17.0.0-rc6` y el source certificado `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`, reemplaza la ruta textual obsoleta de promoción por PR y documenta que el README no autoriza despliegues. CI #255 fue reportado como exitoso para preflight; las pruebas de aplicación quedaron omitidas porque el paquete no está instalado en `main`. No cambió el runtime ni se ejecutó un workflow de producción.

### Paso 2 — inventario de referencias y ramas: EN CURSO

La enumeración global y el análisis de cualquier referencia asociada al workstream paralelo de ingesta PPI quedan expresamente fuera del alcance de este checkpoint. Por tanto, este documento no declara un conteo total actual de ramas ni certifica la eliminación de ramas históricas. Continúan como hallazgos previos los seis alias idénticos de Codespaces candidatos a retiro, sujetos a revisión de reglas/propietario. No se eliminó ninguna rama.

### Paso 3 — decisión sobre workflows manuales heredados: BLOQUEADO PARA FUSIÓN

El PR [#59](https://github.com/mbalbo2023/Porota-trading/pull/59) continúa abierto como borrador, sin cambios en su estado. Su propuesta elimina `.github/workflows/deploy.yml` y `.github/workflows/promote-to-production.yml`, pero no agrega un reemplazo de despliegue RC6 y el historial de ejecuciones previas no se pudo leer mediante la integración GitHub (error `400 INVALID_ARGUMENT`). No se fusiona hasta resolver ese riesgo de dependencia operacional; CI #256 validó preflight y omitió pruebas/stack de aplicación.

### Decisiones de limpieza

No se fusionará ninguna rama histórica de manera automática ni se reescribirá el historial. Solo se integran cambios únicos requeridos por RC6 mediante PR; referencias completamente redundantes se podrán borrar cuando reglas, propietario, PRs y dependencias estén comprobados. Se conservan los commits históricos como trazabilidad. Ningún cambio descrito aquí consulta, modifica o ejecuta la ingesta paralela.


## Verificación adicional de ramas de housekeeping — 2026-09-12

Se revisaron únicamente las referencias con nombre Codespaces/housekeeping, sin enumerar otras ramas ni entrar en el workstream de ingesta.

- Las siete refs `codespace-setup`, `codespace-setup-v2` a `-v6` e `infra/v16.1-codespaces-ready` comparan contra `main` con 0 commits exclusivos, 18 detrás y 0 archivos distintos. Son refs redundantes; decisión: eliminar los punteros, no fusionarlos. La integración GitHub conectada no ofrece operación de borrado de ramas.
- `chore/disable-codespaces-ci` compara 1 commit exclusivo y 4 detrás, pero su `.github/workflows/ci.yml` tiene el mismo blob SHA que `main` (`dc8864cc254fa7a6910d66aae92950a52d6e3b36`). Su commit solo quita la validación de archivos Codespaces que ya no existen en `main`; no aporta una variante de CI. Candidata a retiro de ref después de verificar protección/PRs.
- `codespace-config` compara 11 commits exclusivos y 18 detrás. Sus siete rutas únicas forman un conjunto histórico de entorno/configuración de desarrollo y smoke test; no se fusiona a `main` ni se trata como código operativo RC6. Se conserva temporalmente como referencia hasta cerrar dependencias y decisión de retiro.

Resultado de clasificación para las referencias revisadas: siete refs redundantes listas técnicamente para borrar, una ref CI redundante candidata a borrar, una rama de configuración divergente en cuarentena. **No se borró ninguna** por falta de operación de borrado en la conexión y por no poder verificar rulesets/propietarios desde esta integración. No se examinó ninguna ref del workstream de ingesta.


## Límite de inventario histórico — 2026-09-12

En la auditoría de solo lectura, una comparación de referencias históricas/audit previamente documentadas devolvió una divergencia amplia y listas de rutas fuera del alcance de housekeeping. Se detuvo la inspección de esos diffs inmediatamente; no se abrieron archivos, no se ejecutaron workflows y no se modificó ninguna rama o recurso operativo. Esas referencias quedan en conservación preventiva y no son candidatas de borrado en este lote. El inventario del paso 2 sigue limitado a referencias independientes de ese análisis. Para ampliarlo hace falta un método que excluya de manera verificable el workstream de ingesta; hasta entonces, no se presume que los datos obtenidos cubran todo el grafo.


## Referencias de PRs ya integrados — 2026-09-12

Se confirmó en GitHub que los PRs #56, #57 y #58 están fusionados y cerrados. Sus ramas de origen todavía existen: `chore/retire-codespaces-config-20260912`, `docs/parallel-chat-policy-20260912` y `docs/align-release-flow-rc6-20260912`. No hay que volver a fusionarlas; son refs de tareas completadas y candidatas a retiro. El conector no permitió leer el estado de protección de esas refs y no ofrece operación de borrado, así que no se eliminaron.

La comprobación fue read-only. El intento de consultar protección por endpoint genérico fue rechazado por la integración; no se aplicó ningún cambio al repositorio operativo.
