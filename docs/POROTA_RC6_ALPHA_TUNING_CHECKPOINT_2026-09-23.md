# POROTA TRADING RC6 — CHECKPOINT ALPHA TUNING / HANDOFF

Fecha de corte: 2026-09-23 ~23:15 ART / 2026-09-24 ~02:15 UTC

## REGLA DE CONTINUIDAD
Continuar desde este punto. NO reconstruir desde main. NO repetir auditorías ya verificadas. Distinguir siempre CONFIRMADO / EN CURSO / PENDIENTE.

## Rama canónica vigente
- Rama canónica de Porota RC6: `deploy/rc6-pr69-isolated-20260915`
- SHA canónico base usado para esta auditoría: `a5b5d14aa2bd88056ec8e0350464546251a8ad4d`
- `main` NO es la rama canónica de deploy y no debe usarse como base.
- No tocar PPI Watch.
- No usar Codespaces.

## Rama aislada de auditoría
- `audit/rc6-alpha-tuning-analysis-20260923`
- Base: SHA canónico `a5b5d14aa2bd88056ec8e0350464546251a8ad4d`
- Último SHA de código analítico previo a este handoff: `3948772cb6a83825db98f8334c226fc7e1eedab2`
- Esta rama NO modifica el motor factual, NO despliega, NO cambia parámetros, NO envía órdenes.
- Workflow: `.github/workflows/rc6-alpha-tuning-readonly-audit-20260923.yml`
- Safety obligatoria antes/después de cada run: `PRODUCTION_PAPER|0`.

## Qué significan los avisos de “comprobaciones”
Son GitHub Actions de la rama de auditoría. Ejecutan tests y consultas SQLite read-only contra `observer_v17.db`.
No son deploys. No reinician Porota. No envían órdenes. No cambian el ledger.

## Evidencia histórica CONFIRMADA
Ledger PAPER acumulado:
- 82 cierres PAPER totales.
- ARS: 75 trades, 9 wins, 66 losses, win rate 12.0%, PnL neto -52135.2627 ARS.
- USD_MEP: 7 trades, 0 wins, 7 losses, PnL neto -4.8069 USD_MEP.
- Conteo combinado: 82 trades, 9 wins, 73 losses, win rate aproximado 10.98%.
- NO sumar monetariamente ARS y USD_MEP.

Fricción:
- ARS gross PnL antes de costos: -16423.6727 ARS.
- Costos ARS acumulados: 35711.59 ARS.
- ARS neto: -52135.2627 ARS.
- 25 trades ARS tuvieron gross PnL positivo.
- 16 de esos gross-positive terminaron net-negative por fricción/costos.

Integridad:
- 82/82 CLOSED reconciliados.
- duplicate_paper_ids = 0.
- faltantes BUY fills = 0.
- faltantes SELL fills = 0.
- MFE/MAE medible = 82/82.
- Immutable decision evidence verificado sólo en 13 trades nuevos; no atribuir IOL/contexto retrospectivamente a los anteriores.

## Baseline factual CONFIRMADO
Run exitoso de replay factual:
- GitHub Actions run #12 / id `35944902350`.
- decisions_total = 53750.
- replayable = 16532.
- action_match_rate = 1.0.
- score_match_rate = 1.0.
- mismatches = 0.

Densidad de BUY si sólo se subiera threshold sobre esas 16532 decisiones:
- 0.62 -> 180 BUY
- 0.63 -> 145
- 0.64 -> 102
- 0.65 -> 70
- 0.66 -> 58
- 0.67 -> 44
- 0.68 -> 39
- 0.69 -> 33
- 0.70 -> 26
- 0.72 -> 19
- 0.74 -> 15
No interpretar esto como mejora de PnL; es sólo densidad de candidatos.

## Diagnóstico de score CONFIRMADO
- AUC factual score (higher-is-better) = 0.3637747336377473.
- score vs net PnL correlation ≈ -0.16042.
- Score >= 0.70: 10 operaciones realizadas, 0 wins.
Conclusión provisional fuerte: subir el threshold NO arregla el alpha; scores más altos no discriminaron ganadores.

## Stops / post-exit CONFIRMADO
- 16 STOP_PAPER analizados.
- 16/16 con seguimiento 120m.
- recovered_to_entry_30m = 0.
- recovered_to_entry_60m = 0.
- recovered_to_entry_120m = 1.
- recovered_net_breakeven_120m = 0.
- reached_plus_1pct_120m = 0.
- reached_plus_2pct_120m = 0.
- median_max_return_120m = -0.017211629468...
Conclusión provisional fuerte: ampliar el stop no está respaldado por esta muestra; los stops no mostraron recuperación útil posterior.

## Target factual / MFE CONFIRMADO
- Ningún trade alcanzó +5% executable MFE durante su vida.
- Target factual +5% no fue observado como alcanzable en los 75 ARS.
- Lower-target counterfactual riguroso, respetando prioridad de salida factual y profundidad completa, run #15 id `35945441839`:
  - +0.50%: delta ARS +4131.7536 -> neto -48003.5091
  - +0.75%: delta ARS +6136.2173 -> neto -45999.0454
  - +1.00%: delta ARS +4299.6444 -> neto -47835.6183
  - +1.25%: delta ARS +4726.8277 -> neto -47408.4350
  - +1.50%: delta ARS +3087.4976 -> neto -49047.7651
  - +2.00%: delta ARS +825.3036 -> neto -51309.9591
  - +2.50%: delta ARS +1085.1912 -> neto -51050.0715
  - +3.00%: delta ARS +29.7640 -> neto -52105.4987
  - +3.50%: sin cambios
  - +5.00%: sin cambios
Conclusión: bajar target mejora, pero ni el mejor +0.75% convierte la estrategia en positiva. Es una mejora de exit, no solución del alpha.

## Exit-grid preliminar
Run #13 id `35945325970` fue exitoso y evaluó 76 trades con fee rates persistidas.
Sirve como orientación pero NO es evidencia final para promoción porque el primer grid no replicaba toda la semántica de liquidez/precedencia del supervisor. No usarlo para cambiar parámetros sin un replay endurecido.

## Velas point-in-time CONFIRMADO
Run consolidado #20 id `35945917444` terminó SUCCESS y GREEN.
Safety PRE/POST = `PRODUCTION_PAPER|0`.
Coverage:
- closed_total = 82
- candle21 = 79
- daily20 = 9
- daily50 = 9

Los filtros clásicos probados NO solucionan el alpha:
- CANDLE_MOMENTUM_3V15_POS: kept 39, 3 wins, 36 losses.
- EMA9_GT_EMA21_5M: kept 35, 3 wins, 32 losses.
- RSI14_35_65_5M: kept 57, 6 wins, 51 losses.
- RSI14_RISING_LT70_5M: kept 57, 6 wins, 51 losses.
- MOM_POS_AND_EMA_UP: kept 33, 3 wins, 30 losses.
- EMA_UP_AND_RSI_RISING: kept 21, 1 win, 20 losses.
- Daily trend tiene sólo 9 trades de cobertura: muestra insuficiente para conclusiones.

Conclusión: no promover momentum/EMA/RSI simples como BINDING. El alpha factual parece perseguir impulso y los filtros clásicos de continuación no han separado adecuadamente winners/losers.

## Código de auditoría ya presente
- `rc6_trade_master_audit.py`
- `rc6_baseline_replay_audit.py`
- `rc6_alpha_tuning_analysis.py`
- `rc6_trade_cohort_analysis.py`
- `rc6_exit_target_counterfactual.py`
- `rc6_exit_grid_replay.py`
- `rc6_point_in_time_entry_features.py`
- `rc6_entry_context_audit.py`
- `rc6_entry_breadth_audit.py`
- tests correspondientes.
- Se eliminó el analizador de velas duplicado/superseded y se consolidó el workflow.

## Entry context CONFIRMADO
Run #21 id `35946098360` terminó SUCCESS.
- PRE_SAFETY = PRODUCTION_PAPER|0.
- POST_SAFETY = PRODUCTION_PAPER|0.
- RC6_ALPHA_TUNING_READONLY_AUDIT = GREEN.
- coverage = 82/82 para fresh_book_120s, book_imbalance, breadth y asset_day_return.

Filtros evaluados sobre trades factual-opened:
- BOOK_IMBALANCE_POS: kept 27, 3 wins, 24 losses; ARS kept net -19936.1986.
- BREADTH_POS: kept 43, 3 wins, 40 losses; ARS kept net -32040.6780.
- BREADTH_NONNEG: kept 44, 4 wins, 40 losses; ARS kept net -31471.1322.
- ASSET_DAY_RETURN_POS: kept 51, 4 wins, 47 losses; ARS kept net -38551.7219.
- BREADTH_POS_AND_ASSET_POS: kept 39, 3 wins, 36 losses; ARS kept net -30929.2279.

Conclusión: book imbalance positivo, breadth positiva y retorno intradiario positivo NO rescatan el alpha; tampoco deben promoverse a BINDING.

## Estudios EN CURSO / PENDIENTES al momento del handoff
1. `rc6_entry_breadth_audit.py`
   - first-vs-last observed trade breadth, no es un índice.
   - threshold bearish de referencia 70%, ALERT_ONLY.
   - requiere run exitoso completo antes de conclusiones.

3. Cost-aware net target counterfactuals añadidos en SHA `3948772...`.
   - run #23 id `35946419504` fue CANCELLED.
   - NO considerar ese run como evidencia.
   - Si hace falta, re-ejecutar después de que no haya otro workflow en curso, siempre read-only.

## Próximo paso exacto
A. El run #21 ya está cerrado SUCCESS + GREEN. No repetirlo.

B. Ejecutar una única corrida consolidada de HEAD actual sólo si hace falta cubrir breadth + cost-aware targets que todavía no tengan run exitoso.
- No disparar múltiples runs en paralelo.
- No hacer deploy.
- No modificar strategy/env.

C. Construir el experimento SHADOW de “no perseguir precio / mean-reversion control” con evidencia point-in-time:
- score factual bajo vs alto;
- asset day return;
- breadth;
- book imbalance;
- candle momentum;
- spreads/costos.
Debe ser diagnóstico, no una nueva estrategia factual.
Separar train/validation temporal.

D. Combinar el mejor filtro de ENTRADA sólo si es estable out-of-sample con el target neto corto candidato, sin auto-promoción.
- El mejor target aislado observado hasta ahora fue +0.75%, pero sigue con PnL negativo y NO debe implementarse todavía.

E. Antes de proponer implementación:
- walk-forward por jornadas;
- incorporar costos y precios ejecutables;
- no look-ahead;
- medir trades eliminados y winners sacrificados;
- comparar contra baseline;
- exigir estabilidad temporal;
- PAPER/SHADOW primero.

## Guardrails NO NEGOCIABLES
- PPI manda; IOL sólo complementa hasta promoción explícita.
- IA intradía OFF.
- Nunca dinero real.
- No cambiar `PAPER_SCORE_THRESHOLD`, stop, target, spread, sizing, risk ni economics_mode desde esta rama.
- No promover IOL/economic gate/market breadth automáticamente.
- No tocar PPI Watch.
- SQLite siempre `mode=ro` + `PRAGMA query_only=ON`.
- Antes y después de cualquier Action remota: `mode=PRODUCTION_PAPER`, `real_orders_sent=0`.
- No inventar datos faltantes: usar NO_MEDIDO / INSUFFICIENT_EVIDENCE.
- No mezclar monedas.
- No usar resultados de run CANCELLED.
- No tomar una asociación de cohorte como causalidad.
- No crear PR contra main. Cualquier PR futuro debe ir contra la rama canónica vigente.
- Ningún cambio de estrategia se implementa sin nueva rama/PR separado y autorización explícita.

## Workflow / GitHub / deploy
- Rama main protegida pero no canónica.
- Autorización persistente para operar GitHub en nombre del usuario; no pedirla de nuevo.
- Preferir GitHub Actions.
- Deploy sólo cuando se ordene.
- Branch aislada -> PR estrecho contra rama canónica -> tests -> revisar diff -> merge -> workflow canónico -> verificar Droplet.
- Después de deploy: limpieza Docker/caché/residuos y recuperación de espacio, sin borrar datos persistentes.

## Objetivo final de esta auditoría
Entregar una propuesta de tuning del motor basada en evidencia de las 82 operaciones y todas las decisiones históricas, explicando:
- por qué pierde;
- qué parte es señal, qué parte es exit y qué parte son costos;
- qué señales discriminan winners/losers;
- qué cambios sobreviven walk-forward;
- qué debería ir primero a SHADOW;
- qué NO debe implementarse.
