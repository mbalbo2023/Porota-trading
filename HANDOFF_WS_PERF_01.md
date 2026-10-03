# HANDOFF WS_PERF_01 — continuación T2

WORKSTREAM_ID: WS_PERF_01_RC6_20261003_T2.
BRANCH: perf/ws-perf-02-lineage-scanner-20261003.
BASE_SHA: da697c6e6c2274579f9e4a112fabc4327475dd35.
DEPENDENCY_OWN_FROZEN_HEAD: ee1b22996907f282f77d3c31956104bd52cba858 (#454).
MODE: READ_ONLY. WRITE_OWNER: RELEASED. DEPLOY_OWNER: NOT_ACQUIRED.
Estado final: ARTEFACTO_VALIDADO. Predeploy V2 exacto GREEN.
HEAD_SHA: b8c95c10459cb8ede2925da491730f5988b29561.
HEAD_TREE_SHA: f580e451d83eadc43575684378cb8f009d02366f.
PR: https://github.com/mbalbo2023/Porota-trading/pull/456.
PREDEPLOY_RUN: 37147635110, completado 2026-10-03T19:27:34Z.
ARTIFACT_ID: 11282743179.
ARTIFACT_DIGEST: sha256:2729fb737831704e43820ae03c274187c0868cb946e1ae0651e6522a7c241bd4.

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

285 pruebas locales pasan; 21 nuevos casos en test_rc6_performance_runtime.py.
Predeploy V2 GREEN: 2564 descubiertos/ejecutados; 0 fallos, errores, skips o xfail.
Una exclusión gobernada previa de duplicado, sin nuevas exclusiones.
436 archivos en el bundle; build-once e imagen exacta verificados.
Image: sha256:d4f63a24129e8c80a2fc0a28cdfbaf8e68ec8802906b454f3f33d617bf51fb5a.
Bundle SHA256: 4a03b159908f6628011e00f71cd3704946f4a3d5553c350ad58f7fbdff468188. #454 queda congelado
con su GREEN anterior; esta tanda lo incorpora como dependencia propia y es
el candidato actualizado para una futura integración consolidada.

No merge/deploy, SSH, modificaciones productivas, nuevas rutas ni cambios en
PPI Watch. Rentabilidad, modelo empírico fuera de muestra, latencias efectivas
y validación runtime de T2: NO_VERIFICADO. Calibración/alpha no se infieren de
20 ruedas ni de tests GREEN. Guardar informes detallados en artifacts privados.
WRITE_OWNER liberado tras GREEN y cierre durable en #452 / #446.
El candidato de #456 queda congelado; cambios posteriores requieren otra tanda.
Este cierre documental es una rama de evidencia separada, sin modificar el HEAD validado.
Los tres bloqueos de implementación spot/scanner están resueltos en código.
Validación runtime y evaluación económica fuera de muestra requieren un futuro deploy
y nuevas ruedas, fuera de la orden actual. FUTUROS especializado sigue con su owner.
