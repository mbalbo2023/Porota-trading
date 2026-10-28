# POROTA TRADING RC6 — Deploy V2, continuidad y aprendizaje permanente (A149)

**Corte documental:** 28/10/2026, hora de Buenos Aires (ART). **Naturaleza:** procedimiento de gobierno y handoff; NO acredita estado vivo, no es un cambio de runtime ni un permiso para saltar gates.

## 1. Fuentes de verdad y arranque obligatorio

1. Leer íntegro el [último handoff canónico de #732](https://github.com/mbalbo2023/Porota-trading/issues/732) y la última rectificación posterior, no sólo el primer comentario citado. Para el corte documental A149, el [A148](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6459266846) y su [addendum GREEN](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6459290988) son el punto de partida histórico.
2. Releer [#624](https://github.com/mbalbo2023/Porota-trading/issues/624), [#485](https://github.com/mbalbo2023/Porota-trading/issues/485), [#478](https://github.com/mbalbo2023/Porota-trading/issues/478), [#753](https://github.com/mbalbo2023/Porota-trading/issues/753), owners [#28](https://github.com/mbalbo2023/Porota-trading/issues/28)/[#679](https://github.com/mbalbo2023/Porota-trading/issues/679), PR abiertos y pipelines reales.
3. Obtener HEAD exacto de `deploy/rc6-pr69-isolated-20260915`, HEAD/tree del candidato, SHA/manifest/digest del artefacto y registros más recientes de runtime. La rama `main`, los archivos estáticos, los chats y este documento NO son prueba operacional.
4. Orden de evidencia: GitHub/SHA (código) → Actions/run/logs (pipeline) → artefacto/manifest/hash (paquete) → droplet/imagen/marker (despliegue) → DB/logs/APIs (comportamiento). Registrar fecha fuente UTC ISO-8601 y conversión ART.
5. Usar sólo estados `PROPUESTO`, `DESARROLLADO`, `COMMITTEADO`, `EN_GITHUB`, `ARTEFACTO_VALIDADO`, `DESPLEGADO`, `VALIDADO_RUNTIME`, `NO_VERIFICADO`, `BLOQUEADO`. Un run SUCCESS no implica `VALIDADO_RUNTIME`.

## 2. Invariantes innegociables

- Exclusivamente `PRODUCTION_PAPER` / `SIMULATION`, órdenes reales bloqueadas, `real_orders_sent=0` y rutas reales `NOT_CALLED` con evidencia nueva de runtime.
- FIX-FORWARD, nunca rollback; errores repetibles deben culminar en `ERROR → RCA → FIX → GUARD → TEST/CI/PREFLIGHT → EVIDENCIA → DOCUMENTACIÓN` o una excepción técnica explícita.
- GitHub y GitHub Actions son el plano normal; no requerir comandos manuales al operador. No Codespaces ni SSH como vía ordinaria.
- No alterar lógica ni cadencias de SHADOW, PAPER, scalping, sus fuentes o sus estados sin autorización de scope, baseline y pruebas de paridad. El trabajo de scheduler nunca debe acelerar/ralentizar inadvertidamente el motor.
- PPI es identidad primaria, IOL y otras fuentes son complementarias, sin sobrescribir identidad ambigua; readiness por **instrumento** fail-closed. Que existan diez familias en el código no acredita diez familias operables.
- Sin estrés de droplet: E09 cancelado y E10 no aplicable; tampoco soak encubierto ni benchmark de recursos. Permitir sólo sondeos READ_ONLY acotados. No mutar PPI Watch.
- No eliminar/modificar DB, libros PAPER, cuentas, fees históricos, backups o imágenes activas para fabricar un semáforo verde. No mentir sobre tarifas de broker ausentes.

## 3. Control de concurrencia y paralelización obligatoria

| Carril | Owner | Paralelismo permitido | Mutaciones prohibidas sin DEPLOY_OWNER |
|---|---|---|---|
| Integración productiva/PR candidato | `WRITE_OWNER` único por rutas, rama aislada | Integrar cambios compatibles en un único candidato | Rama productiva directa, host, systemd, DB |
| Deploy V2 | **`DEPLOY_OWNER` global único** | Un solo pipeline/promoción | Interferencias de otros chats; mutex `rc6-unified-paper-deploy`, `cancel-in-progress=false` |
| Arquitectura PAPER/SHADOW/scalping y 10 familias | `READ_ONLY` | Auditorías del issue #753, trazabilidad Discovery→Tradability→Readiness, gates y estrategia | Modificar motores, estrategias, admisión o contratos |
| Timers, workers e IOL OAuth | `READ_ONLY` | Inventario por prerueda/rueda/postrueda/noche/fin de semana; métricas y duplicación | Rearmar timers, cambiar polling o forzar autenticación sin owner |
| DB/SQLite/cauciones/almacenamiento | `READ_ONLY` | Integridad acotada, costos verificables, crecimiento, espacio y recibos de cierre | Reescrituras del ledger, VACUUM agresivo, limpieza Docker |
| QA visual/accesibilidad y PR governance | `READ_ONLY` o `WRITE_OWNER` documental separado | Evidencia por vista/modal, control de PR históricos | Tocar runtime, productiva o PR ajenos |

Todos los writers declaran scope, rama, SHA base, exclusividad y release pareado; `READ_ONLY` no bloquea. Nunca múltiples deploys por subagente: una integración coherente y un Deploy V2 final. Toda auditoría que requiera datos de rueda debe informar fase/hora reales; no inventar ejecuciones OPEN fuera de rueda.

## 4. Puertas reales de Predeploy / Deploy V2

1. Verificar owners y mutex vigentes; revisar diferencias contra HEAD real; candidato exacto no Draft con diff auditado. No mezclar cambios de gobierno no operativos en la rama de deploy.
2. Predeploy `.github/workflows/porota-predeploy-v2.yml`: suite completa gobernada sin fallas/skips no justificados; compile/import/dependencias, contratos, seguridad PAPER, paridad SHADOW, fallos reproducibles; **build once** y mismo paquete OCI/tar/manifest/digest que luego se usará. Si SHA/tree cambia, congelar y predeploy nuevamente; no reutilizar artefacto viejo.
3. Comprobar que el artefacto no haya vencido. La expiración de un artefacto de GitHub Actions **no** implica caducidad del código ni del sistema instalado; exige nuevo artefacto certificado si ya no está disponible. El artefacto #752 `12787124776`, del Predeploy [#40620692434](https://github.com/mbalbo2023/Porota-trading/actions/runs/40620692434), fue validado 5.111/5.111 a 27/10 y tenía vencimiento **03/11/2026 23:46:23 ART**: revalidar en la API antes de usarlo.
4. Gate previo a host: fecha/ventana de A125 (27-Oct ART no se traslada automáticamente al 28-Oct), quiescence/flat PAPER, guardian OAuth IOL, disco real suficiente y `CURRENT_STATE_V2`; verificaciones exactas sobre el runtime. No ejecutar una promoción que se sepa que fallará tras instalar por gate poststart (p. ej. scanner DEGRADED por ledger inválido).
5. Las dos autorizaciones literales y únicas en [#624](https://github.com/mbalbo2023/Porota-trading/issues/624) se emiten **sólo cuando el candidato exacto es promovible**: `RC6_624_APPROVE_DEPLOY_AND_COLD2 candidate=<SHA> tree=<TREE>` y `RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED candidate=<SHA> tree=<TREE>`. Nunca reutilizar comentarios de SHA/tree anteriores ni reemplazarlos por la autorización genérica del chat.
6. Integrar vía PR protegido a productiva; ejecutar `.github/workflows/porota-deploy-v2-promote.yml` una vez, sin rebuild en droplet, con artifact/manifest/digest idénticos. Preflight bloqueante, cleanup Docker sólo seguro/medido, sin estrés.
7. Validar desde host/DB/logs/API: imagen SHA/tree/manifest, observer, PAPER supervisor, SHADOW/scalping separados, dashboard, SQLite/fees, fuentes, IOL guardian, timers, RAM/disco/CPU acotados; confirmar modo PAPER y órdenes reales cero. Sólo entonces `VALIDADO_RUNTIME`; de lo contrario mantener `DESPLEGADO` o `BLOQUEADO` con RCA/guard/evidencia. No degradar safety gate para lograr verde.

## 5. Lecciones recientes que se deben preservar

- [A146](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6458948320): un `SQLITE_FULL` **inyectado en un test** es distinto de una base real llena. Un reporte de mediodía no satisface el cierre postrueda; retries deben ser acotados y no deben falsificar resumen.
- Cauciones: seis obligaciones PAPER maduras con autoridad de comisiones no validada requieren **NO_GO contable**, sin reescribir el ledger ni inventar tarifas. El scanner puede degradarse por ese dato; eso NO prueba fallo del algoritmo de scalping.
- PAPER/SHADOW/scalping son componentes distintos: `bv_paper_runtime.py` supervisa hijos `scanner`, `dynamic_shadow` (base30s) e `intraday_scalping` (base180s), con admit/risk/DB relacionados. No confundir failure de un hijo con todos los demás.
- Captura pública BYMA/CNV/A3 a 60s e IOL a 60s en el candidato histórico #752; SHADOW consume snapshot público fresco (límite120s). Se descartó subir captura pública a 900s. No cambiar estas cadencias sin medición, dependencias y equivalencia; el guardian IOL OAuth 30min es una función diferente de scraping/captura.
- A138 dejó 16 timers en HOLD tras un Deploy FAIL; A125 tenía vigencia **27-Oct ART**. Los cronogramas se restauran por autorización exacta y evidencia de cada servicio, no por asumir que mañana equivale a ayer.
- Reportes/cierre de cauciones sólo cuando cumplen propósito económico/postrueda y son idempotentes; no generar trabajo repetido nocturno. Clasificar cada timer por fase, necesidad, frecuencia, costo RAM/CPU/DB, dependencia y owner; suprimir duplicados sólo tras demostrar equivalencia y sin afectar SHADOW.
- Diagnosticar disco, memoria y SQLite con sondeos limitados; no confundir tests, snapshots ni logs históricos con runtime actual. Registrar crecimiento/duplicados y limpieza recuperable; preservar datos y artifactualidad.
- La evidencia visual 52 rutas/26 imágenes de un SHA pasado no acredita la navegación/modales de la versión nueva. Verificar denominadores por vista/fila y accesibilidad de voz, sin afirmar 100% por capturas parciales.
- `OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES` sólo define alcance; no implica READY, contrato/fill PAPER, datos frescos, PnL confiable ni GO de scalping. Analizar las diez familias individualmente.

## 6. "100% electrónico" = evidencia completa por alcance, nunca una afirmación vacía

El objetivo del operador es **100% de pruebas, contratos, controles y recibos verificables electrónicamente** en GitHub Actions/artefactos y en probes READ_ONLY del runtime, sin comandos manuales. Antes de cualquier GO, publicar una matriz con **denominador, evidencias, fuente y estado** de: diffs, suite de tests, manifiesto/OCI, fecha de artefacto, approvals exactos, disco, runtime service/DB, PAPER/0, workers/timers por fase, diez familias por instrumento, observación OPEN real cuando aplique, QA visual y ownership.

- Si un criterio aplicable no puede probarse electrónicamente, queda `NO_VERIFICADO` o `BLOQUEADO`; **no** se eleva el conjunto a 100%, GREEN o `VALIDADO_RUNTIME` por inferencia.
- No exigir eventos imposibles fuera de rueda como si ya ocurrieron. Documentar pendientes fechados y conservar fail-closed mientras falte evidencia de mercado OPEN.
- El éxito de build/predeploy prueba el artefacto, no rentabilidad, actividad simulada ni seguridad del host tras deploy.
- Adjuntar run IDs, job IDs, SHA/tree, artifact ID/digest, timestamps UTC/ART, resultados y guardias permanentes; evitar exponer secretos, PII, cuentas o datos de broker en el repo público.

## 7. Aprendizaje y mantenimiento de reglas

Para cada incidente significativo registrar: error observado (run/host), RCA causal contrastado, cambio fix-forward, guard automático o razón de imposibilidad, test adversarial en CI, evidencia final, documentos afectados, estado de cierre y dueño del pendiente. Si una decisión nueva corrige una instrucción antigua, **corregir además el punto de entrada** que consume el siguiente agente: comentario correctivo aislado no alcanza.

**Lección CI A149 (PR documental a `main`):** el workflow histórico `Checkpoint attestation` exige campos literales en el cuerpo del PR (`Checkpoint ID`, `Continuity checkpoint path`, `Checkpoint commit SHA`, `RC6 baseline SHA`, `Safety invariants`). Conservar etiquetas y valores comprobables; un SHA histórico debe identificarse explícitamente como histórico. Su PASS es compatibilidad documental, **nunca prueba de runtime PAPER/0**. Si el cuerpo de PR causa RED, corregir los campos y activar un evento `synchronize` de contenido pertinente en la rama aislada; no desactivar el guard para conseguir GREEN.

Antes de Predeploy y de cada nuevo chat, reconciliar `AGENTS.md`, política `ops/policy/porota-policy.yaml`, este runbook documental, #732 y el workflow vivo. Los registros históricos deben conservarse con fecha y rótulo *histórico*, no usarse para GO. Actualizar documentación en rama/PR de gobierno fuera de productiva; **un commit sólo documental nunca debe disparar recreación de contenedores**. Los tests/guards de runtime requieren su propio WRITE_OWNER, candidato integrado y pruebas.

## 8. Handoff mínimo exigible

El agente entrante lee A148 + último addendum y cualquier sucesor de #732, #624, #753 y este documento. Vuelve a consultar GitHub/Actions/artefacto/host, adquiere ownership exclusivo adecuado, delega READ_ONLY sin interferir y ejecuta FIX-FORWARD. Entrega: qué se desarrolló e integró, qué no se tocó, SHA/tree, tests y evidencia, errores/RCA/guard, estado `DESPLEGADO` versus `VALIDADO_RUNTIME`, bloqueo real, cronograma, pendientes por familia y worker, links #732/#624 y **liberación pareada** de ownership. Nunca prometer GO sin pruebas.

**Corte A148 histórico para continuidad:** PR #752 HEAD `7e7e306b44c5e0a0cd89988e461dcba42944c969`, tree `aeb1efe0215ea0a97ef5b4f5bd5be46db4a7b8be`, Predeploy #40620692434 GREEN/artefacto validado, aún NO desplegado; runtime #751 instalado con validación pendiente, scanner/ledger de cauciones bloqueante, A125 vencido por fecha y timers en HOLD. **Verificar todo nuevamente** al continuar; este documento no congela el estado del 28/10.
