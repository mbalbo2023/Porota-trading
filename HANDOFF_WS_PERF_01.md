# HANDOFF WS_PERF_01 — continuación T2

WORKSTREAM_ID: WS_PERF_01_RC6_20261003_T2.
BRANCH: perf/ws-perf-02-lineage-scanner-20261003.
BASE_SHA: da697c6e6c2274579f9e4a112fabc4327475dd35.
DEPENDENCY_OWN_FROZEN_HEAD: ee1b22996907f282f77d3c31956104bd52cba858 (#454).
MODE: WRITE_OWNER. DEPLOY_OWNER: NOT_ACQUIRED.
Estado al publicar: DESARROLLADO; Predeploy exacto pendiente.
La identidad HEAD/PR/run/artifact posterior está en el cierre de #452.

Se retoma el trabajo bloqueado por una reserva anterior del archivo completo.
Ahora se limita a componentes spot de evidencia/costos, planner de scanner y
rc6_performance/tests/docs propios. #453 conserva FUTUROS binding/admisión,
caja/lifecycle y DailyRisk. Prueba offline contra su HEAD d738db79a452c699b50aed00ac539e57e1cc3b04:
sin conflictos, AST de seis métodos FUTUROS y admission_error/_cash/mark_equity
idéntico. No se publica esa integración ni se altera su PR.

Los cambios agregan clocks nativos SIGNAL/DECISION/INTENT/entry fill confirmado,
strategy_id, fingerprint efectivo y SHA verificado contra attestations
canónicas completas de fuentes Python. Valores ausentes siguen desconocidos.
El funnel incluye rechazos previos a la señal, códigos de gate nativos y raw
reason heredado; las observaciones cold no son oportunidades BUY.

La canasta warm dura una ventana, persiste en metrics existentes y tiene
capacidad matemática compatible. Cold usa hasta un slot del mismo límite y no
autoriza entradas. Catálogo íntegro, abiertas primero, cierres antes del gate.
La cota no reemplaza muestras distintas reales, freshness, profundidad y riesgo.
Costos spot se delegan a funciones comunes conservando centavos/bonificaciones
y todos los parámetros existentes. FUTUROS especializado queda excluido.

285 pruebas locales pasan; 23 nuevos casos en test_rc6_performance_runtime.py.
Nuevo Predeploy V2 completo requerido sobre HEAD exacto. #454 queda congelado
con su GREEN anterior; esta tanda lo incorpora como dependencia propia y es
el candidato actualizado para una futura integración consolidada.

No merge/deploy, SSH, modificaciones productivas, nuevas rutas ni cambios en
PPI Watch. Rentabilidad, modelo empírico fuera de muestra, latencias efectivas
y validación runtime de T2: NO_VERIFICADO. Calibración/alpha no se infieren de
20 ruedas ni de tests GREEN. Guardar informes detallados en artifacts privados.
Liberar WRITE_OWNER tras GREEN y cierre durable en #452 / #446.
