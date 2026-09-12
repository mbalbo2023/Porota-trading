# POROTA TRADING RC6 — CHECKPOINT F01/F02/F03
Fecha: 2026-09-12
Rama: fix/rc6-f01-exit-model-parity-20260912
Commit actual previo a este checkpoint: a7de0461b5b1871c76b3c22ace1a5cfbf239575c

## Estado
- F-01 (paridad backtest/PAPER): **CORREGIDO**
- F-02 (identidad completa en latest_quote): **CORREGIDO**
- F-03 (SQLite en DailyRisk): **CORREGIDO**
- Runtime productivo: **SIN CAMBIOS / SIN DEPLOY**
- Modo certificado preservado: PRODUCTION_PAPER / EXECUTION=SIMULATED
- Órdenes reales: bloqueadas; no se ejecutó ninguna operación.
- F-04 en adelante: pendientes; no se modificaron.

## Cambios validados
- Modelo canónico PAPER_FIXED_PERCENT_V1; PAPER y backtest legado comparten stop 2% / objetivo 5%.
- Replays ATR quedan explícitamente como candidatos no comparables/no promovibles.
- latest_quote filtra symbol, asset_class, settlement, currency y market.
- DailyRisk persiste SYSTEM_ERROR ante sqlite3.Error y admission_error devuelve DAILY_RISK_SYSTEM_ERROR.
- Se agregaron regresiones para identidad monetaria y error SQLite.

## Pruebas
- F-01: regresiones PaperBroker, take-profit, supervisor, backtest e integración PAPER: GREEN.
- F-02: suite focalizada de aislamiento de ledger/supervisor/producción: 223 passed, código 0.
- F-03: suite de riesgo/outbox + aislamiento + producción: GREEN, código 0.
- Compilación de módulos modificados: GREEN.
- No se hicieron llamadas de órdenes, cambios de credenciales ni cambios en el host.

## Próximo paso
Auditar y corregir F-04 en rama separada o continuación controlada, actualizar este checkpoint después de cada bloque validado y revisar antes de cualquier promoción o despliegue.

Nota: no se elimina ninguna rama, código ni evidencia sin especificar exactamente el objeto a eliminar.
