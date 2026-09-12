# POROTA TRADING RC6 — CHECKPOINT F01/F02/F03/F04
Fecha: 2026-09-12
Rama: fix/rc6-f01-exit-model-parity-20260912
Commit actual previo a este checkpoint: 202678f4d7d3b20469fc6740d69a61bca8e5f940

## Estado
- F-01 (paridad backtest/PAPER): **CORREGIDO**
- F-02 (identidad completa en latest_quote): **CORREGIDO**
- F-03 (SQLite en DailyRisk): **CORREGIDO**
- F-04 (POST de estimación IOL): **CORREGIDO CON OPT-IN EXPLÍCITO**
- F-05 (dependencias reproducibles): **ABIERTO; SIN CAMBIO SEGURO AÚN**
- Runtime productivo: **SIN CAMBIOS / SIN DEPLOY**
- Modo certificado preservado: PRODUCTION_PAPER / EXECUTION=SIMULATED
- Órdenes reales: bloqueadas; no se ejecutó ninguna operación.

## Cambios validados
- PAPER_FIXED_PERCENT_V1: stop 2% / objetivo 5%; replay ATR no comparable/no promovible.
- latest_quote filtra symbol, asset_class, settlement, currency y market.
- DailyRisk persiste SYSTEM_ERROR ante sqlite3.Error y devuelve DAILY_RISK_SYSTEM_ERROR.
- IOL sigue desactivado por defecto; estimar_operacion() requiere IOL_COST_ESTIMATE_EXPLICIT_OPT_IN=true antes de cualquier POST. El verificador estándar lo informa como OMITIDA.
- El opt-in IOL quedó clasificado como variable crítica que requiere confirmación.
- F-05 no se modificó: no se inventaron versiones ni se generó un lock Python 3.12 para un runtime Docker Python 3.11.

## Pruebas
- F-01: regresiones PaperBroker, take-profit, supervisor, backtest e integración PAPER: GREEN.
- F-02: suite focalizada de aislamiento de ledger/supervisor/producción: 223 passed, código 0.
- F-03: suite de riesgo/outbox + aislamiento + producción: GREEN, código 0.
- F-04: 11 pruebas focalizadas y compilación: GREEN.
- No se hicieron llamadas de órdenes, cambios de credenciales ni cambios en el host.

## Próximo paso
Resolver F-05 únicamente con entorno limpio Python 3.11/Docker, lock con hashes y suite completa en la imagen exacta. Luego abordar F-06/F-07/F-08/F-10. Actualizar este checkpoint después de cada bloque validado y revisar antes de cualquier promoción o despliegue.

Nota: no se elimina ninguna rama, código ni evidencia sin especificar exactamente el objeto a eliminar.
