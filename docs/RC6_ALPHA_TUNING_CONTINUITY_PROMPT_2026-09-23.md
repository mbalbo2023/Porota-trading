CONTINUIDAD OBLIGATORIA — POROTA TRADING RC6 ALPHA TUNING

Quiero que continúes EXACTAMENTE desde la auditoría de performance/alpha de Porota Trading. NO reconstruyas desde cero, NO repitas trabajo terminado y NO inventes datos.

Lee primero:
1. docs/RC6_ALPHA_TUNING_CHECKPOINT_2026-09-23.md
2. docs/RC6_ALPHA_TUNING_AUDIT_PROPOSAL_2026-09-23.md
3. el HEAD actual de la rama audit/rc6-alpha-tuning-analysis-20260923
4. los últimos runs del workflow RC6 Alpha Tuning Readonly Trade Audit

REPOSITORIO:
- mbalbo2023/Porota-trading
- rama canónica de deploy: deploy/rc6-pr69-isolated-20260915
- NO usar main como canónica.
- SHA canónico base verificado al iniciar la auditoría: a5b5d14aa2bd88056ec8e0350464546251a8ad4d
- rama de auditoría: audit/rc6-alpha-tuning-analysis-20260923

REGLA CRÍTICA:
Esta fase es 100% PAPER / SHADOW / READ-ONLY.
No hacer deploy.
No reiniciar Porota.
No cambiar estrategia factual.
No cambiar threshold, stop, target, spread, sizing, riesgo ni gates.
No tocar PPI Watch.
No enviar órdenes.
SQLite siempre mode=ro + PRAGMA query_only=ON.
Todo run válido debe verificar PRODUCTION_PAPER|0 antes y después.

EVIDENCIA YA CONFIRMADA:
- 82 trades PAPER cerrados.
- ARS: 75, 9 wins, 66 losses, -52135.2627 ARS.
- USD_MEP: 7, 0 wins, 7 losses, -4.8069.
- MFE/MAE 82/82.
- baseline factual: 53,750 decisiones históricas, 16,532 replayables con features persistidas, 100% action match, 100% score match, 0 mismatches.
- score AUC = 0.3637747336.
- score >=0.70: 10 trades, 0 wins.
- ARS gross PnL = -16423.6727; costs = 35711.59; net = -52135.2627.
- 18 trades fueron gross-positive pero net-negative.
- 0 trades alcanzaron MFE +5%.
- mejor gross target aislado probado: +0.75%, mejora ARS ~+6136 pero deja ~-45999; no arregla el motor.
- 16 STOP trades seguidos 120m: sólo 1 volvió a entry; 0 recuperaron breakeven neto; 0 llegaron +1% o +2%. No ampliar stop por intuición.
- filtros simples de momentum/EMA/RSI point-in-time no generaron cohortes positivas robustas.
- book imbalance, breadth positiva y same-day return positivo tampoco generaron cohortes positivas robustas.
- el problema principal es alpha/entrada; costos agravan una expectativa bruta ya negativa.

ESTADO DE WORKFLOWS AL CHECKPOINT:
- run 20 35945917444 SUCCESS GREEN.
- run 21 35946098360 SUCCESS GREEN.
- run 25 35946456546 estaba ejecutándose desde SHA 26b42510d4993fccec99461a1dfe41ce7242f9e3.
Primera acción: consultar run 25. Sólo usar sus resultados si termina SUCCESS y POST_SAFETY=PRODUCTION_PAPER|0.

SIGUIENTE PASO EXACTO:
1. recoger resultados cost-aware NET_TARGET +0.25%, +0.50%, +0.75%, +1.00%.
2. recoger resultado point-in-time de market breadth.
3. después estudiar explícitamente hipótesis “avoid chase / mean reversion” usando sólo features point-in-time y con split temporal/walk-forward.
4. después combinar candidato de entrada + salida en SHADOW, sin portfolio look-ahead.
5. NO proponer implementación factual hasta encontrar una mejora positiva y estable fuera de muestra.
6. si ningún perfil logra edge, decirlo claramente y diseñar nueva señal desde cero en SHADOW en lugar de tunear parámetros viejos.

HIGIENE:
- revisar HEAD real antes de escribir.
- no duplicar scripts/workflows.
- eliminar o no usar analizadores superseded.
- cancelled != evidence.
- conservar sólo resultados SUCCESS.
- no mezclar ARS con USD_MEP.
- no inferir contexto histórico no persistido.
- toda ausencia debe quedar INSUFFICIENT_EVIDENCE/NO_MEDIDO.

Si la UI muestra mensajes de “comprobaciones/chequeos”, eso corresponde a GitHub Actions de auditoría. Verificar seguridad y continuar; no asumir que son órdenes reales.

Continúa desde este punto sin pedirme que repita contexto.
