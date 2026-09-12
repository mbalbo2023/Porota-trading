# POROTA TRADING RC6 — CHECKPOINT F01 CORRECCIÓN
Fecha: 2026-09-12
Rama: fix/rc6-f01-exit-model-parity-20260912
Commit actual: 43fb9d511c3b572388d5765f4489f946c5879b8c

## Estado
- F-01 (paridad backtest/PAPER): **CORREGIDO EN RAMA**
- Runtime productivo: **SIN CAMBIOS / SIN DEPLOY**
- Modo certificado preservado: PRODUCTION_PAPER / EXECUTION=SIMULATED
- Órdenes reales: bloqueadas; no se ejecutó ninguna operación.
- F-02 y F-03: análisis/corrección en paralelo, pendientes de integración y prueba.

## Cambios F-01
- Se agregó bk_exit_model.py con PAPER_FIXED_PERCENT_V1.
- PaperBroker usa la función canónica para diagnóstico económico y registro de stop/target.
- El default del broker quedó alineado con la política vigente: stop 2%, objetivo 5%.
- El backtest legado retirado dejó de calcular barreras ATR y usa el mismo modelo porcentual PAPER si se invoca internamente.
- by_strategy_backtest.py declara cualquier candidato ATR como no comparable con PAPER y no promovible.
- Las posiciones registran modelo y parámetros efectivos de salida.
- Se agregaron pruebas de paridad y se actualizaron las pruebas del backtest retirado.

## Validación
- Regresiones focalizadas de PaperBroker, supervisión de salidas, take-profit, backtest/históricos y producción PAPER: **GREEN**.
- Compilación Python de los módulos modificados: **GREEN**.
- No se hicieron llamadas de órdenes, cambios de credenciales ni cambios en el host.

## Próximo paso
Resolver F-02 y F-03 en ramas de trabajo locales, integrar sólo después de pruebas regresivas, actualizar este checkpoint y solicitar revisión antes de cualquier promoción o despliegue.

Nota: no se elimina ninguna rama, código ni evidencia sin especificar exactamente el objeto a eliminar.
