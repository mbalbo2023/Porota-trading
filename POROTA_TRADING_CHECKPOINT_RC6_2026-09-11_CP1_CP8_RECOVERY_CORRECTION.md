# POROTA TRADING RC6 — CORRECCIÓN CANÓNICA CP1–CP8 — 2026-09-11

## Propósito
Checkpoint correctivo posterior a la recuperación del chat saturado. La recuperación visible había quedado demasiado angosta al mostrar CP2–CP5. Este archivo restituye el programa completo CP1–CP8 y continúa, no reemplaza, la cadena de checkpoints previos.

## Rama / HEAD base antes de este checkpoint
- Rama: `fix/rc6-w10-sector-map-binding-20260910`
- HEAD base verificado: `99d0f7f444e553b83bc230041f346600caf826ab`
- Checkpoint previo de recuperación: `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_RECOVERY_GO_LIVE_WATCH.md`
- Checkpoint CP2–CP5 previo: `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_PARALLEL_CP2_CP5_BARRIER.md`

## Corrección de alcance
El checkpoint CP2–CP5 era explícitamente incremental y no reemplazaba los checkpoints anteriores. La matriz de recuperación posterior heredó sólo CP2–CP5 y por eso perdió visibilidad de CP1 y de las etapas CP6–CP8. Esta corrección vuelve obligatorios los ocho gates para el GO-LIVE.

## Evidencia de CP1
La rama actual contiene `rc6-cp1-parallel-runtime-audit-20260911.yml`, una barrera real de auditoría runtime/no-regression que recorre Waves 1–18, verifica el runtime PAPER, `real_orders_sent=0`, UX/validation, Contract Evidence/history, risk/GDELT y otros frentes en modo read-only. CP1 no fue eliminado; quedó omitido de la matriz abreviada de recuperación.

## Matriz canónica CP1–CP8
### CP1 — auditoría amplia Waves 1–18 / no-regression / baseline de seguridad
- OBLIGATORIO.
- Debe revalidarse sobre el candidato final/HEAD final si cambios posteriores pueden afectar sus supuestos.
- No se hereda GREEN ciegamente desde un HEAD anterior.

### CP2 — UX / ledger / dashboard operational wiring / PRODUCTION_PAPER
- Source + focused tests: GREEN en la barrera CP2–CP5.
- Falta revalidación integrada/final sobre el candidato definitivo antes de cerrar el programa.

### CP3 — Contract Evidence / PPI trusted read-only / capture → materialization
- Estado: YELLOW, bloqueante.
- Root cause/shape localizado.
- `blocked_nonread` demostrado como `list`, longitud `0`, booleano `False`, sin filas untrusted.
- Falta formalizar/certificar el gate fail-closed y cerrar la ruta de materialización con evidencia explícita, sin inferir datos contractuales/económicos.

### CP4 — historical ingestion/backfill/rejections + A3 DLR deterministic identity
- Estado: YELLOW, bloqueante.
- Mapper DLR determinístico/fail-closed identificado para contratos simples demostrados.
- Falta wiring mínimo a adquisición histórica, focused proof y conservación fail-closed para aliases/spreads no demostrados.

### CP5 — risk/GDELT + UX asociada
- Source + focused tests: GREEN.
- GDELT permanece estrictamente `SHADOW_ONLY`.
- Falta revalidación integrada/final sobre el candidato definitivo.

### CP6 — cierre de gaps operativos residuales
- PENDING.
- Sólo se corrigen gaps reales que sobrevivan CP1–CP5.
- Prohibido rehacer/redeployar frentes ya probados sin evidencia de regresión.

### CP7 — barrera integrada limpia Waves 1–18 / 18-of-18
- PENDING.
- Requiere clean-tree/full integrated regression, seguridad y cross-wave regression.
- Ningún deploy se considera final mientras CP7 no esté GREEN.

### CP8 — deploy/runtime/postflight + readiness nocturna/preapertura
- PENDING.
- Incluye preflight/deploy/postflight auditados, salud runtime, timers/schedulers/next-trigger proof.
- Sólo después de postdeploy satisfactorio: activar/verificar historical ingestion, backfill, scraping/Contract Evidence y demás jobs de adquisición aprobados, y medir freshness/cobertura para la próxima rueda.

## Política de recuperación de errores — NO ROLLBACK
Esta política supersede cualquier redacción previa de “rollback readiness”.
- No rollback automático.
- No usar rollback como estrategia de recuperación.
- Ante todo error: capturar firma exacta → RCA → smallest evidence-based forward fix → revalidar → continuar hasta pasar.
- No repetir ciegamente un workflow fallido con inputs idénticos.
- Si la misma falla se repite sin evidencia nueva, cambiar la vía diagnóstica/fix; no entrar en loop.

## Seguridad innegociable
- `PRODUCTION_PAPER`.
- `REAL_ORDER_CAPABILITY=BLOCKED`.
- `real_orders_sent=0`.
- Validaciones sin llamar rutas de órdenes reales.
- PPI autenticado exclusivamente read-only/fail-closed para evidencia.
- No inventar valores contractuales/económicos ausentes.
- GDELT `SHADOW_ONLY`.

## Ejecución paralela desde este checkpoint
Track A: CP3 formal gate/fix/certification.

Track B: CP4 DLR historical wiring/focused proof.

Track C: revalidación de supuestos CP1/CP2/CP5 sobre el candidato en evolución, sin source churn innecesario.

Luego: CP6 sólo gaps residuales → CP7 integrated 18/18 → preflight/deploy/postflight → CP8 activación/verificación de datos y readiness de próxima rueda.

## Supervisor autónomo
`RC6 Go-Live Watch` fue actualizado para tratar CP1–CP8 como gates obligatorios y para aplicar la política NO-ROLLBACK/RCA+forward-fix. Debe generar/actualizar checkpoint después de cada milestone grande y no declarar GREEN sin evidencia concreta.

## Estado global
- `GLOBAL_RC6=YELLOW`.
- `GO_18_OF_18=NO`.
- `READY_FOR_FINAL_DEPLOY=NO` hasta cerrar CP3/CP4, resolver sólo gaps CP6 si aparecen y certificar CP7.
- CP8 se cierra únicamente con runtime/postflight + data jobs/freshness comprobados.

## Próximo paso exacto
Continuar inmediatamente y en paralelo con CP3 ∥ CP4 ∥ revalidación CP1/CP2/CP5; luego cerrar CP6 residual, CP7 y CP8 bajo política NO-ROLLBACK.
