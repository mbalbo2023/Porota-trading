# Retiro de referencias a Codespaces — 2026-09-12

## Hallazgo

RC6 operativo auditado (`a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`) no contiene `.devcontainer/` ni `codespace-test.sh`; su README y `ci.yml` tampoco tienen referencias a Codespaces. El bot desplegado no depende de Codespaces.

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

## Aclaración de ramas testing / testigo y permisos — 2026-09-12

- La rama exacta `testing` existe en SHA `612b0431a33909af3eeaaaa909db648c165a4ac9`; último commit del 2026-08-27: `feat: dashboard 24x7 y observabilidad v16.3.5`. Respecto al `main` actual (`82750cc0b97df69f9936bdd5d65360cd8f9020f0`), está divergente: 102 commits ahead / 2 behind.
- La rama exacta `testigo` no existe (GitHub 404). En esta conversación “testigo” debe ser una referencia inmutable (tag + SHA) extraída del checkpoint RC6 vigente, no una rama móvil.
- El CI de `main` incluye `main`, `develop` y `testing` para push y pull request. El workflow manual `promote-to-production.yml` tiene `testing` como origen por defecto, acepta `testing` y `develop`, valida CI y puede hacer push a `main`. `deploy.yml` es manual y selecciona `main` por defecto.
- Conclusión: `testing` sigue conectado al flujo GitHub de promoción, pero eso no prueba que su HEAD sea el runtime desplegado ni que sea el baseline RC6. No borrar/modificar `testing` ni el flujo de promoción hasta diseñar y revisar una migración que preserve el proceso manual vigente; no se ejecutó ningún workflow.
- Corrección sobre permisos: la integración de GitHub sí tiene escritura/fusión en `main`, demostrado por los PRs #57 y #56 fusionados. El 403 corresponde a la consulta administrativa de branch protection; la lectura de rulesets tampoco está disponible por el plan/permisos. La Deploy Key del servidor es otra credencial y su carácter read-only no implica falta de permisos de escritura de la integración GitHub.

### Resultado de la consulta actual de GitHub

La rama `testing` existe en `612b0431a33909af3eeaaaa909db648c165a4ac9`; su último commit data del 2026-08-27 y se titula `feat: dashboard 24x7 y observabilidad v16.3.5`. Compara como 102 commits ahead y 2 behind de `main`. No existe branch `testigo` (404).

El CI de `main` incluye `testing` y `develop` para push/PR. La promoción manual elige `testing` por defecto, permite `testing/develop`, verifica CI y actualiza `main`; el despliegue manual toma `main` por defecto. Esto confirma que `testing` está cableada como entrada de desarrollo/promoción, pero no prueba que su HEAD sea RC6 o el código vivo.

GitHub enumera la rama `release-candidate/v17.0.0-rc6-deploy3-20260906` en SHA `5bdad270c2a23bb2456a320d41e18940ce70ec6f`; la enumeración de tags devuelve solo `v17.0.0-rc3-hf6`, no un tag RC6. La auditoría previa de este mismo checkpoint había anotado otro SHA para RC6. Antes de nombrar un “testigo” canónico o cambiar el selector de promoción, reconciliar el SHA/tag operativo con el checkpoint de runtime autorizado. No promover ni ejecutar workflows durante esa conciliación.
