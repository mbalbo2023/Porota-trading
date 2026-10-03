# HANDOFF WS_PERF_01 — cierre de tranche independiente

WORKSTREAM_ID: WS_PERF_01_RC6_20261003.
BRANCH: perf/ws-perf-01-rc6-20261003.
BASE_SHA: da697c6e6c2274579f9e4a112fabc4327475dd35.
HEAD_SHA (código candidato exacto): ee1b22996907f282f77d3c31956104bd52cba858.
TREE_SHA: eba53b71d92a86daa4dd3360fdf72cb4605cf023.
PR: https://github.com/mbalbo2023/Porota-trading/pull/454 — abierto, draft, sin merge.
Estado del código: ARTEFACTO_VALIDADO.
Estado runtime de este código: NO_VERIFICADO; no se desplegó.
WRITE_OWNER: RELEASED al cierre registrado en issue #452.
DEPLOY_OWNER: NOT_ACQUIRED.
Rama de evidencia final: docs/ws-perf-01-closure-20261003.
Esa rama conserva este cierre sin cambiar el HEAD candidato probado; no es otro candidato de deploy.

## Validación exacta

Predeploy V2: https://github.com/mbalbo2023/Porota-trading/actions/runs/37140546207.
Job: 111253960213. Conclusion: success, completado 2026-10-03T17:32:39Z.
Suite automática desde raíz: 2543 descubiertos / 2543 ejecutados;
0 fallos, 0 errores, 0 skipped, 0 xfail.
50 casos nuevos focales también GREEN localmente; compile y secret scan GREEN.
Una exclusión preexistente gobernada: test_a3_primary_readonly_hf6.py de raíz,
duplicado supersedido por tests/test_a3_primary_readonly_hf6.py; no se añadieron exclusiones.

Todos los gates del workflow pasaron: políticas, fixtures negativos, suite general,
secretos, procedencia, dependencias, manifest/policy, imagen construida una vez,
integridad/import closure (0 faltantes), imports, contrato runtime en imagen,
seguridad PAPER y contrato frozen. Smoke en runner no equivale a runtime productivo.

Artifact: 11280311699, porota-predeploy-v2-ee1b22996907f282f77d3c31956104bd52cba858.
Digest: sha256:e8b1699066b57a3e292d5f55fb9b061b9ba040650c4411060f2ad8ee8704effb.
Image ID: sha256:a3638c60319a0b8dede0dde564a162c85c423d211f8dd785d5ac9fe157f7d6b9.
Bundle SHA256: caec03c441e1213ee0cb45dc530f8c76e57f121339c6affa9d2652486c8a9bf1.
Vence 2026-10-10T17:32:23Z; un deploy futuro requiere evidencia canónica vigente.
Los runs anteriores de este PR fueron supersedidos por synchronize; no se usan
para validar este SHA. No se cancelaron workflows ajenos.

## Archivos modificados

- AUDITORIA_IMPLEMENTACION_PERFORMANCE_RC6.md
- HANDOFF_WS_PERF_01.md
- PERFORMANCE_TRUTH_MATRIX_RC6.json
- bm_exit_supervisor.py
- bv_paper_runtime.py
- rc6_performance/__init__.py
- rc6_performance/capture.py
- rc6_performance/common.py
- rc6_performance/costs.py
- rc6_performance/counterfactuals.py
- rc6_performance/metrics.py
- rc6_performance/replay.py
- rc6_performance/report.py
- rc6_performance/scanner.py
- rc6_performance/shadow.py
- scripts/rc6_performance_audit.py
- scripts/rc6_performance_report.py
- tests/test_rc6_performance_capture.py
- tests/test_rc6_performance_costs.py
- tests/test_rc6_performance_report.py
- tests/test_rc6_performance_shadow.py

## Resultados

Auditoría reproducida: 26 hashes válidos, 20 ruedas, 68 posiciones, 151 fills,
10 posiciones con ventas parciales; cantidades exactas y dinero dentro de 0.01.
ARS: bruto -10954.5809, cargos 23445.66, neto -34400.2409, PF 0.1003815553.
USD_MEP: bruto -1.4439, cargos 2.64, neto -4.0839. Monedas separadas.
Drawdown diario realizado -35833.4232 ARS; por cierre individual -36367.7944.
14 targets y 30 perfiles negativos; 14 post-stops sin equilibrio neto observado.
Scanner histórico: rotación INFEASIBLE, revisita 118300 s, cota conservadora 54.
Funnel histórico parcial: 650 evaluaciones, 15 BUY, 11 BLOCKED, 4 OPENED,
aceptación 26.666667%; consultas no atómicas, no 650 oportunidades independientes.

Captura/reportes, costos canónicos, feasibility API, evaluadores SHADOW, replays
causales y telemetría de salida quedan probados como software. El collector
automatizado no duplica ingesta externa ni escribe/migra la DB de trading.
No cambia stops, targets, EOD, riesgo, freshness, depth o rutas reales.
PPI Watch intacto. No SSH, merge, rollback, deploy ni escritura productiva.

## Riesgos, bloqueos y NO_VERIFICADO

Cuota 128 MiB; inicio en tail; ventana runtime de 1000 eventos. Gaps y cuota
alcanzada son explícitos. Sin limpieza silenciosa ni historia completa ficticia.
Probes independientes tienen hasta 5 ms de espera SQLite cada uno; si fallan
registran drop y no vetan salida. Latencia real e IO posteriores NO_VERIFICADO.
Costos esperados/replays son sensibilidad PAPER; no tarifa individual certificada.
Break-even SHADOW queda armado pero un gap puede producir pérdida neta.
No hay paths de mercado completos para repetir de cero primer toque/depth.

BLOQUEADO: relojes nativos SIGNAL/DECISION, SHA/config completos por decisión,
gate factual del scanner y unificación total de fórmulas engine/observer.
Se preserva READ_ONLY por #453/WS-MOTOR-16 y reconciliación observer/dashboard.
Ownership y base se revalidaron al cierre: producto permanece en BASE_SHA y
no consta liberación del owner de engine/DailyRisk en tracker #446.
NO_VERIFICADO: edge, calibración OOS, ganancias de HOLD/rechazos, equity completa,
rentabilidad postdeploy e impacto monetario causal de SQLite.

## Trabajo posterior y ownership

Primero reconciliar scopes con los owners, luego native clocks/config hooks y
gate factual, sin duplicar #437/#440/#449/caucion. Congelar protocolo antes de
outcomes OOS; evaluadores consumen mismo snapshot, nunca features futuras.
Toda variante sigue SHADOW y no se promueve por grilla histórica o CI GREEN.
Deploy y validación de mercado futuros requieren una misión autorizada separada.

Se liberan los paths de #452 sin adquirir scopes ajenos. PR permanece draft
por las dependencias abiertas. Este handoff no declara completos B/C de la
misión integral mientras esos bloqueos subsistan.
