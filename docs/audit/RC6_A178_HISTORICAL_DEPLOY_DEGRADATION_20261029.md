# RC6 A178 — forensia de por qué los Deploy dejaron de ser previsibles

**Corte:** 29-Oct-2026 ART. **Operación:** `NO_GO_PENDING_EXPLICIT_REARM`; PAPER/SHADOW solamente, `real_orders_sent=0`; PPI Watch intocable. **Alcance:** se estudian cambios de entrega y sus fallos, no se deduce un GO a partir de un Action exitoso. **Control de cambios:** candidato A178 aislado PR #763; ningún cambio de este informe autoriza por sí solo promoción.

## Método y límites de la evidencia

1. Se consultó la colección **real** `GET /repos/mbalbo2023/Porota-trading/actions/runs?per_page=100&page={1..5}&branch=deploy%2Frc6-pr69-isolated-20260915` el 29-Oct-2026 y se filtró por los workflow IDs canónicos: viejo RC6 `358890088` y Deploy V2 `367852515`. La [tabla de 458 ejecuciones](data/rc6_deploy_actions_20260915_20261029.csv) incluye **todas** las ejecuciones de esos dos workflows que devolvía esa colección para la rama, con fecha UTC, run ID, nombre y conclusión. Tres ejecuciones de otros workflows de la rama se excluyen deliberadamente. Los totales son reproducibles con la API y no incluyen Predeploy ni Actions de ramas de PR.
2. La evolución de YAML se verificó contra blobs GitHub de commits históricos, conservando las versiones por fecha: antiguo [`a07699be`](https://github.com/mbalbo2023/Porota-trading/blob/a07699be6d56ea2eebd82ac078fc56f7402f1f1e/.github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml); V2 [`7d9dbe3b`](https://github.com/mbalbo2023/Porota-trading/blob/7d9dbe3b78e4a01cbb6c5a0c386acbf18d5c2a75/.github/workflows/porota-deploy-v2-promote.yml), [`f525ed70`](https://github.com/mbalbo2023/Porota-trading/blob/f525ed707d540ad3b4b75eecf95c503bfd4b230c/.github/workflows/porota-deploy-v2-promote.yml), [`1a36eae7`](https://github.com/mbalbo2023/Porota-trading/blob/1a36eae75a2dcbd0420c324b5ef0bfea07a02da8/.github/workflows/porota-deploy-v2-promote.yml). Los conteos de líneas/pasos se obtuvieron de esos blobs, no de estimaciones.
3. Se cruzó el historial Git de modificaciones a workflows (commits que tocaron archivos, no cantidad de líneas modificadas) con RCA, jobs/logs y recibos en [RC6_A178_DEPLOY_TRANSITION_RCA_20261029.md](RC6_A178_DEPLOY_TRANSITION_RCA_20261029.md).
4. **Limitación crucial:** `conclusion=success` de GitHub **no** equivale a `VALIDATED_RUNTIME`. Para clasificar éxito operativo se necesita marker, runtime, logs/DB y recepción de evidencia. Los artifacts de septiembre que expiraron no permiten reconstruir retrospectivamente tamaños de imágenes, y no se afirma una tendencia cuantitativa de bytes de Docker. Este análisis NO inspecciona retrospectivamente cada línea de los 317 logs RED, sino todos sus estados y los incidentes seleccionados con RCA; los detalles de causas individualizadas restantes son `NO_VERIFICADO`.

## Serie histórica reproducible — dos generaciones de Deploy

| Generación | Fechas UTC | Runs | Actions success | failure | Otros |
| --- | --- | ---: | ---: | ---: | ---: |
| RC6 transaccional anterior `358890088` | 15–26 Sep | **292** | **83** | **196** | 10 cancelados, 2 omitidos, 1 `startup_failure` |
| Deploy V2 `367852515` | 26 Sep–29 Oct | **166** | **45** | **121** | 0 |
| Ambas | 15 Sep–29 Oct | **458** | **128** | **317** | 13 |

La afirmación de que *siempre* se desplegaba sin problemas no está respaldada: la automatización ya tenía numerosos RED el 15–17 Sep. **Sí hubo una ventana significativamente mejor 18–21 Sep**, seguida de una ruptura cuantificable el 22 Sep.

| Ventana | Runs | Success | Failure | Otros | Lectura |
| --- | ---: | ---: | ---: | ---: | --- |
| 15–17 Sep, flujo anterior | 91 | 11 | 71 | 9 | Baja estabilidad desde los inicios de esa generación. |
| **18–21 Sep, flujo anterior** | **36** | **28 (77,8%)** | **8** | 0 | La mejor ventana reciente observada; probablemente explica la percepción de despliegues rápidos. |
| **22–25 Sep, flujo anterior** | **161** | **42 (26,1%)** | **116** | 3 | **Primera inflexión fuerte**, cuatro días ANTES de Deploy V2. |
| 26 Sep, cierre flujo anterior | 4 | 2 | 1 | 1 | Sustitución por V2 en curso. |
| 26 Sep–16 Oct, V2 | 81 | 31 | 50 | 0 | V2 no elimina el problema heredado. |
| **17–20 Oct, V2** | **39** | **0** | **39** | 0 | Congestión sostenida: 41 fallos consecutivos desde [#38981124467](https://github.com/mbalbo2023/Porota-trading/actions/runs/38981124467) hasta [#39514335011](https://github.com/mbalbo2023/Porota-trading/actions/runs/39514335011), incluyendo finales del 16 Oct. |
| 21–26 Oct, V2 | 33 | 14 | 19 | 0 | Éxitos intermitentes del Action; no extrapolar a runtime. |
| **27–29 Oct, V2** | **13** | **0** | **13** | 0 | Último run [#40796450488](https://github.com/mbalbo2023/Porota-trading/actions/runs/40796450488) termina A175 con marker `DEPLOYED_VALIDATION_PENDING`. |

**Punto de inflexión observable:** el 23-Sep se enviaron **81** ejecuciones del Deploy transaccional, **55** fallidas y 25 Action success, con una cancelación/otro. El mismo día el archivo se modificó **46 veces** en el historial; el 22-Sep, 45 veces. Estas cifras indican cambios concurrentes de contrato y una dinámica de reintento/fix-forward sin estabilización integral, pero no prueban que cada commit explique cada error: esa causalidad requiere su log individual.

## Cuánto creció el plano de control

| Corte (commit) | Workflow | Líneas | Pasos `- name:` | Evidencia de cambio |
| --- | --- | ---: | ---: | --- |
| 20 Sep `a07699be` | RC6 anterior | 562 | 9 | `sudo docker build` directamente en el Droplet; no existía paquete único V2 reutilizable. |
| 27 Sep `7d9dbe3b` | Deploy V2 | 467 | 9 | Artefacto Predeploy y `docker load` en host, digest/manifest y un segundo dominio de falla: transferencia/espacio. |
| 30 Sep `94b31b3a` | Deploy V2 | 762 | 9 | Nuevos contratos de runtime y validación. |
| 05 Oct `da697c6e` | Deploy V2 | 1.078 | 10 | Crecimiento de guardias sin matriz completa de origen instalado. |
| 15 Oct `9c9c2a7a` | Deploy V2 | 1.225 | 11 | Más admission/soak/reconciliación. |
| 20 Oct `f525ed70` | Deploy V2 | 1.637 | 23 | Estado previo, interrupción y suspensión de timers recibieron nuevas ramas. |
| 24 Oct `26a95f73` | Deploy V2 | 1.847 | 25 | Mutex/barriers, clasificaciones y limpieza por ownership. |
| **28 Oct `1a36eae7`** | **Deploy V2** | **2.639** | **33** | A143→A175, fuentes staged/installed, runtime pendiente y receipts fechados. |

Predeploy V2 creció de **269 líneas y 19 pasos** (27-Sep, `7d9dbe3b`) a **619 líneas y 29 pasos** (28-Oct, `1a36eae7`). En todo el historial del candidato hubo **206 commits que tocaron el workflow anterior**, **126 que tocaron Deploy V2** y **32 que tocaron Predeploy V2**. Un mismo commit puede tocar varios, por lo que **no** se suman como commits únicos.

## Cambios de proceso que introdujeron las nuevas superficies de fallo

### 1. 22–25 Sep: el alcance cambió durante los intentos de instalación

- Git registra múltiples adiciones de escalping/scalping, BYMA, IOL, dashboard, catálogos, sector-map y verificadores durante el mismo ciclo. Ejemplos de commits: [`f0043806`](https://github.com/mbalbo2023/Porota-trading/commit/f0043806), [`ddfcbbff`](https://github.com/mbalbo2023/Porota-trading/commit/ddfcbbff), [`a25edfd3`](https://github.com/mbalbo2023/Porota-trading/commit/a25edfd3), [`fc43f501`](https://github.com/mbalbo2023/Porota-trading/commit/fc43f501).
- 45 modificaciones al YAML de Deploy el 22-Sep y 46 el 23-Sep, 81 runs el día 23-Sep. **Inferencia:** la acumulación de correcciones pequeñas sobre un workflow compartido, sin congelar un candidato integrado contra un origen host exacto, produjo mayor probabilidad de regresiones cruzadas.
- Aquel flujo construía `docker build` en el host, lo que permitía reparar la imagen y reconstruirla en cada intento, pero no podía asegurar que el artifact probado fuera el mismo desplegado. No debe restaurarse ese comportamiento: el problema no se arregla renunciando a la inmutabilidad.

### 2. 26 Sep: Deploy V2 cerró la brecha de reproducibilidad y abrió otra de transición

- Cambio comprobado: build en Predeploy, empaquetado del candidato, manifest/digest, transferencia y `docker load` sin rebuild. Beneficio: identidad del binario verificable; costo: más disco para ZIP/TAR/capas y más pasos intermedios.
- El código inicial V2 incluía rutas de `docker image prune -af`/`builder prune`, luego reemplazadas por reclamación de imagen con ownership y exclusiones; esas primitivas iniciales constituían una **superficie de riesgo**, sin concluir que borraron algo material sin log.
- **Falla arquitectónica inferida del patrón de errores:** Predeploy certificaba código en ambiente limpio, pero NO reproducía todas las precondiciones de un host que llevaba días de fallos parciales (`CURRENT_STATE_V2`, `.Image` vs tag, receipts, fuentes instaladas antiguas, timers, PATH/argumentos, UID/directorios).

### 3. 16–24 Oct: rescates COLD/OPEN, recepción y state machine de múltiples épocas

- Surgen `rc6_deploy_quiescence.py` (16-Oct), `rc6_deploy_scheduler_suspend.py` (20-Oct) y cambios del workflow cada día. Agrega A92, A125, A138, A143, A145, A169... además de barreras persistentes, COLD2, guardian, preopen y recetas de cierre de posiciones PAPER.
- Los guards fallan correctamente ante desvíos, pero se agregaron **de manera reactiva** tras cada error de host. Los casos de fechas/ventana/recibo exacto y estado previo instalados NO se probaron conjuntamente antes de los siguientes Deploys. Resultado medido: **0/39 success entre el 17 y el 20 Oct**.
- Fallos de disco/RAM/SQLite fueron problemas reales del host y de carga; no deben mezclarse con defectos de tests o de artifact. La [bitácora canónica #732](https://github.com/mbalbo2023/Porota-trading/issues/732) y la serie de Actions documentan ejemplos de RAM bajo reserva mínima, espacio insuficiente y scanner degradado. No se realiza estrés E09/E10 cancelado.

### 4. 27–29 Oct: la cadena de intentos pasó a ser una dependencia adicional

- El marker de A175 es `DEPLOYED_VALIDATION_PENDING`, **no** `VALIDATED_RUNTIME`. La próxima instalación debe **adoptar exactamente** el origen A175 con su artifact/IDs/recibos sin aceptar un estado cercano o supuesto.
- Ocho Deploys RED en 28-Oct exhibieron, uno después de otro, límites `execve`/SSH, helper viejo sin símbolo nuevo, gzip, receipt COLD2, timer, fuente instalada vs staged, scanner bootstrap y confusión Docker tag/ID. Véase RCA [tabla con 8 runs](RC6_A178_DEPLOY_TRANSITION_RCA_20261029.md). No se infiere que las ocho fallas fueran del motor de trading.
- Predeploy A178 [#40877608438](https://github.com/mbalbo2023/Porota-trading/actions/runs/40877608438) descubrió **5.497 tests**, ejecutó todos y falló **18**, antes de host: 15 escenarios históricos de classifier que no remapeaban el marker real, 2 aserciones de presupuesto/forma del wire SSH anteriores al envío por stdin, 1 aserción de preopen anterior al veto NO_GO del 29-Oct. Localmente se actualizaron fixtures **sin bajar guards de producción**, con **189 PASS** en las cuatro suites afectadas; hace falta otra ejecución completa.
- El [Predeploy siguiente #40880953697](https://github.com/mbalbo2023/Porota-trading/actions/runs/40880953697) ejecutó **5.499 pruebas: una sola falla** por lock SQLite en una prueba multiproceso de PPI budget que ya contemplaba `BudgetBackpressure` en `acquire` y `finish`, pero no en `start`. Era una **omisión del contrato adversarial de la fixture**: el productor negó el inicio seguro, sin enviar un pedido. Se agregó el caso de `start` con falla segura, contador de reserva pendiente y verificación de deuda real, sin modificar `rc6_ppi_global_budget.py` ni PPI Watch. La suite completa y nuevo SHA siguen pendientes.
- La limpieza [#40876344156](https://github.com/mbalbo2023/Porota-trading/actions/runs/40876344156) recuperó **2.903.625.728 B** de dos imágenes sin refs, con tres contenedores protegidos. Libre `/` **4.339.638.272 → 7.243.264.000 B**, todavía bajo el piso deseado de 8 GB. El siguiente paso exige cálculo REAL de bytes requeridos del artifact; ningún `GREEN` de código puede reemplazarlo.

## RCA de sistema, no sólo síntomas

**Hechos validados:** alta tasa histórica de Action RED; inflexión 22-Sep; >5× crecimiento de YAML V2; nuevas cadenas de recibos/guards; fallos al cruzar la transición host; Predeploy A178 RED sobre tests de contratos migrados; A175 no validado; disco reducido hasta cleanup.

**Causa raíz de ingeniería (inferencia fundada en esos hechos):** el pipeline se convirtió en una **máquina de estados de recuperación versionada implícitamente dentro de un YAML creciente**, a la vez que cambiaban código, instrumentación, scheduler y recursos. Cada fix-forward endureció la frontera local, pero la **matriz completa de transiciones entre versión instalada × versión candidata × día de mercado × tipo de estado previo** seguía incompleta. No existía una fuente única de contrato de transición consumida tanto por Predeploy como por Deploy.

**Causa raíz de gestión (inferencia):** se promovieron muchos cambios de entrega de alta frecuencia sin exigir un candidato integral estable y un checklist de prueba de host desde el estado anterior **antes de iniciar mutaciones**. Esto facilitó que el error siguiente apareciera tras pasar la barrera anterior; por sí sola la cantidad de commits no demuestra mala calidad individual.

## Defensas permanentes y decisión de promoción A178

1. **Congelar identidad:** un PR/candidate SHA, tree, manifest, digest y artifact de Predeploy GREEN; si cambia cualquier archivo, invalidar el certificado anterior. No reusar artifacts de Predeploy RED.
2. **Estado previo como entrada formal:** testear antes de host la transición A175 `DEPLOYED_VALIDATION_PENDING` + A176 NO_GO exacto + source instalado + imágenes/tags/IDs, recibos, libros flat PAPER/0, mutex, horarios y timers. El preflight host será de **sólo lectura**, atado al ZIP congelado, con espacio/inodos medidos.
3. **Compatibilidad instalada/staged:** versiones de helper explícitas, no suponer que el helper viejo soporta símbolos del nuevo; fuentes grandes por stdin, presupuesto SSH medido y tests de gzip/tag/ID. Un error repetible no se cierra sin guard + test.
4. **Tests de todos los guardias en el PR:** la suite de 5.497 debe ser 100% GREEN antes de merge/Deploy. Las fixtures antiguas se actualizan para simular las precondiciones nuevas, preservando fallos adversariales; no se ocultan tests con `skip`/`xfail`.
5. **Host y control de capacidad:** no modificar servicios ni limpiar más imágenes sin nueva identidad/refcount/espacio; no apagar guardias ni reducir límites de RAM/disco para obtener un verde aparente.
6. **Un solo Deploy:** con `rc6-unified-paper-deploy`, branch protegida, aprobaciones literales #624 para candidate+tree nuevo, promoción merge de dos padres, Deploy V2 canónico y evidencia del **mismo artifact**. Validar marker `VALIDATED_RUNTIME`, Docker, systemd, DB, API, scanner, dashboard, `real_orders_sent=0`, PPI Watch no tocado.
7. **Separación de GO:** `VALIDATED_RUNTIME` certifica salud técnica; **NO habilita trading PAPER**. Mantener `NO_GO_PENDING_EXPLICIT_REARM` hasta una reactivación explícita del operador; real orders permanecen bloqueadas siempre.
8. **Trabajo estructural después del ciclo urgente:** extraer el contrato de transición del enorme YAML a una biblioteca versionada de datos y validaciones, con matriz de orígenes conocidos y pruebas reproducibles prehost. Reducir el número de excepciones históricas por fecha sólo mediante migración reversible en pruebas, **no rollback del Droplet**.

**Semáforo al corte:** RCA y serie de Actions **VALIDADO**; fixtures focalizados **DESARROLLADO/test local PASS**; Predeploy del SHA nuevo **PENDIENTE**; artifact/preflight #624/merge/Deploy/runtime **NO_VERIFICADO**; GO de PAPER **NO_GO**.
