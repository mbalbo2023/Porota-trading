# POROTA TRADING — HF6-v2 Release Candidate

## Estado

CANDIDATE_PREPARATION=AUTHORIZED
DEPLOY_AUTHORIZED=NO
REAL_ORDERS_ALLOWED=NO
ACTIVE_RUNTIME_TO_REPLACE=NO

## Fuente congelada

- Baseline contractual HF6: `0f68d6de747273898642237b863457a04ecc762e`.
- WIP validado: `a03cf47d5db01d8990a99f3f7836879c0606771b`.
- Rama de preparación: `release/v17.0.0-rc3-hf6-v2-candidate`.
- El WIP está 86 commits por delante del baseline y no está detrás de él.
- La imagen candidata se materializa desde el SHA WIP congelado y aplica patchers deterministas; los commits posteriores en esta rama son únicamente preparación/correcciones de release y documentación.

## Identidad candidata

El builder usa temporalmente `17.0.0-rc3-hf6-v2-candidate1` dentro del árbol materializado y etiqueta la imagen `porota-trading-bot:17.0.0-rc3-hf6-v2-candidate1`.

Esto NO renombra todavía la versión productiva ni mueve el tag inmutable `v17.0.0-rc3-hf6`.

## Cambios materializados

Orden obligatorio de patchers:

1. Dashboard UX/logs.
2. Sistema → Scheduler.
3. Resultado diario + Reportes responsive.
4. Históricos dinámicos / History Store v2.
5. Riesgo concurrente dinámico.
6. Prioridad histórica exacta `A3_CEM_CLOSING`.
7. Etiquetas LEGACY para horarios globales y cap histórico de posiciones.
8. Identidad temporal de release candidate.

El árbol resultante debe cumplir:

- `self.max_positions` no participa del gate normal de `_open()`.
- `PAPER_EMERGENCY_MAX_OPEN_POSITIONS` es exclusivamente guardia anti-runaway, default 50.
- riesgo concurrente se revalida dentro de la misma transacción que registra la compra PAPER.
- PPI es la única fuente live síncrona autoritativa para las familias existentes cuando sus campos requeridos están completos y frescos.
- diferencias A3/PPI nunca ponen una operación en HOLD si PPI dispone de los datos live requeridos.
- A3/CEM/Data912 son background/histórico/contractual y no pueden enrutar órdenes.
- CEM `settlement` es precio de ajuste y nunca el plazo de liquidación de identidad de Porota.
- IA intradiaria permanece deprecada/OFF.
- órdenes reales permanecen bloqueadas.

## Dashboard incluido

- Panel: resultado de las últimas jornadas por moneda, sin sumar ARS y USD.
- Reportes: cards verticales responsive; no tabla horizontal principal.
- Listados: presentación compacta/paginable para evitar páginas gigantes.
- Trading: familias visibles aunque estén HOLD; Scalping como estrategia dentro de Trading.
- Instrumentos/Contratos: descubrimiento, evidencia, histórico y readiness separados.
- Sistema → Scheduler: jobs internos + timers host, última corrida, resultado y próxima ejecución.
- Sistema → Logs: snapshot sanitizado sin Docker socket.
- Históricos: objetivo dinámico por familia; desaparece el denominador fijo 243.

## Riesgo

El gate financiero normal deja de ser un número pequeño de posiciones simultáneas. La admisión considera:

`pérdidas realizadas consumidas + riesgo a stop de posiciones abiertas + riesgo de candidata <= presupuesto del soft stop`

Las ganancias realizadas no aumentan el presupuesto. Se mantienen además caja, exposición total, exposición por posición, profundidad, contratos, costos y hard stop.

## Caución de caja al cierre

El cash sweep permanece FAIL-CLOSED hasta que oferta, mínimo, step, costos exactos, cutoff, vencimiento, obligaciones de caja y deadline de liquidez estén verificados. La reserva no es un porcentaje fijo: es la suma de obligaciones verificadas que deben pagarse antes de recuperar la caución.

El orquestador alimenta el allocator PAPER existente; no crea un segundo ledger ni una ruta real de órdenes.

## Seguridad del build

El contexto de build debe excluir `.git`, backups de patchers, `.env`, DB/WAL/SHM, logs, credenciales cifradas y archivos de secretos. `.dockerignore` continúa bloqueando runtime/secrets del `COPY . .`.

La imagen exacta se valida con red deshabilitada y sin arrancar `entrypoint.py` con credenciales.

## Condición para pedir GO de deploy

No se pedirá GO hasta obtener simultáneamente:

- source freeze OK;
- patchers OK;
- invariantes estáticos OK;
- sintaxis no-write OK;
- regresión source-tree OK;
- contexto de build limpio;
- build de imagen OK;
- regresión sobre la imagen exacta OK;
- security scan de imagen OK;
- observer activo sin cambios;
- `real_orders_sent=0`;
- rollback y matriz READY/HOLD revisados.
