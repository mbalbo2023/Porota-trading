# POROTA TRADING RC6 — CHECKPOINT HOUSEKEEPING + F01 — 2026-09-12

## Runtime canónico vigente

- Versión: `17.0.0-rc6`.
- Host SHA final certificado: `c8773c8346a880ac5ecfe99ece5555c5bb771524`.
- Modo: `PRODUCTION_PAPER`.
- Ejecución: `SIMULATED`.
- `REAL_ORDER_CAPABILITY=BLOCKED`.
- `REAL_ORDERS_SENT=0`.
- Política de cambios: NO ROLLBACK; forward-fix solamente.

## EOD / Overnight UI — DESPLEGADO GREEN

- Run: `34671405159`.
- SHA desplegado inicialmente: `d719deeb379de285b07fa61a40f5d48d19d5bffd`.
- Validación offline: GREEN.
- Activación/postflight: GREEN.
- `TRADING_ESTRATEGIAS_EOD_UI=GREEN`.
- EOD / Overnight queda dentro de `Trading → Estrategias`.
- Scalping no vuelve a aparecer dentro de Estrategias y mantiene su menú propio.
- `EOD_POLICY_ACTIVE=CURRENT_EOD`.
- `EOD_AUTO_PROMOTION=false`.
- `REAL_ORDERS_SENT=0` y rutas reales no llamadas.
- El posterior despliegue F01 preservó esta UI y política (`EOD_UI_PRESERVED=GREEN`).

## F01 — DESPLEGADO GREEN

Antecedente original:

- Rama original: `deploy/rc6-f01-paper-20260912`.
- Candidato original: `55826f9b0a0ee7998f5498cc44d887f76ffc730f`.
- La lógica/CI original era GREEN, pero la activación fue bloqueada por un guard que exigía `git status --porcelain` completamente vacío mientras el host tenía 58 archivos no trackeados.
- No fue un fallo funcional de F01.

Rebase y despliegue final:

- Base productiva exacta usada: `d719deeb379de285b07fa61a40f5d48d19d5bffd`.
- Rama: `deploy/rc6-f01-paper-after-eod-20260912`.
- Commit funcional rebased: `a42fdd4fbce05d9390e55cc8e73358e39c242961`.
- Commit final autorizado/desplegado: `c8773c8346a880ac5ecfe99ece5555c5bb771524`.
- Run final: `34673102199`.
- Validación exacta: SUCCESS.
- Build candidate: SUCCESS.
- Tests F01 + backtest + regresión EOD: SUCCESS.
- Activación/postflight PAPER: SUCCESS.
- `F01_SOURCE_IDENTITY=GREEN`.
- `F01_RUNTIME_MODEL=GREEN`.
- `EOD_UI_PRESERVED=GREEN`.
- `POST_SAFETY=ok|PRODUCTION_PAPER|0`.
- `HEALTH_OK=YES`.
- `REAL_ORDERS_SENT=0 REAL_ORDER_ROUTES=NOT_CALLED`.
- `RC6_F01_DEPLOY_POSTFLIGHT=GREEN`.

## Housekeeping — PENDIENTE

Estado validado durante ambos despliegues:

- `TRACKED_DIRTY_COUNT=0`: no hay código versionado modificado fuera de Git.
- `UNTRACKED_COUNT=58`: existen 58 artefactos no trackeados, principalmente scripts/resultados históricos de diagnósticos y despliegues (`v17_*.py`, `*_result.json`, `.pid`, `.sh`, probes).
- Los 58 artefactos fueron preservados exactamente antes/después de EOD UI y F01.
- No hubo colisiones entre esos artefactos y los paths de los targets desplegados.

Pendientes obligatorios:

1. Inventariar y clasificar los 58 artefactos en evidencia útil / activo / obsoleto.
2. Preservar la evidencia útil fuera del checkout operativo antes de eliminar nada.
3. Retirar sólo residuos confirmados como obsoletos; prohibido `git clean` indiscriminado.
4. Evitar que diagnósticos/deploys escriban nuevos `.json`, `.pid`, logs o scripts temporales en la raíz del repo.
5. Endurecer `.gitignore` sólo para artefactos inequívocos, sin patrones amplios que oculten código legítimo.
6. Auditar y reducir ramas GitHub cerradas/integradas preservando referencias canónicas, release, checkpoints y ramas activas.
7. Establecer lifecycle de ramas: creación, convergencia, certificación y retiro.
8. Converger a un workflow estable de deploy por SHA/dispatch.
9. Mantener deploy observable por etapas: preflight, build, activation, health y postflight.
10. Mantener siempre guardas `PRODUCTION_PAPER`, `SIMULATED`, `REAL_ORDER_CAPABILITY=BLOCKED`, `real_orders_sent=0`.

## Estado final de este checkpoint

- EOD / Overnight UI: `GREEN / DESPLEGADO`.
- F01: `GREEN / DESPLEGADO`.
- Runtime canónico: `c8773c8346a880ac5ecfe99ece5555c5bb771524`.
- Housekeeping: `PENDIENTE`.
- Limpieza destructiva: `NO AUTORIZADA` sin clasificación previa.
- Los 58 untracked permanecen preservados para auditoría posterior.


---

## Addendum — último deploy RC6 confirmado y continuidad de ingesta — 2026-09-12

Este addendum actualiza el campo histórico “runtime canónico” de este checkpoint, que correspondía al cierre de F01. El contenido original de F01 y housekeeping queda preservado arriba y en el historial Git.

### Último deploy de aplicación confirmado por GitHub Actions

- Run final: `34675061113` — RC6 SWING Options Telemetry after EOD-only deploy.
- Estado: validación y job de deploy/postflight `SUCCESS`.
- Rama del deploy: `deploy/rc6-swing-options-telemetry-after-eodonly-20260912`.
- SHA exacto desplegado y verificado por el workflow: `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`.
- Finalizado: `2026-09-12 05:18:01 UTC` (`02:18` Argentina).
- Identidad validada: `17.0.0-rc6`, `PRODUCTION_PAPER`, `SIMULATED`, `REAL_ORDER_CAPABILITY=BLOCKED`, `REAL_ORDERS_SENT=0`.
- Este commit está 13 commits adelante y 0 detrás del F01 `c8773c8346a880ac5ecfe99ece5555c5bb771524`; representa avance lineal RC6.
- Deploy RC6 posterior a F01, previo al SWING: run `34674764193`, ruta EOD-only de Estrategias, éxito.

### Ingesta PPI activa — aislada de limpieza y deploy

- Estado informado por el usuario: el trabajo PPI sigue activo exclusivamente para ingesta de datos; no está realizando despliegues.
- Rama de trabajo conocida: `ops/rc6-ppi-fullfamily-history-20260912`.
- Esta rama y sus ejecuciones de ingesta quedan fuera de cualquier poda, cambio de base, reset o despliegue dentro de este paso.
- No se canceló ni modificó ninguna ejecución PPI. No se desplegó código al host durante este paso.

### Alcance de este paso

- Se registra `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d` como último baseline de aplicación confirmado por Actions.
- F01 permanece como antecedente RC6; no se lo trata como el último deploy.
- No se eliminaron ramas, tags, archivos ni artefactos del servidor.
- Housekeeping destructivo sigue pendiente de clasificación y aprobación específica.


---

## Paso 2 — clasificación read-only de ramas — 2026-09-12

Referencia de comparación: último deploy RC6 confirmado `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`.

### Inventario y PRs

- Ramas GitHub observadas: 345.
- La API de ramas marcó `protected=false` en todas las entradas consultadas. Esto no confirma la ausencia de rulesets; la configuración de rulesets/entornos no fue accesible desde la conexión de auditoría.
- 13 grupos comparten SHA de cabecera idéntico; 39 nombres de rama involucrados (26 aliases adicionales dentro de esos grupos).
- No hay PR abierto cuya rama de origen pertenezca a esos 13 grupos.
- Único PR abierto del repositorio: #13, draft RC5 hacia `main`; no pertenece a esos grupos.
- PR #5, RC3-HF6, está cerrado sin merge y corresponde a una de las ramas RC3 duplicadas.

### Comparación con el último deploy RC6

- Ocho grupos duplicados están enteramente contenidos en la historia de `a47f3339…` (0 commits por delante). Sus aliases son candidatos a poda de refs una vez revisados workflows y dependencias de nombre.
- Cinco grupos divergen del baseline y tienen trabajo propio por delante: checkpoints del 09-Sep (+2), IOL auth (+7), cauciones cost authority (+2), contract evidence (+37) y waves 12–19 (+90). Debe permanecer al menos una referencia por cada SHA hasta integrar o archivar explícitamente ese trabajo.
- Los aliases de checkpoints se conservan por continuidad, aunque compartan SHA.
- Candidatos tentativos para una primera tanda, sin borrar todavía: alias RC3-HF6/HF7 (mismo SHA, PR #5 cerrado, su commit es ancestro del deploy actual) y aliases de Codespaces (siete nombres, mismo SHA, también ancestro del deploy actual). Antes de retirar nombres se debe completar el chequeo de workflows/rulesets y elegir qué referencia conservar.

### Límite operativo PPI

- El chat activo es ahora `Revisar estado del pipeline ppi`; el anterior `continuar pipeline ppi` quedó colgado.
- Según el usuario, el trabajo activo en PPI es ingesta de datos, sin deploy.
- Se excluye `ops/rc6-ppi-fullfamily-history-20260912` de la poda y de cualquier cambio de base durante la limpieza.
- En este paso no se borraron ramas ni se cambió código, datos o estado del host.


---

## Verificación de dependencias de alias RC3 / Codespaces — 2026-09-12

Alcance read-only; comparación contra el último deploy confirmado `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`.

- Alias RC3 HF6/HF7: ambos apuntan a `9ec1fdb7f6571cc9c237276944b240c3469bd024`, ancestro del baseline RC6; no tienen trabajo único pendiente frente al deploy. No son cabeza de PR abierto. Para HF6 aparecen dos ejecuciones históricas de Actions del 02-Sep; para HF7 no aparecen ejecuciones.
- El commit RC3 contiene `.github/workflows/deploy.yml`. Su blob es idéntico en el SHA desplegado `a47f3339…`: `workflow_dispatch` manual, confirmación `DESPLEGAR`, environment `production`, acción SSH y referencia de despliegue ingresable. Por eso, eliminar los refs RC3 no retiraría ese workflow del árbol RC6. Se debe auditar el flujo y su historial como tarea separada antes de modificarlo.
- Aliases Codespaces: los siete nombres previamente identificados comparten `f39cadaac8bff62f7176e10b80ffda80f6ee6f9b`, ancestro del baseline RC6. En ese commit el API devuelve raíz vacía y no existe `.github/workflows`; no hay ejecuciones de Actions para esos nombres. Ninguno encabeza un PR abierto.
- Los refs repetidos parecen redundantes por contenido y actividad observada, pero la conexión no pudo leer las reglas/rulesets del repositorio. No se considera probada la ausencia de protección.
- No se borró ni actualizó ninguna rama. No se tocó el pipeline PPI, sus datos, ejecuciones ni el host. La limpieza de refs sigue pendiente de la auditoría restante y autorización destructiva específica.
- Siguiente control de auditoría: inventariar workflows existentes en el árbol RC6, clasificar triggers y capacidad de despliegue/escritura, y cruzarlos con ejecuciones recientes; separar archivos realmente operativos de flujos manuales obsoletos antes de proponer cambios.


---

## Estado observado del run PPI paralelo — 2026-09-12

- Run `34705180201`, rama `ops/rc6-ppi-fullfamily-history-20260912`, SHA `023730072194d97c52aa84ff0137f20219e66611`; evento `push`, workflow `.github/workflows/rc6-ppi-history-storage-audit-20260912.yml`.
- Al revisar estaba `in_progress`, con un único job `audit` ejecutando “Serialized read-only capacity and coverage audit”. No es un deploy ni fue iniciado desde la rama de housekeeping.
- El workflow verifica el SHA esperado del host, estado de servicios, disco y cobertura de bases por SSH. Toma el lock compartido `/run/lock/porota-ppi-fullfamily-history.lock` (espera hasta 30 minutos; job timeout 40 minutos), por lo que puede serializar temporalmente otros procesos de historial/ingesta.
- Este registro no modifica, cancela ni interrumpe la ejecución. La rama y los datos PPI siguen excluidos de la limpieza.


---

## Inventario de workflows del árbol RC6 — 2026-09-12

Referencia: SHA desplegado `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`. Sólo lectura.

- `.github/workflows` contiene 167 entradas: 166 YAML y el helper ejecutable `rc6_apply_patch.py`.
- Conteo estático de YAML: 163 declaran `push` (todos con filtro de rama; 151 también filtran paths), 56 declaran `workflow_dispatch` y 2 `pull_request`. Los eventos se solapan. No se encontraron triggers `push` sin filtro de rama.
- La rama default `main` sólo contiene `ci.yml`, `deploy.yml` y `promote-to-production.yml`. CI filtra `main/develop/testing`, no las ramas operativas RC6/PPI. `promote-to-production.yml` tiene `contents: write` y puede crear tags y hacer push/merge de `testing` a `main`; requiere dispatch, texto de confirmación y environment `production`.
- El mismo path `deploy.yml` difiere entre `main` y el árbol RC6: el de `main` despliega una referencia manual a `/home/tradingbot/app`; el del árbol RC6 usa `/opt/porota-trading`. Hay que auditar ambos entrypoints antes de unificarlos.
- Hallazgo RC5: `deploy-rc5-paper-once.yml` escucha push sólo en `release/v17.0.0-rc5` y despliega si el mensaje contiene `[DEPLOY_RC5_PAPER]`. Run `33992858125` terminó SUCCESS el 05-Sep. La rama tiene 0 commits por delante y 591 detrás del deploy RC6; no se halló PR asociado.
- Hallazgo de escritura automática: 46 YAML declaran `permissions.contents: write`; 45 contienen `git push`. Ejemplo concreto: `rc6-apply-active-tests.yml` llama al helper `rc6_apply_patch.py`, modifica sólo tests, crea commit y hace push a `candidate/v17.0.0-rc6-weekend-20260905`. Ese workflow no tiene ejecuciones observadas; la rama sí tuvo otros runs hasta el 10-Sep, pero está 131 commits detrás y 0 por delante del deploy RC6, sin PR asociado.
- Los workflows `rc6-final-transactional-deploy`, `...deploy2` y `...deploy3` contienen comprobaciones/rehearsals con RC5. No retirar esas referencias a ciegas: falta determinar si son evidencia histórica, rollback local o ruta operativa.
- Conclusión: triggers acotados reducen activaciones accidentales entre ramas distintas, pero el número de flujos con permiso de escritura/deploy permite que varias acciones sobre una misma rama se encadenen y cambien el checkout sin PR central. Son candidatos prioritarios para consolidar tras revisar cada rama, run y efecto.
- Sin cambios a workflows, ramas de aplicación, host ni datos; sin poda. La configuración de rulesets sigue sin ser visible desde esta conexión.


---

## Actualización de actividad PPI observada después del inventario — 2026-09-12

- El run `34705180201` de storage/capacity audit seguía `in_progress` al último chequeo; conserva su lock compartido de historial.
- Nuevo run PPI `34705796508`, creado por `push` a SHA `68835673e0cdf1f70b4760831b64bf9d28e30800`, terminó `SUCCESS`. Workflow `rc6-noncanonical-cleanup-diagnostic-20260912.yml`, job `diagnostic`.
- El diagnóstico consulta por SSH; valida el SHA de host `a47f3339…`, lee el observer DB y abre el history DB en `mode=ro`. Crea/inserta sólo una tabla temporal `extra54` en SQLite para medir cruces; no elimina ni repara filas persistentes y reporta `HOST_MUTATION=NONE`.
- Ambos runs son auditorías de historial/storage, no deploys. Son actividad del branch PPI independiente y no fueron disparados, cambiados ni cancelados por este trabajo de housekeeping.
