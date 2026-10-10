# POROTA RC6 — contrato de continuidad para Codex (v1)

**Fecha de corte documental:** 2026-10-10  
**Workstream:** WS-CODEX-CONTINUITY-20261010  
**Alcance:** entrada a Codex desde la rama por defecto `main`. Documento de gobierno, no evidencia de runtime ni autorización de deploy.

## 1. Propósito y límite

Cada tarea nueva debe reconstruir el contexto **desde evidencia vigente de GitHub**, no desde la conversación anterior, una memoria de Codex, un checkpoint antiguo, o el hecho de haber seleccionado un entorno con nombre POROTA. La configuración de un entorno no garantiza que dos tareas compartan archivos sin commit o contexto conversacional.

Este documento es una **guía de recuperación**. Nunca declara cuál es el último SHA desplegado, los instrumentos READY, el resultado de una corrida o el dueño actual de un scope. Esos datos deben comprobarse en sus fuentes en cada inicio.

## 2. Fuentes y precedencia

1. **Código:** GitHub, rama y SHA exactos; el HEAD de `main` no es el HEAD operativo RC6.
2. **Pipeline:** GitHub Actions, run, jobs y conclusión del SHA pertinente.
3. **Paquete:** artefacto, manifest, tree, digest/hash y su correspondencia con el candidato.
4. **Deploy:** evidencia directa del Droplet/runtime. Un workflow GREEN no basta.
5. **Comportamiento:** DB, logs y APIs con timestamps y procedencia.
6. **Documentos/Issues/PRs:** decisiones, scopes e hipótesis contextualizados por fecha y commit.
7. **Memoria o chat:** ayuda para localizar evidencia; nunca autoridad operacional.

Usar, según la evidencia: `PROPUESTO`, `DESARROLLADO`, `COMMITTEADO`, `EN_GITHUB`, `ARTEFACTO_VALIDADO`, `DESPLEGADO`, `VALIDADO_RUNTIME`, `NO_VERIFICADO`, `BLOQUEADO`. No convertir el pasado documentado en una afirmación actual sin verificarlo.

## 3. Lecturas obligatorias al iniciar una tarea RC6

1. Consultar `/AGENTS.md` de la rama efectivamente seleccionada.
2. Resolver con GitHub la rama productiva RC6 **actual**. En el corte documental de esta guía era `deploy/rc6-pr69-isolated-20260915`, pero **no reutilizar este dato sin verificarlo**.
3. Obtener su HEAD/SHA actual desde GitHub y leer **su** `AGENTS.md` y `ops/policy/porota-policy.yaml`.
4. Consultar el último release/handoff aplicable (por ejemplo, los archivos de `docs/releases/`) y la evidencia posterior en Actions, artifacts y GitHub. Cualquier handoff es histórico hasta que se contraste con el HEAD y la evidencia contemporánea.
5. Examinar Issues y PRs abiertos, incluidos los workstreams de integración y coordinación, sus heads y los scopes con `WRITE_OWNER` o `DEPLOY_OWNER`. Confirmar que el trabajo propio no se superpone.
6. Leer el Issue/orden específica que define esta misión, en forma completa; sus límites de alcance no los reemplaza este documento.
7. Identificar si la acción solicitada requiere datos actuales del runtime. Si no son accesibles, reportar `NO_VERIFICADO`, sin inferirlos de código o CI.

**Enlaces de entrada:** [AGENTS principal](../../AGENTS.md), [rama productiva (navegable)](https://github.com/mbalbo2023/Porota-trading/tree/deploy/rc6-pr69-isolated-20260915), [Issues](https://github.com/mbalbo2023/Porota-trading/issues), [PRs](https://github.com/mbalbo2023/Porota-trading/pulls), [Actions](https://github.com/mbalbo2023/Porota-trading/actions).

## 4. Invariantes no negociables

- **PAPER/SHADOW ONLY**: modo esperado `PRODUCTION_PAPER / SIMULATION`; `real_orders_sent=0`; rutas de órdenes reales bloqueadas/no llamadas.
- PPI Watch: **NO TOCAR** sin autorización explícita y específica.
- **FIX-FORWARD ONLY**: sin rollback.
- GitHub y GitHub Actions: plano de control normal. SSH/manual: únicamente break-glass. No usar Codespaces para operación normal.
- Ninguna tarea desarrolla directamente en `main` ni en la rama productiva; usar branch aislada y PR.
- Separar readiness contractual, viabilidad del instrumento, estrategia y ventaja económica. PPI es autoridad primaria de identidad; IOL y demás fuentes sólo complementan.
- Todo error repetible significativo sigue `ERROR → RCA → FIX → GUARD → TEST/CI/PREFLIGHT → EVIDENCIA`.
- Un único candidato reconciliado y un único deploy final. No usar éxito de tests como sustituto de comprobación de runtime.
- El control por voz y la accesibilidad son parte del contrato: no exigir terminal ni comandos manuales al usuario para trabajo rutinario.

## 5. Ownership antes de escribir

- `READ_ONLY`: lectura/auditoría sin mutación, nunca bloquea.
- `WRITE_OWNER`: exclusivo para rutas/componentes definidos; declarar identificador, branch aislada, SHA base real, alcance y paths. En caso de solapamiento, no escribir: continuar en `READ_ONLY` o redefinir un scope legítimamente separado.
- `DEPLOY_OWNER`: exclusivo global. Nadie más despliega, reinicia servicios, modifica systemd/DB ni limpia Docker mientras esté activo.
- Mutex de promoción: `rc6-unified-paper-deploy`, `cancel-in-progress: false`.

Ningún PR antiguo o amplio se fusiona ciegamente. No borrar heads de PRs abiertos, branches de REVIEW ni evidencia única.

## 6. Handoff obligatorio por hito material

Al terminar cada bloque de trabajo, dejar en el PR o documento canónico de la propia rama:

```text
WORKSTREAM_ID:
MODE: READ_ONLY | WRITE_OWNER | DEPLOY_OWNER
SOURCE_BRANCH / SOURCE_SHA:
BASE_BRANCH / BASE_SHA:
SCOPE_PATHS:
PR / COMMIT / TREE:
TESTS_AND_ACTIONS_RUN_IDS:
ARTIFACT_MANIFEST_DIGEST: o NO_VERIFICADO
RUNTIME_EVIDENCE: o NO_VERIFICADO
PAPER_SAFETY_EVIDENCE: o NO_VERIFICADO
DECISIONS / RCA / GUARDS:
BLOCKERS / PENDING / NEXT_OWNER:
OWNERSHIP: ACTIVE | RELEASED
```

No incluir secretos, tokens, datos de cuenta, logs sensibles ni fotografías de credenciales. La evidencia única debe permanecer rastreable mediante URL/ID/SHA, no mediante recuerdo de un chat.

## 7. Prueba de recuperación de contexto en un Codex nuevo

Una tarea completamente nueva y **READ_ONLY** debe poder responder con fuentes y timestamps:

1. Qué rama y SHA productivos encontró **ahora**, diferenciando `main` de runtime.
2. Qué `AGENTS.md` y política leyó, y cuáles son las prohibiciones PAPER/PPI Watch.
3. Qué PRs/workstreams tienen scopes pertinentes y si existe conflicto de ownership.
4. Qué evidencia hay de CI, artifact y runtime, por separado, usando `NO_VERIFICADO` si falta.
5. Qué no hará sin autorización: merge, deploy o mutación de runtime.

La prueba debe fallar si el agente confunde un SHA de ejemplo con la verdad actual, proclama runtime validado por CI GREEN o se adjudica un scope ya ocupado. No basta con que diga «leí los archivos»: debe citar rutas/SHAs verificables.

## 8. Correcciones pendientes en productiva, fuera de este scope

Este cambio de `main` **no actualiza automáticamente** `AGENTS.md` ni `README.md` de la rama productiva. El README productivo conserva texto de una versión legacy, incluidas referencias a operación real incompatibles con PAPER/SHADOW. Resolverlo en un PR aislado coordinado con el integrador, **sin fusionarlo en productiva sólo por ser documentación**: los cambios en raíz pueden activar el Deploy V2 vigente.

En esa integración, añadir a los checks **existentes** de Predeploy V2 (no a un workflow nuevo innecesario) un guard automatizado para comprobar: existencia y coherencia del bootstrap; precedencia de autoridad; prohibiciones PAPER y PPI Watch; ausencia de rutas/documentación que habiliten órdenes reales; links y archivos obligatorios; y fixtures negativos de contexto obsoleto. Probar contra el candidato exacto y preservar la política de artefacto único.

**Estado de estas mejoras productivas:** `PROPUESTO`. No insinuar que están integradas, desplegadas o validadas.
