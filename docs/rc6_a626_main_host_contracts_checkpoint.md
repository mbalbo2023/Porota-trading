# RC6 A626 — control-plane de censo financiero host (sin deploy)

Estado: EN_GITHUB en PR DRAFT #905 contra `main`, rama
`work/rc6-a626-main-host-contract-aggregates-ro-20261104`.
Owner exclusivo: `RC6_A626_MAIN_HOST_CONTRACT_RO`.

## RCA

Los 819 pendientes A385 describen `financial_instrument_catalog.capability`.
El productor A385 no abrió SQLite host y no hizo JOIN con
`contract_evidence_v2_current`, `contract_evidence_v2_snapshots` ni
`financial_instrument_catalog.metadata_json`. La ausencia física de 819
contratos queda NO_VERIFICADO, al igual que la completitud/autoridad financiera.

## FIX propuesto en main

Un workflow manual independiente (sin promover producto) obtiene sólo
agregados por BONOS, OBLIGACIONES, OPCIONES, FUTUROS, CAUCIONES y FCI.
Requiere un `workflow_dispatch` con booleano explícito
`permit_readonly_host_probe=true` desde `main`. Un dispatch `false`
ejecuta únicamente tests sintéticos, sin SSH.

El probe remoto sólo puede abrir la DB si corre tras `unshare --mount`
en un bind **privado, RO** de `/opt/porota-trading/data`. Valida
`/proc/self/mountinfo`, namespace distinto del PID1, ausencia de alias,
sidecars WAL/SHM/journal, `paper_workspace` del dataset PAPER, y hace
`sqlite3 mode=ro` con `query_only=ON`, transacción corta y progreso
acotado. **No usa `immutable=1` sobre WAL vivo.**

La revisión de snapshots históricos PPI hace **una sola lectura streaming**
acotada por filas, bytes y tiempo y consulta pertenencia en memoria;
no ejecuta una consulta por cada instrumento, no crea índices,
temporales SQLite en disco ni exporta la base.

## Guardas

- Sólo se imprime JSON agregado por familia, contadores de causas y
  columnas permitidas; no se imprimen tickers ni identidades individuales.
- `paper_workspace` **no prueba autoridad PPI**; los campos de procedencia,
  PIT causal, comisión/FX y readiness quedan NO_VERIFICADO.
- No se ejecuta ninguna orden ni se habilita automáticamente PAPER.
- Host inaccesible, falta de namespace/remount RO, WAL/SHM inseguro o
  esquema diferente producen NO_VERIFICADO **antes de mutar runtime**.
- PPI Watch, timers, DB y otros contenedores permanecen intocados.
- No Predeploy ni Deploy, no merge a la rama productiva.

## Pruebas y evidencia

Suite sintética en `tests/test_rc6_a626_host_contract_aggregates.py`:
identidad 5-key, divisa/USD_MEP, V2 huérfanos, histórico sin current,
namespace/sidecars, test de montaje RO y negativa, invariancia del SHA
DB y que un PR nunca dispara el host. GitHub CI debe validarse sobre
el SHA exacto final de #905. Un workflow exitoso sin host no prueba
resultados físicos; una ejecución de host posterior tampoco autoriza READY.

## Decisión pendiente

Tras CI y revisión exacta de #905, integración **sólo del plano de control**
en `main`; entonces prueba manual `permit_readonly_host_probe=false`
y, tras GO del dueño, una única ejecución `true` durante rueda. Reconciliar
los agregados físicos contra A385 y preparar correcciones por evidencia
real de fuente/familia. El materializador efectivo y admisión PAPER
siguen bloqueados hasta fuente auténtica y gates.
