CONTINUIDAD OBLIGATORIA — POROTA TRADING RC6 ALPHA TUNING

Lee primero en el repositorio:
1. docs/POROTA_RC6_ALPHA_TUNING_CHECKPOINT_2026-09-23.md
2. docs/RC6_ALPHA_TUNING_AUDIT_PROPOSAL_2026-09-23.md

Después corrobora el HEAD actual de:
- rama canónica: deploy/rc6-pr69-isolated-20260915
- rama de auditoría: audit/rc6-alpha-tuning-analysis-20260923

NO uses main como rama canónica. NO reconstruyas el trabajo desde cero. NO repitas auditorías cerradas salvo que el checkpoint marque expresamente que un run fue cancelado/incompleto.

OBJETIVO:
Continuar la auditoría exhaustiva del motor PAPER de Porota Trading, explicar por qué la mayoría de las operaciones pierde y definir tuning respaldado por evidencia, sin cambiar todavía el motor factual.

ESTADO VALIDADO QUE DEBES TOMAR COMO PUNTO DE PARTIDA:
- 82 cierres PAPER reconciliados.
- ARS: 75 trades, 9 wins, 66 losses, -52135.2627 ARS.
- USD_MEP: 7 trades, 0 wins, 7 losses, -4.8069 USD_MEP.
- 82/82 MFE/MAE medibles.
- Baseline factual reproducido sobre 16532 decisiones: 100% action match, 100% score match, 0 mismatches.
- Score AUC = 0.3637747336377473; score alto no discrimina winners.
- Score >=0.70 en trades realizados: 0 wins en 10.
- ARS gross PnL = -16423.6727; costos = 35711.59; neto = -52135.2627.
- 16 gross-positive terminaron net-negative por costos.
- 16 STOP_PAPER: ninguno recuperó net breakeven dentro de 120m; sólo 1 volvió a entry; ninguno alcanzó +1%/+2%.
- Target +5% nunca fue alcanzado por MFE ejecutable.
- Lower target +0.75% fue el mejor aislamiento probado: mejora ARS +6136.2173, pero sigue neto -45999.0454. NO implementar todavía.
- Filtros simples momentum/EMA/RSI point-in-time siguen negativos; no promoverlos.

CONFIRMADO ADICIONAL:
- Run #21 id 35946098360 terminó SUCCESS + GREEN, PRE/POST safety PRODUCTION_PAPER|0.
- Entry context coverage 82/82.
- BOOK_IMBALANCE_POS: kept 27, 3 wins, 24 losses.
- BREADTH_POS: kept 43, 3 wins, 40 losses.
- ASSET_DAY_RETURN_POS: kept 51, 4 wins, 47 losses.
- BREADTH_POS_AND_ASSET_POS: kept 39, 3 wins, 36 losses.
- Ninguno de estos filtros vuelve positiva la estrategia.

EN CURSO:
- Breadth audit separado y cost-aware net-target fueron agregados después; cualquier run CANCELLED no cuenta.
- Si hace falta una nueva ejecución, hacer UNA sola corrida consolidada del HEAD, no varias.

PRÓXIMO ANÁLISIS:
1. Extraer contexto point-in-time de book imbalance, breadth y asset-day-return.
2. Evaluar explícitamente “no perseguir precio / mean-reversion control” frente al momentum factual.
3. Hacer split temporal / walk-forward; no optimizar y evaluar en los mismos días.
4. Cruzar entrada candidata con target neto corto y costos, sin auto-promoción.
5. Concluir qué cambio mínimo debería pasar a SHADOW; no BINDING hasta evidencia posterior y autorización explícita.

REGLAS DE SEGURIDAD:
- rama canónica = deploy/rc6-pr69-isolated-20260915, salvo que un checkpoint posterior demuestre un cambio canónico.
- rama de auditoría = audit/rc6-alpha-tuning-analysis-20260923.
- PPI manda; IOL complementa.
- IA intradía OFF.
- Nunca real money.
- No tocar PPI Watch.
- No cambiar env/strategy/threshold/stop/target/spread/sizing/risk desde la rama de auditoría.
- SQLite read-only: mode=ro + PRAGMA query_only=ON.
- Verificar PRODUCTION_PAPER|0 antes y después.
- No inventar datos.
- No sumar monedas.
- No usar runs cancelados.
- No afirmar causalidad por cohortes.
- No deploy sin orden explícita.
- Si más adelante hay deploy: PR contra rama canónica, no main, y limpiar residuos Docker después.

FORMA DE TRABAJO:
- Continuidad primero.
- Semáforo de completado / en curso / pendiente.
- Mostrar evidencia exacta y run/SHA.
- No hacer preguntas innecesarias.
- Si un test falla, corregir en la rama aislada; distinguir fallo del analizador de fallo de Porota.
- Un solo workflow de auditoría a la vez para evitar el mensaje repetido de comprobaciones y cancelaciones por concurrencia.
- No dejar scripts duplicados/superseded.

RESULTADO ESPERADO:
Una propuesta técnica concreta, priorizada y respaldada por replay/walk-forward, indicando qué implementar primero en SHADOW, qué mantener sin cambios y qué descartar.
