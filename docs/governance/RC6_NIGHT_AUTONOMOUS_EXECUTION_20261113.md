# RC6 — GUÍA CANÓNICA DE EJECUCIÓN AUTÓNOMA NOCTURNA HASTA DROPLET VALIDADO

**Versión:** `A1115-R349 / 2026-11-13 ART` (publicación 2026-11-14 UTC).  
**Rol:** instrucciones ejecutables, plan de reducción de complejidad y mapa de pendientes. **No es autorización técnica de un artefacto ni evidencia de runtime.**  
**Ámbito:** `mbalbo2023/Porota-trading`; plano de gobierno `main`; producto `deploy/rc6-pr69-isolated-20260915`; candidato único #1087.  
**Owner de esta edición:** `RC6_A1115_R349_NIGHT_AUTONOMOUS_RUNBOOK_20261113` — [lease #980](https://github.com/mbalbo2023/Porota-trading/issues/980#issuecomment-6869249473), documentación solamente.  
**Fuentes vivas obligatorias:** [AGENTS.md main](../../AGENTS.md) y [AGENTS.md producto](https://github.com/mbalbo2023/Porota-trading/blob/deploy/rc6-pr69-isolated-20260915/AGENTS.md); [política producto](https://github.com/mbalbo2023/Porota-trading/blob/deploy/rc6-pr69-isolated-20260915/ops/policy/porota-policy.yaml); [#1093 continuidad](https://github.com/mbalbo2023/Porota-trading/issues/1093), [#980 ownership](https://github.com/mbalbo2023/Porota-trading/issues/980), [#732 historia](https://github.com/mbalbo2023/Porota-trading/issues/732), [#1087 candidato](https://github.com/mbalbo2023/Porota-trading/pull/1087), [#1113 R334](https://github.com/mbalbo2023/Porota-trading/pull/1113). Si #1093 señala sucesor más reciente verificado, seguirlo.

> **Regla anti-deriva:** la foto siguiente es una fotografía fechada, NO una instrucción para devolver GitHub a esos SHA. En cada nuevo turno primero releer HEAD/PR/Actions/owners/artifact/runtime actuales y construir un DELTA. El chat, los ficheros adjuntos históricos y la memoria no son fuente de verdad. En caso de contradicción, evidencia más reciente y de mayor capa manda.

## 0. Misión, autonomía y criterios de éxito

El agente nocturno debe: (1) cerrar el camino crítico P0 hacia **un único Deploy V2** de RC6 en modo PAPER; (2) dejar codebase/CI-CD más simples y medibles sin reimplementar ni romper los guards; (3) racionalizar PR, ramas, pruebas, documentación y código no usado mediante *censos y PR separados*, sin borrados ciegos; (4) registrar progreso, fracasos, RCA y próximos pasos en #1093, con leases en #980; (5) entregar evidencia verificable del droplet o un `HOLD79/NO_GO` explícito con causa y pasos exactos.

El titular **ya dio autorización general** para gestión de repositorio, análisis, desarrollo, PR, pruebas, seguimiento y uso de Actions/navegador dentro de estas reglas. No formular preguntas repetidas de permiso por tareas ordinarias. Los GOs **específicos y vinculados a identidad** que los contratos técnicos exigen para host/root/release siguen siendo gates verificables: buscar el GO vigente exacto; si falta o no coincide, `BLOQUEADO/HOLD79`, documentar e intentar resolver por la vía autorizada. No fingir que ownership de GitHub concede UID0 ni inventar credenciales o suplantar firma. Una denegación real de permisos o un cambio material de alcance es un stop, no una razón para sortear controles.

El éxito final requiere **todos** los siguientes recibos, separados:

1. Commit/SHA exacto de fuentes y branch productiva, PR reconciliados (capa `EN_GITHUB`).
2. CI/gates precisos + RELEASE inmutable creado **una vez** para ese SHA, manifest/digest y procedencia verificables (`ARTEFACTO_VALIDADO`).
3. Transferencia y promoción única por Actions hacia el droplet exacto, con custodia root/host y tres CID cuando aplique (`DESPLEGADO`).
4. Lectura física fresca de contenedores, systemd, Docker ownership, DB/WAL, Dashboard 8000, observer, logs, PPI Watch sin tocar, `PRODUCTION_PAPER/SIMULATION`, salidas fail-closed y **`real_orders_sent=0` físico** (`VALIDADO_RUNTIME`).
5. Evidencia y cierre en #1093, owners liberados en #980. Si cualquier capa falta, dejar `NO_VERIFICADO` y NO declarar despliegue final.

No operar ni emitir órdenes reales. No mutar PPI Watch, Boca, otros contenedores, puertos o volúmenes. FIX-FORWARD, no rollback; no stress, no limpieza global. SSH interactivo/manual y consola del usuario sólo **break-glass** si Actions realmente no puede hacerlo.

## 1. Fotografía reconciliada (2026-11-14 01:16–01:20 UTC; 13-Nov ART)

| Plano/objeto | Recibo verificable | Estado y efecto |
|---|---|---|
| `main` control plane | `db957b6949a6c83b9e7a4759ee68ad57586dc10b`, PR [#1114 MERGED](https://github.com/mbalbo2023/Porota-trading/pull/1114), CI [43535784095](https://github.com/mbalbo2023/Porota-trading/actions/runs/43535784095) y attestation [43535784077](https://github.com/mbalbo2023/Porota-trading/actions/runs/43535784077) GREEN | Control-plane R327/R340 integrado; **no root host instalado**. Branch protection observada con checks `Infrastructure preflight`, `Repository tests / application when present`, `checkpoint-attestation`. Revalidar al actuar. |
| Producto protegido | `deploy/rc6-pr69-isolated-20260915` HEAD `7ac6e368bd42137817e3271839c35f579bdbbbbe` | Sin promoción R349. No escribir directamente allí. |
| Candidato único runtime | [#1087](https://github.com/mbalbo2023/Porota-trading/pull/1087), branch `integration/rc6-a1115-r87-unified-paper-candidate-20261110`, HEAD `0e7577fc6112406794ffbc392c902904481f8ca0`, ~240 paths contra product | OPEN, non-draft, merge blocked; R271/R272 integrados una sola vez (10 paths) en [R348](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6868993111). No rebases/copias masivas sin matriz de procedencia. |
| Otro PR abierto | [#1113](https://github.com/mbalbo2023/Porota-trading/pull/1113), HEAD `ee283ece5846cb83021e8c5f35a4bdcb33a03240`, DRAFT | R334 normalizador ROOT env, 46/46 focal y CI histórica GREEN; `WRITE_OWNER` en #980 ACTIVE, **no editar sus dos paths**. |
| Censo PR | Sólo #1087 y #1113 abiertos **en este corte** | No decir “28/30 PR abiertos” por recuerdos históricos. Censar de nuevo. |
| Candle candidato | [43537395711](https://github.com/mbalbo2023/Porota-trading/actions/runs/43537395711) | SUCCESS, prueba focal, no RELEASE. |
| Predeploy candidato | [43537395716](https://github.com/mbalbo2023/Porota-trading/actions/runs/43537395716) | **FAIL/HOLD79 R252 esperado**: faltan D/E nuevos de identidad exacta. No es GO. |
| R163 offhost | [43537395700](https://github.com/mbalbo2023/Porota-trading/actions/runs/43537395700) | **COMPLETED/FAILURE**, job Py3.11 SUCCESS; job native/anonymous-volume FAIL en paso **R205 raw JUnit privacy hold before upload**; job REST SKIPPED. Es protección fail-closed. No reintentar sin RCA y decisión R206 A/B/C. |
| D/E anterior | B [43297125572](https://github.com/mbalbo2023/Porota-trading/actions/runs/43297125572), C 43300581231, D 43301355473, E 43304509783, vinculados a candidato viejo `4fe14abd...`/publisher `7718e1ed...` | Históricos; **NO autorizan** el SHA `0e7577...` ni el main `db957...`. Construir nuevos sólo tras freeze definitivo. |
| Root físico (recibo operador 13-Nov 18:32 UTC) | [R341a](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6861731649) | `sudo -n` root OK; R280 installer root0 0555, R301 PIN root 0400, R297 receipt 0400; R271 CLI/custody/profiles/approval/generations **ABSENT**. R272 es workflow, **NO archivo del host**. Hash/semántica/DB/runtime NO_VERIFICADO. |
| R321 instalación | `.github/workflows/rc6-r321-root-install-approved.yml` en main | Dispatch owner-only hace checks offhost; `--install-approved` devuelve `PHYSICAL_TRANSPORT_NOT_CERTIFIED`, no hay R309 transporte certificado conectado. Permisos repo no reemplazan gate físico. |
| Host extra P0 | [R348b](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6869055999) | Prebirth attestor `custody.attest_prebirth` invocado pero backend no lo implementa: `EXTERNAL_QUIESCENCE_ATTESTOR_UNVERIFIED`. Cleanup refiere `OWNED_IMAGE_REFERENCE_PROOF_UNVERIFIED`. Ambos HOLD79 hasta pruebas concretas. |
| Runtime actual | Sin recibo físico fresco en este corte | **`NO_VERIFICADO`, Deploy V2 `NO_GO`**. No decir `real_orders_sent=0` como medición actual. |

**Eventos canónicos clave:** [R339](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6859710535) (recuperación source code, mapa ROOT); [R344](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6862973308) (dos planos y orden correcto); [R347](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6868776432) (control-plane main); R348/R348b (candidato y bloqueantes nuevos). La branch productiva tiene su propia política; nunca inferir su YAML leyendo `main`.

## 2. Primeros 15 minutos: bootstrap obligatorio de un agente nuevo

1. Declarar `READ_ONLY`; anotar UTC/ART; identificar repositorio con conector GitHub. Si falla el conector, abrir GitHub **en navegador interno del usuario ya autenticado**, consultar commits, Actions, PR e issues desde la UI. Si tampoco es posible, registrar bloqueo; no inventar. No usar Codespaces ni SSH operacional.
2. Leer íntegros `main/AGENTS.md`, `product/AGENTS.md`, `product/ops/policy/porota-policy.yaml`, este documento, últimos comentarios de #1093 y leases #980. Releer R339/R344/R347/R348/R348b; #732 sólo antecedente.
3. Consultar de nuevo `main`, product, candidato #1087 y #1113: SHA de HEAD, base, diff/archivos, PR state, required checks, run IDs, artifacts, manifest/digest, release. Listar TODOS los PR abiertos actuales y comentarios posteriores. **No usar el snapshot anterior como permiso para actuar.**
4. Verificar OWNERS activos exactos en #980, incluidas liberaciones y supersesiones. Buscar `DEPLOY_OWNER` global y trabajo concurrente por rutas, también en ramas y workflows.
5. Consultar runtime sólo mediante **workflow READ_ONLY existente** de Actions (o propuesta de workflow limitado si falta), sin root arbitrary shell. Buscar último receipt físico de ruta/host, fecha, CID, SHA, root files, PAPER, `real_orders_sent`. Si no hay, `NO_VERIFICADO`.
6. Publicar en #1093 un mini-checkpoint con cuatro columnas **ayer → ahora → delta → pendiente**, fuentes y horas. No copiar íntegro el histórico.
7. Crear tabla para cada solicitud: `YA IMPLEMENTADO` / `PARCIAL` / `FALTANTE` / `SUPERSEDIDO` / `NO APLICA` / `NO_VERIFICADO`. Buscar primero implementaciones equivalentes (archivo + entrypoint + commits + tests + PR abiertos/cerrados + run). **No escribir código o tests antes de esta búsqueda.**
8. Seleccionar máximo un candidato a RELEASE y marcar un orden de integración; las tareas independientes READ_ONLY pueden progresar en paralelo, pero scopes WRITE separados y UN deploy global.

**Comandos de GitHub/Actions, no del droplet del usuario:** usar lista de workflows/run/dispatch del conector cuando funcione; fallback UI GitHub → pestaña Actions → workflow exacto → Run workflow con ref y entradas verificadas. Nunca adivinar nombres de workflows, refs, inputs o secretos; leer YAML real antes del dispatch. Guardar URL y run_id; consultar jobs/logs/artifacts. Disparar un run es una acción con efectos, por lo que verificar su clase `READ_ONLY`/safe antes de ejecutarlo. Si se necesita ejecutar un comando en el droplet, usar la ruta **Actions → workflow revisado → transporte R309 restringido → allowlist de comando exacto → salida redaccionada en artifacts**, no una shell universal.

## 3. Coordinación y permisos sin burocracia repetida

- Modo `READ_ONLY`: auditar cualquiera de los frentes en paralelo sin lock; no modifica estado.
- Modo `WRITE_OWNER`: exclusividad por lista exacta de paths; registrar en #980 `Workstream ID`, `Ownership status: ACTIVE`, `Work branch`, `Base SHA`, `Scope paths`; no cambiar paths ni tests de otro owner. Abrir branch aislada y PR; revisión independiente; release lease con `RELEASED` y commit/PR.
- Modo `DEPLOY_OWNER`: exclusión global del droplet. Antes de tomarlo, comprobar que nadie lo tiene. Mientras activo, otros NO hacen cambios en Docker, systemd, DB, contenedores, host, PPI Watch, cleanup o deploy. Sólo audit y coding no solapado.
- `main` es control-plane, `deploy/rc6-pr69-isolated-20260915` es producto. No mezclar el release branch con un PR de gobernanza. Los scripts ROOT control-plane se integran y autorizan en main; runtime/deploy único desde candidato hacia product sólo después del gate.
- `concurrency.group=rc6-unified-paper-deploy`, `cancel-in-progress: false` en despliegue. Ningún chat cancela deploy en vuelo ni corre un segundo.
- No relanzar failures “para ver si pasa”: leer primer error, exact-SHA, condición de éxito/fracaso, hacer RCA y guard focal. Protecciones `HOLD79` pueden ser salidas esperadas; un FAILED no es automáticamente bug ni éxito.

## 4. Camino crítico único hasta droplet: puertas G0–G12

| Gate | Acción operativa (en este orden lógico) | Evidencia GO requerida; si falta |
|---|---|---|
| **G0 Snapshot** | Reconciliar fuentes, SHA/product/PRs, #980, #1093, Actions, artifacts, runtime. | Checkpoint fresco; sin él `READ_ONLY`. |
| **G1 Cerrar huecos de origen** | Auditar código existente R271/R272 y backend OCI; implementar **sólo** la interfaz/attestor prebirth realmente faltante con semántica fail-closed en branch owner, y prueba focal de quiescence contra consumidor real. Auditar limpieza exacta por referencias/digest propiedad Porota; sin eliminación de terceros. | Contrato de interfaz + pruebas de regresión actuales; si falta, `HOLD79`. |
| **G2 R334** | Esperar/revisar owner activo PR #1113. Integrar el normalizador ya construido (no rehacer), con inputs de v17 y política PAPER, sin imprimir secretos. | PR reviewed, scope 2 paths, CI y attestation exact SHA. ROOT env aún no instalado. |
| **G3 R326/R337/R308** | Recuperar fuentes **remotas originales congeladas** R326 (`69f15843...`) y R337 (`f74739cc...`), R308 (`d7df74a8...` reconstruida); buscar equivalentes integrados y comparar API antes de integrar. Enlazar normalizador R334 → sealer ROOT R337 y bundle/provenance R326 → perfil R308, sin reescribir. | Tests focales, schema host, root0600/0400, PIN/fuente, manifest y rejection-path. Fuentes reconstruidas no heredan automáticamente conteos PASS antiguos. |
| **G4 Integración control-plane** | Mantener main R327/R340/R321 de #1114: usar stable GitHub identity que no depende de mutable REST metadata. Revisar si R321 debe conectarse con R309 bounded SSH; NO convertir en deploy remoto ad hoc. | Identidad exacta de owner, ref, digest, nonce, lease, pin, idempotencia, prevencion TOCTOU. |
| **G5 Código y paquetes congelados** | Integrar sólo commits demostrados de G1–G4 al plano correspondiente, con matriz de paths/semántica; no arrastrar 240 paths o ramas DRAFT sin clasificar. Congelar **SHA final** de #1087 y **SHA trusted main**. | Diff completo, cross-plane compatibility, tests relevantes, ownership, review, cero cambios posteriores al freeze. |
| **G6 Pruebas A/B/C de cadena exacta** | Reparar la **causa** R163/R205 raw JUnit privacy: examinar job [132270220296](https://github.com/mbalbo2023/Porota-trading/actions/runs/43537395700), artefactos y contrato R206; ejecutar sólo A/B/C requeridos contra fuente/checkout EXACTOS y transporte autorizado. | Runs exactos sin exposición de PII, no reutilizar A/B/C de 4fe14; R205 privacy no debe relajarse. |
| **G7 Único RELEASE D/E** | Preflight barato y focal primero; después CI final de riesgo, BuildKit `build once` (no rebuild en droplet) para SHA congelado; manifest checkout, SHA/digest de imagen y artefacto, witness D/E externo/independiente, seed. | D/E **nuevos para par candidato/main actual** y provenance completa; artifact `ARTEFACTO_VALIDADO`. No producir 8–14 imágenes de tanteo. |
| **G8 R252 Predeploy V2** | Ejecutar predeploy del release PR exacto; comprobar required checks, `RC6_R252_TRUSTED_RELEASE`, guards de origen/cleanup y que run NO fue SKIPPED por DRAFT. | `GO` real para SHA/digest exacto. Si exit79, registrar primer guard y detener promoción. |
| **G9 Transporte físico root** | Invocar **sólo** workflow owner-approved R321/R309 desde trusted main, con `DO_HOST/DO_USER/DO_SSH_KEY/DO_KNOWN_HOSTS` normalmente configurados, host pin + nonce/expiry. Validar R280 installer/PIN/receipt existentes, e instalar únicamente componentes faltantes R271, perfiles, aprobación, generaciones, env ROOT con R334/R337. | Prueba física root0 ownership/mode/hash, 0400/0600, inode, rutas, generation seal, PIN y rechazo de drift. El `SUDO_ID_ROOT=OK` anterior NO basta. |
| **G10 Promoción única** | Tomar DEPLOY_OWNER exclusivo, verificar triple PAPER y mutex, ventana operativa, servicios ajenos intactos; ejecutar Deploy V2 del único artifact inmutable. | Run/manifest/digest al host, 3 CID correctos, entorno controlado, cero rebuild, error handling fix-forward. |
| **G11 Validación física** | Por Actions READ_ONLY: probar containers Porota con labels/digests, systemd, observer/dashboard 8000, DB + WAL/SHM, sello de modo, rutas de orden real NOT_CALLED, `real_orders_sent=0`, health y logs; verificar PPI Watch **sin tocarlo**; comparar exactamente con manifest SHA. | Evidencia UTC del host y capas, `VALIDADO_RUNTIME` si TODO coincide; un health green aislado no sirve. |
| **G12 Limpieza y cierre** | Limpiar sólo imágenes, containers y build cache **inequívocamente Porota** sin referencias activas ni volúmenes/datos; medir bytes recuperados. Registrar fecha, SHAs, runs, fallos, RCA/guards, residuals, release lease. | #1093 con GO/HOLD y handoff, #980 `RELEASED`. |

**Orden esencial:** G0–G5 no admiten nuevos D/E de otro SHA; G6–G8 autorizan paquete pero **no** root; G9 es requisito de host; G10 único deploy; G11 decide VALIDADO_RUNTIME. Deben quedar `HOLD79` si la prueba correspondiente falta. Las acciones de análisis/QA/métricas/recuperación de fuentes pueden ir en paralelo si sus archivos no se solapan; la integración y el despliegue son secuenciales.

### 4.1 Detalle del instalador ROOT / R271 / R272, pregunta frecuente del titular

R271/R272 no se “instalan” simplemente por hacer merge a GitHub. El despliegue físico debe crear, atestiguar y custodiar los archivos root-owned (CLI/custody/profiles/approvals/generations) en el droplet; R272 es un workflow de GitHub y **no un fichero del host**. El recibo del operador mostró sudo root funcional y R280/R301/R297 físicos, pero cinco destinos R271 ausentes. R321 control-plane existe, aunque el transporte físico permanece sin certificar. Construir el binario/script y aprobar el código NO sustituye ejecutar R309/R321 por Actions con secretos y pruebas exactas. Sólo después se puede afirmar `HOST_GO`. Si un workflow debe instalar elementos ROOT, usar allowlist pequeña de rutas, `sudo -n` dentro de procedimiento ya auditado, logs sin secretos y `stat`+digest posterior; jamás pedir a Tincho una serie de comandos manuales como ruta normal.

### 4.2 Regresión particular R163 / R205 / R206 / R252

- R163 [43537395700](https://github.com/mbalbo2023/Porota-trading/actions/runs/43537395700) acabó `failure` en R205 raw JUnit privacy, paso nativo. No se debe subir material sensible ni eludir ese guard.
- Diagnosticar si el hold es esperado y si permite derivar R206 B/C independiente; **comprobar política y salida exacta**, no inferir por historia.
- R252 [43537395716](https://github.com/mbalbo2023/Porota-trading/actions/runs/43537395716) falló por falta de D/E correspondiente a HEAD actual. Ejecutar el productor una sola vez DESPUÉS del freeze y antes de repetir R252.
- Si el primer error cambia, abrir RCA específico; no “reintentar predeploy” indefinidamente ni reconstruir imágenes iguales.

## 5. Plan CI/CD — simplificación medible (PROPUESTO; NO IMPLEMENTADO por esta guía)

La definición de “más rápido” es **menos tiempo, imágenes, costo y reversos por cambio**, sin disminuir el hallazgo de defectos críticos. Medir antes de modificar. Referencia histórica de un incidente: tres ciclos nativos ~54m51s frente a tres builds ~5m48s; **no** se extrapola a todos los runs. El workflow de promoción en product tenía ~3.237 líneas y ~206 KB en corte R349; inventariar estructura y triggers antes de tocarlo. Predeploy product ~754 líneas; maintainer moderno debe medir dependencia real, no eliminar por longitud solamente.

### Fase C0 — telemetría sin rebuild ni cambio de seguridad (READ_ONLY)

1. Extraer ~últimos 20 PR/runs representativos y **últimos 5 del candidato exacto**; registrar inicio/fin UTC por `job`/`step`, `queue`, `wall-clock`, tiempo crítico, reintentos, fallos reproducibles vs HOLD, costo aproximado, bytes cache, número de imágenes por SHA, digest reutilizado, pipelines disparados por docs.
2. Agrupar por tipología: docs-only; UI/dashboard; Python negocio; contratos mercados/DB; deps; Docker; CI/infrastructure; root/deploy/policy. Medir p50/p90 y primer gate fallido de cada clase. Sin observabilidad, no afirmar “mejora del 80%”.
3. Generar `C0 baseline` en #1093 (puede ser tabla corta en comentario o artefacto versionado), sin reescribir logs. Registrar métricas antes/después y SHA, hardware/runner comparables.
4. Construir *dependence map*: entrypoints product Docker/service/systemd, workflows, imported Python modules, tests, archivos lock; representar grafo de alcance con evidencia, falsos positivos y riesgos.

### Fase C1 — CI rápido por impacto, sin saltarse safety

1. **Docs-only:** markdown links/schema/ownership attestation y examen de CI, sin construir ninguna imagen ni ejecutar todo pytest. Comprobar si los required checks actuales pueden devolver un resultado correcto de “no impact” con recibo; los checks obligatorios de branch protection deben conservarse.
2. **Dashboard/UI:** lint+render, pruebas API/UI/contract y smoke local, una sola imagen únicamente si cambió su contenido final; pruebas regresivas de lo impactado. Evitar repetir varias suites idénticas por job.
3. **Python funcional:** `compileall/import`, linters relevantes, unitarios del módulo + llamados/deps, contract/broker smoke inofensivo, y suite de integración cruzada en el candidato congelado. No abrir mercado ni generar órdenes reales para “validar”.
4. **Docker/systemd/root/seguridad/policy/deps:** elevar riesgo: host interface/fail-closed/identity/permissions/DB/schema, pruebas de regresión de categoría y suites totales *sólo donde el grafo o riesgo lo requieren*. Validar siempre los invariantes PAPER.
5. Guard técnico que compare diff base→head con mapa de consumidores; si no puede clasificar o ve dependencias dinámicas, **fallback conservador** y registrar razón. No confiar en selección por nombre de archivo solamente.
6. PR checks paralelos y cancelables por SHA supersedido (`cancel-in-progress:true` en pruebas PR cuando seguro); **Deploy V2 siempre `false`** y grupo exclusivo. No aplicar políticas de cancelación a releases inmutables o pruebas que generan evidencia D/E.

### Fase C2 — cache y build once

1. `actions/cache`/`setup-python` con claves por OS/Python, lock/requirements hashes y scopes; guardar también análisis de invalidaciones; no cachear datos secretos ni runtime mutable.
2. Docker BuildKit/buildx cache por digest de Dockerfile, lock, context e imagen base fijada, con namespaces entre main/product, cache remoto verificable y política de expiración. **Nunca** hacer deploy de una etiqueta flotante o una imagen recompilada sin digest atestado.
3. Preflight más barato primero; si falla, no arrancar suites de 30–90 min ni construir imágenes. Una sola compilación del artefacto RELEASE en el SHA final; D/E independiente autentica **ese mismo artefacto**, la instalación sólo `pull/load` por digest.
4. Separar “verificación de source” de “promoción física”; no repetir CIDs ni `docker build` por cada chat, PR o workflow reintentado.
5. Limitar matrix Python/OS a combinación de compatibilidad realmente soportada, pero conservar 3.11/native/root críticos cuando correspondan; retirar duplicados sólo tras evidenciar cobertura equivalente.
6. Aplicar límites al crecimiento de imágenes mediante metrics/cap, no borrado de estado activo; limpieza selectiva con dry-run y proof de propiedad.

### Fase C3 — modularizar sin introducir pipelines paralelos

1. Extraer del promote product (3.237 líneas) pasos compartidos por responsabilidad a workflows reutilizables/actions/scripts existentes cuando sea seguro: `inventory/guard`, `artifact verify`, `root transport`, `compose promotion`, `runtime receipt`, `owned cleanup`. Comparar ejecución old/new en **shadow/offhost**, no cambiar contrato de promoción durante un deploy.
2. Eliminar wrappers huérfanos sólo con consumidores demostrablemente nulos. Unificar duplicaciones **por semántica y tests**, no por similitud de nombres. Documentar interfaz estable, owners y lista de dependencias.
3. Mantener al menos un gate final de reproducción end-to-end del release inmutable; no convertir un “quick pass” de docs o UI en sustituto de un preflight physical/ROOT.
4. Integrar C1/C2/C3 por PRs pequeños y pruebas focalizadas, con cambio real de política codificado en `AGENTS.md`, policy YAML del branch correcto y guards CI. **La presente guía propone; no altera automáticamente los workflows.**

### Métricas y criterio de aceptación CI/CD

| Indicador | Medición obligatoria | Umbral de trabajo propuesto (no garantía) |
|---|---|---|
| Tiempo total PR pequeño | p50/p90 desde push hasta checks obligatorios | Reducir ≥50% vs baseline comparable, sin tasas de falsos GREEN. |
| Tiempo de rechazo | hasta el primer FAIL/HOLD determinístico | Llevar preflight barato al inicio. |
| Imágenes por cambio | conteo único por SHA/digest y tipo de cambio | Docs/test-only **0**; candidato final **1 release por SHA** + pruebas derivadas sin rebuild. |
| Reutilización de cache | hits/misses, bytes y tiempo ahorrado | Demostrar delta; no suponer. |
| Tests ejecutados y omitidos | suite, selección, motivos y riesgos | 0 skips injustificados de guards críticos. |
| RCA repetidos | hash/firma del error, número de repeticiones | Cada error recurrente con guard y regresión. |
| Deploy/recovery | horas de BUILD→runtime, número de CUTOVER, % NO_GO | Una promoción coherente; recovery verificado sin rollback ni pérdida. |

## 6. Censo de código y archivos no usados — reducción segura, NO eliminación nocturna masiva

1. Inventariar `.py`, scripts, Bash, YAML, Dockerfiles, Markdown, workflows, entrypoints, import graphs, service units, labels, image build contexts, API routes y tablas DB **del SHA producto/candidato actual**; clasificar `runtime usado`, `CI usado`, `herramienta admin`, `test`, `histórico/evidencia`, `candidato único`, `no alcanzable` y `NO_VERIFICADO`. Tener en cuenta imports dinámicos, CLI de Actions, plantillas y cron, que un `rg` simple no detecta.
2. Medir “archivos de Python reales operativos” por ruta de ejecución, no presumir que ~900 archivos son runtime; dar números con método y SHA. Distinguir usuario de imports Python de funciones no llamadas.
3. Para cada aparente huérfano: buscar referencias en workflows/branches, tags/release, Docker/context, docs, logs, cron, runtime import, migraciones, artefactos únicos y PR históricos. Registrar evidencia de **0 consumidores** y owner que avala. **No borrar por edad/nombre/ausencia de import estático**.
4. Primero proponer `DEPRECATED`/quarantine con tests/manifest y plan de reemplazo; después pequeño PR de remoción con comparación de imports, packaging, Docker layer, runbook y footprint. Migraciones, tests de regresión y evidencia única requieren política de retención; no descartar.
5. Revisar `requirements`/lock: resolver versiones y hashes soportados, compararlos con imports verdaderos, vulnerabilidades y uso efectivo. No agregar `yfinance` por inercia: se reportó deprecado/no deseado; exigir prueba de consumidor permitido y aprobación funcional antes de reintroducir.
6. Evaluar duplicidades de módulos R2xx/R3xx y wrappers `sudo python` con semantic diff y test contract. Identificar entrypoint único y fuente de constantes/policies. **No reescribir fuente original recuperada**; primero preservar remote SHA.
7. Limpieza Docker selectiva (fase posterior a runtime): construir grafo digest→image→container→volume→service→branch/release; si no puede probar ownership exclusivo o referencias = 0, `NO_CLEANUP`. Nunca `docker system prune -a`, `docker volume prune`, `down -v`, borrado de DB/WAL, global daemon reset o PPI Watch.

## 7. Higiene GitHub, PR, branches, Actions y documentación

1. Censo de PR live (corte eran dos: #1087/#1113), branches remotas, checkout SHAs, protection, reviews, check suites, artifacts, releases, issues, docs y duplicaciones. Reportar *vigente* vs *supersedido*. No reabrir ni cerrar “30 PR” históricos sin evidencias.
2. En cada PR abierto identificar: owner activo/released, base/head, zero diff/overlap, bloqueo `mergeable_state`, CI REQUIRED, evidencia única, side effects, dependencia de otros workstreams. No usar `mergeable_state=blocked` como diagnóstico sin leer gates.
3. No cerrar/mergear antiguos por número o antigüedad, no borrar heads de PR abiertos, ramas REVIEW o con código no recuperado, ni evidencia única. Distinguir “merged” vs “closed unmerged” vs “stale snapshot”.
4. Archivar inventario de ramas **sólo** cuando hash de commits y evidencia estén preservados en GitHub, y no existan consumidores/owners/PR/refs; proponer limpieza en PR/issue con lista exacta y reversibilidad. No hacer purga nocturna automática.
5. Comprobar reglas `AGENTS.md` main R335, PR template y `checkpoint-attestation`: existencia de documentos no prueba enforcement. En corte R349, `main` anuncia 3 checks obligatorios con enforcement `everyone`; verificar el check realmente corre contra SHA PR. No editar guards globales para obtener falso PASS.
6. Mantener **un único runbook** (este archivo), enlazado de AGENTS; actualizar con DELTAS y referencias. No generar diez versiones contradictorias ni poner un estado de GitHub sólo en chat. La guía histórica A149 y #732 permanecen referencias, no se copian completas.
7. Auditoría de Actions schedulers: detectar `P1_SCHEDULE_SILENT` con runs reales/timestamps; un workflow `active` no prueba cron. Si falta evento, usar dispatch manual del workflow existente autorizado; no generar supervisores adicionales ni tests que dependan de mercado abierto.
8. Reconciliar jobs fallidos e imágenes costeadas antes de limpiar workflows. Tener política de retención de artifacts/digests y SHA que soportan un release; no borrar porque CI ya está GREEN.
9. Dividir cambios de política en: `governance/docs-only`, `quick-CI`, `cache-build`, `host-guard`, `cleanup`, `runtime-business` con owners no solapados. Integración product sólo cuando corresponda al mismo candidato.

## 8. Backlog funcional y datos RC6 (no confundir con el P0 físico)

**Estado operativo actual de cada línea: `NO_VERIFICADO` hasta consultar receipts contemporáneos.** Históricamente se trabajó en:

| Línea | Trabajo pendiente / verificación actual antes de codificar |
|---|---|
| **PPI primario, permisos y cuota** | 0/20 instrumentos útiles y alerta AUTH/MARKETDATA ROJO en cortes anteriores, tiempo de última fuente, API signatures, sidecar, presupuesto compartido/lease, cuota efectiva, reservas PPI Watch/salidas, 0 GET sin lease, fallos `fail-closed`; fuentes externas degradadas generan alarma. Verificar si A630/A670 se integraron; **no tocar PPI Watch**. |
| **Identidad/procedencia** | 5 claves identidad PPI, PIT causal, alias y tickers, no sobrescribir con IOL/BYMA; `SearchInstrument/Search/BondsEstimate/Configuration`; READY por instrumento y riesgo de LKG/stale. No elegir identidad ambigua. |
| **Embudo mercado** | ~9.500 instrumentos vistos históricamente; `DISCOVERY→WARM→HOT`, COLD/COLD2 y preservación de posiciones, `Book1→decision COMMIT→Book2`; decisión “operable” no implica spread, volumen o edge. |
| **Familias e instrumentos** | Acciones, CEDEAR, bonos/letras/ON, ETF, opciones, futuros, cauciones, FCI local/exterior, acciones USA, índices; contratos económicos `FUND_TERMS`, `TARIFF`, lotes/cuantías, fixed-income exits, FCI importador. Último censo histórico ~821 gaps de ~8.821 contratos en seis familias (dato fechado, reconciliar; no repetir automáticamente el 819 inicial). |
| **IOL y complementarias** | Validaciones históricas AL30, YMCJO, D30N6, GFGC6000OC, 22 FCI, cauciones ARS 1/2/3; BYMA/A3/ROFEX complementarias con procedencia. No suplantan identidad PPI. |
| **BYMA COM19031 T+1 / scheduler** | Crons preopen 10:16, cauciones 10:30, BYMA 18:07, postrueda; monitoreo por receipts reales, no depender de rueda abierta; ejecución manual focal via Actions si scheduler silencio. |
| **Cuentas y DB** | PIT de posiciones/salidas, 5-key vs ticker-only, WAL/SHM, causalidad, idempotencia, retención #478 (365 días), solo dry-run de purga, no borrar DB activa. |
| **Dashboard/observador** | Probar Dashboard 8000, observer, PPI state, TIMER reports, `real_orders_sent`, control PAPER y accesibilidad. Cualquier nueva UI debe tener prueba focal y deploy consolidado. |
| **Política/portafolio** | `OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES` no significa todos READY. Modo PAPER/SHADOW; no operaciones reales; cierre administrativo PAPER cuando mercado cerrado. |

Estas líneas NO deben convertirse en gates inventados para G9/G10: separar seguridad técnica obligatoria de readiness por instrumento o mercados externos degradados; mantener fail-closed para trading y alarmas. Avanzar en ramas independientes si hay owners y mientras no bloquee integración P0. Abrir el plan funcional **después** de la verdad física o como trabajo paralelo no solapado; no lanzar un deploy por cada ítem.

## 9. RCA / lecciones aprendidas convertibles en controles permanentes

| Incidente/error real o histórico | Causa o riesgo demostrado | Defensa que debe existir / evidencia exigible |
|---|---|---|
| **Repetición de código/tests y pérdida R305/R308** | Worktrees/commits locales originales no publicados, chats tomados como memoria; reconstrucciones con recuentos PASS no transferibles. | Preservar branch remota + commit/tree antes de cerrar chat; inventario git y búsqueda histórica antes de escribir; comparar hash/semántica de fuente. R339 como referencia. |
| **R340 false HOLD79/TOCTOU** | Comparación de metadata REST mutable como identidad exacta de trusted-main. | R347 fija identidad estable GitHub; #1114 `main=db957...`; prueba negativa positiva y CI exacto. No tocar sin reproducir RCA. |
| **R252 Release sin D/E exactos** | Se intenta predeploy tras cambiar SHA de candidato o publisher; se reutiliza artefacto anterior. | Gate exact `(candidate_sha, trusted_main_sha, manifest_digest, release_digest)`; freeze → D/E una vez → predeploy; NO_GO si deriva. |
| **R163/R205 raw JUnit privacy** | Material de tests nativos con trazas sensibles no puede subirse sin saneamiento. | R206 pipeline B/C según contrato, privacy hold antes del upload; nunca desactivar para acelerar. Run 43537395700 fallo en native. |
| **R271 ROOT ausente aunque CI source GREEN** | Código/permiso GitHub se confundió con instalación host; R321 transport no conectado. | Pipeline físico Actions R309/R321 con pin/nonce/allowlist; `stat`/hash/mode/owner e idempotencia. Hasta entonces HOST_GO=NO. |
| **Prebirth attestor invocado sin backend** | Método `custody.attest_prebirth` no existe en backend candidate. | Test de integración consumidor↔implementación y negative-path externo (sin asumir éxito por mocks). R348b. |
| **Cleanup owned image referencia sin prueba** | No hay demostración de que imágenes ajenas o activas queden indemnes. | Grafo digest/referencia/labels, dry-run, lista exacta y prueba de exclusión; sin prueba no borrar. R348b. |
| **3 suites nativas para un pase** | Repetición costosa por SHA/candidate drift y pasos no filtrados; incidente histórico ~54m51s vs ~5m48s builds. | C0 baseline, selección de impacto, preflight rápido, job dedup, caché y build once; medir antes/después. |
| **Docker socket/UID/GID/contendedores** | `porotaadmin` con daemon permiso denegado, sudo daemon operativo; labels/health incompletos y ownership PPI Watch delicado. | Comandos root acotados por Actions, 3 CID exactos, owner/label/device proof, no tratar permiso Unix como “falta de aprobación” de GitHub. |
| **PPI MARKETDATA/identidad y cuota** | 0/20 en corte viejo, firma/API/cuota, presupuesto/sidecar no certificados. | LKG marcado, alarma, lease global, no tocar PPI Watch, PIT/5-key, fail-closed per-instrument. |
| **Aliases ON/OBL y FCI/FICI** | Consumidores con identidad/inferencia discordante en historial. | Schema único, test cross-caller, no mapear aliases arbitrarios; buscar PR #901/#912 y versión actual antes de reabrir. |
| **JSON NaN/Infinity, observed_at o ticker-only** | Contratos de datos y temporalidad incorrectos en rutas históricas. | Validación JSON estricta, PIT/hora, 5-key, tests del consumidor; comprobar #914/#921 y código actual. |
| **Cron schedule `active` sin ejecución** | Confundir configuración con eventos reales. | Contar runs y receipts por ventana; P1 `SCHEDULE_SILENT`, dispatch manual limitado si corresponde; no doble worker. |
| **Exit 137, disk guard y limpieza** | OOM/disco al construir/reconstruir y residuos sin ownership. | Cache selectiva, límite de builds, headroom guard, reclamación sólo Porota por digest con medición. |
| **Documentos y políticas con estado obsoleto** | Fechas antiguas, 28/30 PR históricos, release GREEN tomado por deploy. | #1093 vivo, AGENTS pointer, censo SHA en cada inicio y etiquetas de evidencia. |

Cada nuevo error seguirá **ERROR → RCA concreta → FIX mínimo → GUARD contra repetición → TEST focal/CI/PREFLIGHT → EVIDENCIA**. Un workaround aislado no cierra un error repetible. Hacer el guard en el lugar correcto (código/CI/runbook) y no sólo en prosa, con tests que detecten el error original.

## 10. Ejecución nocturna por bloques, con checkpoints

**Bloque N0 — 0–30 minutos:** G0, fotografía y ownership, verificar #1087/#1113; filtrar trabajos en progreso; inventario de run 43537395700 y de pruebas; checkpoint #1093 con nuevo SHA y PLAN. No cambios host.

**Bloque N1 — 30–90 minutos:** RCA de R163/R205, R252, R321/R309, prebirth/cleanup; en paralelo READ_ONLY medir CI (C0) y censo código/PR; elegir primer P0 realmente modificable con owner y test. No construir D/E.

**Bloque N2 — 90–180 minutos:** resolver G1/G2/G3/G4 por ownership exclusivo y ramas pequeñas; aprovechar R334/R326/R337 existentes; tests focales primero, review, CI; unificar candidato una sola vez. Si el owner ajeno de #1113 sigue activo, no invadir, dejar dependencia explícita y ejecutar otra tarea no solapada.

**Bloque N3 — después de integración y freeze:** sólo si G0–G5 GO, correr evidencia A/B/C correspondiente a SHA exactos; luego UNA D/E y predeploy. No gastar horas en una 2.ª imagen si un guard barato ya bloquea.

**Bloque N4 — si D/E/predeploy/transport exactos GO:** obtener DEPLOY_OWNER, Actions R309/R321 root install protegido, hash/uid/mode/inode, Deploy V2 product una vez, validar runtime G11. Si transporte/credenciales/host no certificables, cerrar con HOLD específico y evidencia, NO pedir SSH manual como rutina.

**Bloque N5 — salida:** documentación de alcance y mejoramientos C0/C1/C2 con PRs o propuesta; ningún cleanup peligroso; resumen con table de estados, runs, bloqueantes, owners, métricas y próximo comando seguro. No dejar chats abiertos como único registro.

Cada bloque termina con comentario **delta** en #1093 (no repetir toda la guía). Reconciliar HEAD y #980 antes de *cada* write/merge/deploy. Trabajos de análisis independiente se paralelizan, pero integraciones y deploy no compiten.

## 11. Procedimiento para Actions, navegador y permisos

1. Preferir conector GitHub para `GET` repos/PR/checks/logs/ref y para cambios autorizados en branch. Si falla por timeout, **comprobar primero si la operación ya llegó al servidor** (buscar comentario/branch/PR) antes de reintentarlo; evita dobles PR/workflow.
2. Fallback: **navegador interno GitHub** con la sesión de titular ya disponible y autenticada; leer run/job/log/check/artifacts y completar acción cuando el conector no pueda. No usar búsqueda pública para sustituir autenticación o inventar login. Nunca exponer tokens, claves ni secretos de Actions.
3. Para diagnóstico droplet usar `workflow_dispatch` **READ_ONLY** de Actions en trusted main, con SHA/ref fijo, command allowlist, `timeout`, salida filtrada, artifact SHA y receipt. Si no existe job READ_ONLY adecuado, programar PR mínimo, sin saltarse reviews; no habilitar `bash`/`sh -c` arbitrario como “solución de permisos”.
4. Para root usar únicamente la ruta R309/R321 owner-approved ya auditada, con `DO_*` Secrets y known_hosts; si falta secret/permission de runner, documentar `PERMISSION_BLOCKED` y la capacidad faltante sin filtrar valor. Propietario de GitHub ≠ root del droplet; el host autoriza por SSH+sudo y guard propios.
5. No pedir copiar scripts de varias pantallas a Tincho; su tablet es voz. Sólo **última excepción break-glass**, un único bloque de copiar/pegar, de salida corta, con captura íntegra al portapapeles al final y **sin cerrar terminal**, redactando credenciales. El bloque debe probar sintaxis y ruta de copia (`wl-copy`, `xclip` o `xsel` según disponibilidad); si no existe mecanismo, decirlo antes. No saturar portapapeles ni pedir transcripciones extensas.
6. No reintentar red o permisos más de una vez sin nueva evidencia. No cambiar security/TLS, product safety, PPI Watch o límites de shell por impaciencia. Si GitHub Actions puede ejecutarlo, usar Actions.
7. En caso de actividad concurrente, revalidar mutex y owners en #980. No cambiar estado de un deploy ajeno. No crear nuevos supervisores recurrentes: usar los vigentes sin duplicar.

## 12. Criterio para cambiar política sin debilitar la seguridad

El titular pide explícitamente acelerar deploys. Clasificar cada regla:

- **No negociable:** PAPER/SHADOW, órdenes reales bloqueadas, PPI Watch intocable, identidad SHA/digest, owner+mutex, host pin/ROOT privileges, DB, no rollback, test que detecta orden real, failure fail-closed.
- **Optimizable con evidencia:** cantidad de suites y matriz, triggers por docs, duplicación de jobs, retención de build outputs, cachés, imagen de test vs imagen RELEASE, timeouts, formato de comprobantes, tamaño de workflows, frecuencia de preflight.
- **Revisable sólo por cambio de contrato explícito en repo:** gates de promoción específicos, requisito de D/E externa, release owner consent, delete permissions. Tener autorización general del usuario no hace que un chequeo de seguridad ausente sea GREEN; si el gate es redundante demostrar equivalencia y cambiar guard con test/CI en PR.
- **No bloquear por causas externas ajenas al deploy:** mercado cerrado, fuente de mercado externa AMBER sin instrumentos útiles, instrumento individual NO READY. Dejar alarmas y fail-closed para trade, pero no inventar veto a promoción de infraestructura si policy permite deploy offline. Distinguir source SLA de integrity fail.

Cada relajación exige propuesta, diff, test negativo, revisión e integración por PR con métrica; nunca parche manual invisible en droplet.

## 13. Estructura de evidencia y checkpoint que escribirá cada agente

Publicar comentario en #1093 al entrar/salir de bloque con este formato conciso:

```text
A1115-R### | UTC=... | ART=... | WORKSTREAM=... | owner=READ_ONLY/WRITE_OWNER/DEPLOY_OWNER
BASE: main=<sha> | product=<sha> | candidate PR#1087 HEAD=<sha> | PR#1113=<state,sha>
DELTA vs último checkpoint: ...
HECHO VERIFICADO: <URL GitHub/Action/commit/artifact/receipt exacto>
CÓDIGO: <estado> | CI: <run/status> | ARTEFACTO: <digest o NO_VERIFICADO>
HOST: <receipt o NO_VERIFICADO> | RUNTIME: <receipt o NO_VERIFICADO>
PAPER invariantes: PRODUCTION_PAPER / SIMULATION; REAL_ORDERS_SENT=0 físico [verificado/no_verificado];
  CASH_SWEEP_ORDER_ROUTING_ALLOWED=False; PPI Watch NO TOCAR; fail-closed.
STOPPER: <firma exacta + primer error + RCA y guard o NO_VERIFICADO>
EJECUTADO: <archivos/commits/runs nuevos; si ninguno, READ_ONLY>
NO EJECUTADO / POR QUÉ: ...
PENDIENTES P0 en orden: ...
METRICAS CI: p50/p90, tests, imagen/SHA, tiempo jobs, cache [si disponible]
RIESGOS / REGRESIONES: ...
OWNERS activos/entregados: ...
NEXT EXACT ACTION: ...
```

Si se edita un archivo Markdown vivo, el checkpoint indica su **commit SHA** y no sobreescribe relato histórico. Si un check no se puede verificar, `NO_VERIFICADO` con motivo concreto. Si el agente no puede operar más, dejar handoff suficiente para otro agente **sin depender de este chat**.

## 14. Criterios de entrega de la noche / decision tree de NO_GO

- **GO TOTAL:** cada G0–G12 tiene recibo exacto; producto/runtime SHA/digest coinciden; cero orden real **medido**; PPI Watch intacto; cleanup por propiedad medido; #1093 y #980 actualizados. El agente puede decir “droplet VALIDADO_RUNTIME”.
- **GO código / NO_GO host:** source, tests y PR correctos, pero R321/R309, R271 o root env física sin certificado. No enviar falso “instalado”. Bloqueante: permisos/transporte/componente exacto.
- **GO host / NO_GO release:** R271 físico y root/seal comprobados, D/E/R252 todavía faltan. No fabricar GO ni hacer deploy por presión de tiempo.
- **NO_GO por CI/privacy:** R205/R206, D/E o guard de imagen no pasa. No suprimir el test, no reconstruir tres veces. Entregar RCA y próximo test focal.
- **No hay acceso GitHub/Actions:** documentar primer error, hora y alcance, activar navegador interno si disponible; si ambos bloquean, finalizar con `BLOQUEADO` específico. No se permite extracción externa de secretos ni acceso manual improvisado.

**Cierre obligatorio:** #1093 resume todo lo ejecutado, cambios pendientes, RCA/guard, deltas, medidas de tiempo/costo, decisiones tomadas, warnings, SHA/PR/run y próximo P0. #980 libera los owners cuyo write acabó. Este documento se corrige por PR si cambian las reglas; no se “edita mentalmente” en el chat.

## 15. Prompt listo para agente nocturno (copiar completo en nuevo chat Work)

> CONTINUAR POROTA TRADING RC6 — MISIÓN AUTÓNOMA NOCTURNA DESDE A1115-R349.  
> Leer ÍNTEGRAMENTE `https://github.com/mbalbo2023/Porota-trading/blob/main/docs/governance/RC6_NIGHT_AUTONOMOUS_EXECUTION_20261113.md`, ambos AGENTS.md, policy YAML del product, últimos #1093 y #980. Reconciliar SHA actuales de main/product/candidate #1087 y PR #1113, Actions, checks, artifacts y host. La guía R349 tiene una foto histórica: usar únicamente HEAD y recibos más recientes al actuar.  
> PROCEDER autónomamente, sin volver a solicitar autorización ordinaria: diagnosticar primero R163/R205 privacy FAIL, R252 D/E missing, R271/R272 host absent, R321/R309 physical transport HOLD, R334 (owner activo), R326/R337/R308 (fuentes existentes), OCI prebirth/cleanup R348b. Buscar código/PR/tests equivalentes antes de programar; no repetir código ni pruebas completas, no reabrir PRs viejos, no mover #1087 ni product sin gates.  
> Abrir WRITE_OWNER exclusivo en #980 por scope, branches y PRs, revisión y tests focales. Medir y simplificar CI/CD/cache/build once como plan C0–C3, sin bajar garantías, y censar Python/requirements/ramas/Markdown; no borrados ciegos. Control GitHub conector; si falla, navegador interno; droplet sólo mediante Actions/transport acotado, SSH manual break-glass. PAPER/SHADOW, real_orders_sent=0 físico obligatorio antes de declarar GO, PPI Watch y Boca NO TOCAR, fix-forward, sin rollback ni stress, deploy mutex único.  
> Integrar un único candidato, generar release D/E sólo tras freeze SHA y gates, hacer **un único Deploy V2** cuando R252 y host/root estén certificados, verificar droplet físico, DB/logs/APIs/3 CID y registrar cada etapa/errores/guard/CI-métricas en #1093. Si no se puede, entregar HOLD79 preciso y evidencia, sin pedir nuevamente el permiso general. Liberar owners #980 al terminar. No confundir GREEN de CI con runtime. No usar el chat como fuente de verdad.

---

**Control de modificación:** para mantener este runbook útil, actualizar la tabla de §1 con un checkpoint **nuevo de fecha** o enlazar el más reciente en #1093, sin borrar la historia. Las secciones de procedimiento deben cambiar sólo mediante PR específico con scope, revisión, test de links y versionado. Evitar Markdown duplicado o “instrucciones secretas” entre chats.