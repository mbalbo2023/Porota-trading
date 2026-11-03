# CHECKPOINT 01 — WS-HISTORY-08 — runtime evidence 2026-10-01

## Estado
- mode: WRITE_OWNER
- branch: work/ws-history-08-oct01-coverage-rotation-20260930
- base original: 94b31b3a362f8d0802c1c5f800762c6d024deae1
- no deploy.
- PAPER/SHADOW ONLY; real_orders_sent=0; PPI Watch untouched.

## Históricos
- History canonical total observado: 439.833 filas.
- Última fecha canónica observada: 2026-09-28.
- Repair cutoff 2026-09-21: reanudado bajo lock; evidencia durante la corrida muestra progreso, no pérdida.
- Snapshot intermedio de repair: targets=1.094, complete previo=910, failed previo=184; durante reanudación aparecen estados ALREADY_COVERED / ARCHIVE_PARTIAL_COVERAGE / BLOCKED_NO_CANONICAL_BASELINE / COMPLETE / NO_NEW_VALID_ROWS.
- No lanzar un segundo repair en paralelo.

## Velas — VALIDADO_RUNTIME
Run: 36797940329 GREEN.
- candle_series: 3.456.
- candle_versions: 95.015.
- candle_samples: 111.092.
- candle_rejections: 108.224.
- última muestra: 2026-09-30T19:58:36Z.
- última recepción: 2026-09-30T19:59:10Z.
- última barra cerrada: 2026-09-30T20:00:00Z = 17:00 ART.
- candle_worker_state: RUNNING, heartbeat vivo.
Conclusión: las velas NO están detenidas; la fecha vieja visible en Dashboard proviene de mezclar histórico diario/freshness con velas intradiarias.

## Rotación — decisión
- PAPER_ACTIVE_SYMBOL_LIMIT observado: 20.
- foco efectivo: 8; foco factible (~38 muestras/ventana de 90m).
- pool rotativo: ~6.945.
- slots rotativos con límite 20: 12.
- vueltas estimadas: 579.
- rotation_feasible=false.
- métricas de ciclo recomiendan típicamente 10–15, aun con límite configurado 20.
Decisión: NO aumentar 20 arbitrariamente. Más símbolos por ciclo degrada latencia y no vuelve factible cubrir ~6.945 con 6 muestras/90m.
La cobertura amplia debe seguir como histórico/background/shadow de menor frecuencia; el foco intradiario conserva freshness.

## Código
- historical_candle_shadow_rc6.py alineado a OPERATIONAL_HISTORY_FAMILIES, siempre OBSERVE_ONLY.
- repair workflow conserva artifact/evidencia aun ante timeout/RC no cero.
- regresión history/candle: run 36797940329 GREEN.

## Pendiente para integración
- El Dashboard Históricos debe separar visualmente:
  1. histórico diario/canónico y su freshness;
  2. velas intradiarias, con última barra/known_at reales;
  3. factibilidad de foco vs rotación global.
