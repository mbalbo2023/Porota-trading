# RC6 A178 — RCA de despliegues encadenados y transición A175 (29-Oct-2026 ART)

**Estado:** NO_GO operacional. Este informe separa evidencia de Actions, código en rama, paquete, host y comportamiento runtime. Ningún GREEN de un control parcial equivale a `VALIDATED_RUNTIME`.

## Ownership y fuente de verdad

- Handoff A176: [#732/6485830885](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6485830885), checkpoint `8152e0a2ea3cf3138e1ba1138b0465a080ff561d`.
- A177 detenido por falta de créditos, confirmado expresamente por el operador; claim anterior **reemplazado**, no falsamente liberado. A178 `RC6-A178-DEPLOY-CONTINUITY-20261029`, ownership exclusivo pareado [#28/6496467866](https://github.com/mbalbo2023/Porota-trading/issues/28#issuecomment-6496467866) y [#679/6496467409](https://github.com/mbalbo2023/Porota-trading/issues/679#issuecomment-6496467409).
- Productivo al takeover: `3c5210fef9d4f1d4c95b286b0dbc61b6654fe055`. Candidato actual: rama aislada `fix/rc6-a178-formal-takeover-20261029`, NO mergeada a productiva.
- Cualquier cambio en runtime/estado debe ser observado por Actions y recibos, nunca inferido por este documento.

## RCA sistémica de los Deploy V2 fallidos

**Ampliación longitudinal A178:** el [registro forense 15-Sep–29-Oct](RC6_A178_HISTORICAL_DEPLOY_DEGRADATION_20261029.md) y su CSV de **458 ejecuciones** demuestran que los RED encadenados no comenzaron el 28-Oct: hubo un deterioro medible desde el 22-Sep y una expansión del workflow de 467/9 a 2.639/33 líneas/pasos entre el 27-Sep y el 28-Oct. El problema sistémico es una matriz de transiciones host/versión/día incompleta dentro de un YAML de complejidad creciente; ni su duración ni su causa deben atribuirse a una sola fuente complementaria.

`Predeploy V2` verificaba el árbol y múltiples pruebas aisladas, pero **no modelaba íntegramente la transición del host desde un despliegue anterior fallido**: fuente instalada, env transitorio, locks, recibos históricos, identidad de tags vs ID inmutable, temporizadores y marcador `PENDING`. Cada fix-forward cruzó una frontera distinta y encontró la siguiente recién en el Droplet. Los ocho Deploy RED del 28-Oct no equivalen a ocho regresiones funcionales de la estrategia PAPER, sino a ocho brechas de admisión de Deploy V2.

| Deploy V2 | Firma verificable | Defensa requerida |
| --- | --- | --- |
| [#40737073432](https://github.com/mbalbo2023/Porota-trading/actions/runs/40737073432) | `Argument list too long` por límites `SSH/execve` | Presupuesto medido de argumentos, paso de fuente por stdin/comprimido; regresión de tamaños |
| [#40750131063](https://github.com/mbalbo2023/Porota-trading/actions/runs/40750131063) | `AttributeError` por constante ausente en helper instalado A143 | Probar carga de código **instalado viejo** y código candidato staged |
| [#40755524824](https://github.com/mbalbo2023/Porota-trading/actions/runs/40755524824) | Error de timestamp gzip fuera de rango | `gzip -n` determinista + caso límite del runner |
| [#40765366297](https://github.com/mbalbo2023/Porota-trading/actions/runs/40765366297) | `COLD2_QUIESCENCE_RECEIPT_INVALID` | Contrato y migración de recibo exacto de la etapa anterior |
| [#40771934853](https://github.com/mbalbo2023/Porota-trading/actions/runs/40771934853) | `required_rc6_timers` RED en preopen | Simular estados `SUSPENDED`, guardian y pausa por día |
| [#40781245319](https://github.com/mbalbo2023/Porota-trading/actions/runs/40781245319) | `QUIESCENCE_A143_STOP_SOURCE_CHANGED` | Validar hash de fuente instalada vs staged sin intercambiarlas |
| [#40785249266](https://github.com/mbalbo2023/Porota-trading/actions/runs/40785249266) | `GO_CHECK_FAILED_AFTER_STARTUP`, scanner DEGRADED | Comprobar ventana real de bootstrap y estado de health del scanner |
| [#40796450488](https://github.com/mbalbo2023/Porota-trading/actions/runs/40796450488) | T_MINUS_45 exigía tag estable, runtime arrancó con tag candidato | Contrato separado `Config.Image` (tag) e ID inmutable, alias estable sólo cuando corresponde |

La aprobación anterior [Predeploy #40793657376](https://github.com/mbalbo2023/Porota-trading/actions/runs/40793657376) `5424/5424` verificó el **paquete A175** pero no su ejecución final. El Deploy A175 dejó dos contenedores RUNNING, BOOK FLAT, PAPER/0 y marker `DEPLOYED_VALIDATION_PENDING`; esto es `PENDING/NO_GO`, no GO.

### Aprendizaje permanente: ERROR → RCA → FIX → GUARD → TEST → EVIDENCIA

1. Definir **preflight de transición con estado previo exacto**, no sólo tests de repositorio. Un cambio de tag, SHA de recibo, dueño de directorio o timer aborta antes de iniciar instalación.
2. Separar la autenticidad del paquete (SHA/tree/artifact) de la identidad real Docker (`Config.Image`, `.Image`) y del estado de negocio (PAPER/0, posiciones, órdenes reales).
3. Versionar el contrato de runtime entre el helper instalado y el helper staged, con regresión del día siguiente: la variable `RC6_A145_QUIESCENCE_GZIP_B64` existe durante Deploy, **no se puede asumir** en el systemd nativo posterior.
4. Cada action correctiva debe tener prueba adversarial en runner **no-root**, prueba de modo/UID exacto del host y bitácora de mutaciones. Ningún test debe asumir UID local=0.
5. No tratar frescura IOL complementaria AMBER como bloqueo global de Deploy si PAPERS/safety/identidad primarias están intactas; no elevar información STALE a precio vigente.
6. No reintentar cleanup ni Deploy después de una mutación parcial sin re-sonda READ_ONLY de referencias, receipts, disk, locks y runtime. Sin rollback ni stress E09/E10.

## A176 — recibo NO_GO del día 29 y RCA de su ejecución

- [Actions #40874356609](https://github.com/mbalbo2023/Porota-trading/actions/runs/40874356609) RED **antes del host**: fixture en tests suponía UID root del runner. Fix: identidad de fixture simulada sin desactivar requisito de UID=0 para el host real.
- [Actions #40874797520](https://github.com/mbalbo2023/Porota-trading/actions/runs/40874797520) RED **sin escritura**: directorio `data/deploy` no root-owned. No se relajó por inferencia.
- [Actions #40875328428](https://github.com/mbalbo2023/Porota-trading/actions/runs/40875328428) READ_ONLY: `data` y `data/deploy` UID/GID=1000, modo `0750`, no symlinks; marcador y A151 son archivos root-owned sin permisos de escritura del grupo. El writer ahora exige exactamente este contrato medido.
- [Actions #40875630811](https://github.com/mbalbo2023/Porota-trading/actions/runs/40875630811) SUCCESS: `A176-40875630811-1`, recibo `operator-no-go-continuation-20261029.json` SHA-256 **`3218ccf5296306068d051b12588c4fac253a768ea3c81e0fc31c501adb34338c`**, una única creación atómica bajo mutex. Efectos: `real_orders_sent=0`, `paper_execution_authorized=false`, `ppi_watch=NOT_MUTATED`.
- [Actions #40875975052](https://github.com/mbalbo2023/Porota-trading/actions/runs/40875975052) SUCCESS: reejecución idempotente, mismo SHA y operación; `host_mutations=0`, sin sobrescritura.
- El código A178 contiene lectura estricta de SHA, día ART, A151 origin, A175 receipts, PAPER/0/flat, guardian, pausa preopen; falta validación Predeploy V2 del **candidato final** y Deploy V2.

## Controles pendientes antes de promoción

- **Limpieza exacta VALIDADA:** [Docker RO #40798107395 intento 2](https://github.com/mbalbo2023/Porota-trading/actions/runs/40798107395) confirmó A174/A171 sin refs y dos imágenes activas protegidas. [Cleanup #40876344156](https://github.com/mbalbo2023/Porota-trading/actions/runs/40876344156) SUCCESS: eliminó sólo A174 `sha256:9ca6e7ca98f11bbb82353434f54ffab15b218ec54319c83ae1661aaf897a40e9` y A171 `sha256:e61052af13c01d9ce76e92f38b50c794dac8a146427e132d43a8a80554a879aa`; tres contenedores protegidos intactos, PPI Watch NOT_ACCESSED, órdenes reales 0. Medición `/`: **4.339.638.272 → 7.243.264.000 bytes libres; delta 2.903.625.728 bytes**; inodos libres **2.835.312 → 2.881.348**. NO se borraron imágenes activas, DB, backups, volúmenes ni capas compartidas a la fuerza. El piso deseado de 8 GB continúa incumplido; la capacidad de Deploy debe superar preflight real, sin inferencia.
- Los workflows de un solo uso para sello y reclaim se retiraron del árbol candidato luego de sus Actions; su código de verificación sigue auditable y la evidencia queda en GitHub. Evitar nuevos disparos accidentales al integrar.
- Regresiones locales focalizadas de A175/A176, Docker, recibo, scheduler y gate: **109 PASS** en A178 tras el sello (pytest con PyYAML), más compilación Python y parseo Bash/YAML. No equivalen a suite completa.
- **Predeploy V2 #40876782493 RED** (PR #763, candidato `be35834e…`) en `Fast host-transition incident replay`, antes de build/host: tres casos del fixture viejo de OPEN detenido no simulaban el marker `CURRENT_STATE_V2.json` que el gate ahora exige antes de cualquier admisión. La corrección conserva el guard (no modifica el código de clasificación), proporciona un marcador válido a los casos legítimos y agrega un adversarial de marcador inseguro que debe bloquear sin adoptar recibo. El artifact de sólo 349 bytes del run RED **no es paquete desplegable**. Repetir Predeploy completo para SHA nuevo.
- **Predeploy V2 #40877608438 RED** (PR #763, `993ed6dc…`) en `Governed automatic test discovery and execution`: **5.497 ejecutadas, 18 fallas, 0 skips/xfail**, sin host. Las causas agrupadas por evidencia del job: 15 fixtures históricas no remapeaban `CURRENT_STATE_V2.json` cuando el classifier empezó a exigirlo; además quedaron antiguos wire/argument-size assertions (2) y la precedencia de NO_GO sobre preopen (1). Se corrigió la simulación del marker y fuentes staged **en pruebas**, preservando los guards reales y el caso de veto independiente; **189 PASS** en las cuatro suites afectadas usando `pytest` local. Falta Predeploy V2 completo del SHA nuevo; el artifact del RED (136.116 bytes, evidence-only) **no es deployable**.
- **Predeploy V2 #40880953697 RED** (PR #763, `638f673a…`): **5.499 ejecutadas, 1 falla, 0 skips/xfail**, antes de host. `test_two_processes_cannot_claim_the_same_full_budget` olvidó contemplar el caso **fail-closed de SQLite `database is locked` durante `GlobalPPIBudget.start()`** tras una admisión previa. La implementación respondió con `BudgetBackpressure('PPI_BUDGET_STATE_UNAVAILABLE')`, **no con una admisión extra a PPI**. La fixture de dos procesos ahora contabiliza `start_contention`, exige `used == started` y prueba que una lease adquirida pero no empezada permanezca `used=0`/no doble gasto. Se agregó prueba determinista adversarial `test_start_sqlite_backpressure_never_claims_a_wire_call` y se repitió la pareja de pruebas **5 veces** localmente (10 PASS). **Sin modificar el motor de presupuesto PPI ni PPI Watch.** Falta nuevo Predeploy integral; no reusar artifact del run fallido.
- Test CI completo, candidato congelado, tree y manifest concordantes; Predeploy V2 **nuevo** GREEN no inferido de A175.
- Preflight host del estado exacto anterior A175, incluyendo recibo A176, espacio y mutex; autorizaciones literales #624 ligadas al nuevo candidate SHA/tree, no reutilizables.
- Integración por PR normal y único Deploy V2 canónico del mismo artifact. Confirmar `VALIDATED_RUNTIME` en marker, contenedores, servicios/timers, scanner, DB, dashboard y `real_orders_sent=0`.
- Mantener **NO_GO operativo hasta reactivación expresa**, incluso si el Deploy técnico llegara a VALIDATED_RUNTIME. No realizar órdenes reales, rearmado automático de la operación PAPER ni stress cancelado.

**Nota de procedencia:** las fechas, IDs y estados de acciones de este documento son hechos observados; todo paso pendiente sigue `NO_VERIFICADO` hasta su Action/artefacto/runtime específico.
