# HANDOFF WS_PERF_01

WORKSTREAM_ID: WS_PERF_01_RC6_20261003.
BRANCH: perf/ws-perf-01-rc6-20261003.
BASE_SHA: da697c6e6c2274579f9e4a112fabc4327475dd35.
MODE: WRITE_OWNER. DEPLOY_OWNER: NOT_ACQUIRED.
Estado de este corte: DESARROLLADO; publicación y Predeploy exacto pendientes.
HEAD_SHA y PR: el registro de cierre en issue #452/PR contiene la identidad
exacta posterior a publicar; este archivo no pretende conocer su propio SHA.

## Archivos

- rc6_performance/: cantidades/relojes/provenance, costos, scanner, SHADOW,
  replay, métricas, captura incremental y reportes.
- scripts/rc6_performance_audit.py y scripts/rc6_performance_report.py.
- tests/test_rc6_performance_*.py.
- bm_exit_supervisor.py: probes opcionales sin autoridad de ejecución.
- bv_paper_runtime.py: worker aislado y wiring de probes.
- AUDITORIA_IMPLEMENTACION_PERFORMANCE_RC6.md y PERFORMANCE_TRUTH_MATRIX_RC6.json.

No cambios en engine/observer/DailyRisk, PPI Watch, tarifas, parámetros,
dashboard, contratos, DB productiva, systemd o workflows de deploy.

## Validación

Auditores adjuntos ejecutados offline; 26 hashes válidos; 20/68/151 reproducidos
con conciliación exacta de cantidades y tolerancia monetaria de un centavo.
El auditor canónico nuevo reproduce monedas, bruto/costos/neto, PF, acierto,
expectancy, MFE/MAE y cohortes. Dos cadencias de drawdown se distinguen.
Tests focales y full Predeploy V2: ver evidencia final de #452/PR; no se usa
un run de otro SHA como validación de este HEAD.

## Riesgos y pendientes

Cuota de evidencia 128 MiB; capture corta y expone gaps, no limpia silenciosamente.
Reporte runtime usa ventana acotada de 1000 eventos, no toda la vida del bot.
Dos probes de inicio añaden writes independientes de hasta 5 ms de espera cada
uno. Su pérdida se registra; nunca acredita salida o intención inexistente.
El worker usa baja prioridad y no crea llamadas externas. Costos esperados y
replays son modelos de sensibilidad; los fills reconciliados son el factual.

Faltan relojes exactos SIGNAL/DECISION y SHA/config completos en campos heredados;
no se sustituyen con timestamps de recepción. Integración factual del scanner
y unificación total de costos están BLOQUEADAS por ownership/reconciliación.
Rentabilidad, calibración fuera de muestra y latencia real posterior de este
código permanecen NO_VERIFICADO porque no se hace deploy en esta misión.

Siguiente trabajo independiente: revisar artifacts/reportes y preparar protocolo
fuera de muestra. Siguiente trabajo compartido: reconciliar hooks/gates con
WS-MOTOR-16 y observer, después de liberar sus scopes. No promover parámetros
retrospectivos. No merge/deploy desde este handoff.

Liberación final de WRITE_OWNER: registrar tras Predeploy GREEN en #452.
