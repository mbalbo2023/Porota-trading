# RC6 Alpha Tuning — Propuesta de auditoría exhaustiva

**Fecha:** 2026-09-23/24 UTC  
**Rama de trabajo:** `audit/rc6-alpha-tuning-analysis-20260923`  
**Base exacta:** `deploy/rc6-pr69-isolated-20260915@a5b5d14aa2bd88056ec8e0350464546251a8ad4d`  
**Estado:** PROPUESTA / READ-ONLY FIRST / SIN CAMBIOS DE ESTRATEGIA  
**Objetivo:** explicar con evidencia por qué la operatoria PAPER acumulada tiene expectativa negativa, identificar qué componentes del motor factual aportan o destruyen edge y definir candidatos de tuning que sólo podrán proponerse después de replay y validación walk-forward.

---

## 0. Guardrails obligatorios

1. No usar `main` como rama canónica ni como base de despliegue.
2. No modificar ni desplegar el motor factual desde esta rama.
3. No modificar `PRODUCTION_PAPER`, `SIMULATED`, rutas de órdenes, PPI Watch ni datos persistentes.
4. No auto-promover ningún gate SHADOW a BINDING.
5. No cambiar stops, targets, threshold, ventana, sizing, riesgo, spread máximo, economía ni políticas hasta completar la auditoría.
6. Toda extracción del runtime debe ser SQLite `mode=ro` + `PRAGMA query_only=ON`, sin red y sin llamadas al broker.
7. PPI sigue siendo fuente primaria. IOL/GDELT/macro/históricos sólo se evalúan según la evidencia contemporánea realmente persistida.
8. No mezclar PnL de monedas diferentes en una sola cifra monetaria.
9. No reconstruir datos faltantes por inferencia; marcar `NO_MEDIDO` / `INSUFFICIENT_EVIDENCE`.
10. Cualquier implementación futura deberá seguir rama aislada → PR estrecho contra la rama canónica → tests → revisión de diff → merge → workflow canónico → evidencia de llegada al Droplet.

---

## 1. Evidencia cuantitativa confirmada antes de empezar

Fuente primaria actual: snapshots persistidos en la rama `runtime-observability`, generados por `ops_introspection_rc6.py`.

Último estado acumulado observado al cierre del 23-Sep:

| Moneda | Cierres acumulados | Wins | Losses | Win rate | PnL acumulado |
|---|---:|---:|---:|---:|---:|
| ARS | 75 | 9 | 66 | 12.0000% | -52,135.2627 ARS |
| USD_MEP | 7 | 0 | 7 | 0.0000% | -4.8069 USD_MEP |
| Conteo combinado | 82 | 9 | 73 | 10.9756% | NO SUMAR MONEDAS |

La muestra operativa acumulada verificable es por tanto **82 cierres PAPER**, no 20. El valor “60 y algo” coincide aproximadamente con las **66 pérdidas ARS** vigentes.

Serie diaria disponible en observabilidad:

| Día | Cerradas del día | PnL diario |
|---|---:|---:|
| 2026-09-01 | 9 | -8055.0685 ARS |
| 2026-09-02 | 6 | -1727.5463 (mixed ARS/USD_MEP; no interpretar como total multi-moneda) |
| 2026-09-09 | 7 | +1433.0525 |
| 2026-09-10 | 8 | -1995.9554 |
| 2026-09-11 | 6 | -6655.6167 |
| 2026-09-19 | 0 | 0 |
| 2026-09-21 | 3 | -1658.87 |
| 2026-09-22 | 5 | -1538.8201 |
| 2026-09-23 | 5 | -4308.473 |

Hay saltos acumulados entre snapshots diarios publicados. Eso demuestra que **no se debe reconstruir el histórico completo sumando estos archivos**. El ledger SQLite vigente es la autoridad para los 82 trades.

---

## 2. Hallazgo de arquitectura que debe probarse, no asumirse

En la rama canónica actual:

- `PaperBroker.threshold()` mantiene `score_threshold` congelado.
- La señal factual de `decide()` usa:
  - últimos negocios observados;
  - SMA de 3 muestras;
  - SMA de hasta 8 muestras;
  - `momentum = sma3/sma8 - 1`;
  - `score = clamp(0.5 + momentum*40 - spread*10)`;
  - threshold por defecto 0.62;
  - mínimo 6 muestras;
  - ventana por defecto 90 minutos;
  - spread máximo factual 2%.
- `historical_candle_shadow_rc6` sólo produce `decision_shadow`.
- IOL declara `DECISION_EFFECT = NO_FACTUAL_BINDING`.
- `rc6_counterfactual_learning` es read-only.
- `ch_empirical_learning` calcula expectativa observacional pero no autoajusta parámetros.
- `fc_mfe_mae_provenance_rc6` mide MFE/MAE con precio ejecutable, pero es offline.
- `PAPER_ECONOMIC_GATE_MODE` default del runtime es `SHADOW`.
- IA intradiaria permanece `OFF` por política.

Hipótesis a validar: la sofisticación de riesgo/ledger/observabilidad supera ampliamente la sofisticación del alpha factual, y la mayoría de las señales adicionales todavía no tienen autoridad sobre BUY/HOLD.

---

## 3. Fase 1 — Dataset maestro de los 82 trades

Crear un extractor nuevo **sólo si no puede componerse limpiamente con los módulos existentes**. Antes de escribir código, reutilizar:

- `ops_introspection_rc6.py`
- `rc6_decision_evidence_report.py`
- `rc6_postclose_review.py`
- `rc6_action4_audit.py`
- `et_shadow_learning_rc6.py`
- `fc_mfe_mae_provenance_rc6.py`
- `ch_empirical_learning.py`

### 3.1 Autoridades SQLite

Leer, cuando existan:

- `paper_positions`
- `paper_fills`
- `paper_learning_samples`
- `paper_decisions`
- `trade_gate_evaluations`
- `decision_evidence_snapshots`
- `paper_events`
- `paper_exit_intents`
- `market_snapshots`
- `candle_versions`
- `financial_instrument_catalog`

### 3.2 Una fila por operación cerrada

Campos mínimos:

**Identidad**
- paper_id
- strategy_version
- symbol
- asset_class
- market
- settlement
- currency
- contract identity / multiplier / step

**Entrada**
- opened_at
- entry_price
- entry_cost
- quantity
- notional
- bid / ask / spread al decidir
- book_at / trade_at / observed_at
- score factual
- threshold factual
- sma3 / sma8
- momentum
- samples
- signal_window_minutes
- max_spread

**Contexto contemporáneo**
- historical candle shadow
- IOL state / freshness / quote / spread / variation / volume
- market regime
- sector concentration
- macro shadow
- GDELT shadow
- economic diagnostics
- policy evaluation
- source provenance

**Riesgo**
- stop_price
- target_price
- candidate_stop_risk
- concurrent risk before / after
- capital
- exposure
- position caps
- liquidity cap
- daily risk state

**Salida**
- closed_at
- close_reason
- exit price efectivo
- exit cost
- duration
- net_pnl
- net_return_pct
- fill count / partial fills
- spread / profundidad de salida cuando exista

**Excursiones**
- MFE ejecutable
- MAE ejecutable
- timestamp MFE
- timestamp MAE
- observaciones usadas/rechazadas
- provenance
- estado MEDIDO / NO_MEDIDO

**Contrafácticos**
- baseline factual
- cada perfil SHADOW existente
- would_block / would_allow
- loss avoided / gain removed
- evidencia suficiente/insuficiente

### 3.3 Integridad antes de analizar

El extractor debe demostrar:

- total de CLOSED = total esperado por ledger;
- ningún paper_id duplicado;
- fill BUY y SELL coherentes;
- moneda no mezclada;
- timestamp timezone-aware;
- identidad de instrumento estable;
- net_pnl del dataset = net_pnl de ledger por moneda;
- conteo wins/losses = expectancy vigente por moneda;
- toda ausencia queda explícita, nunca imputada.

Gate de fase 1: **82/82 operaciones reconciliadas o listado exhaustivo de excepciones por paper_id**.

---

## 4. Fase 2 — Diagnóstico causal por cohortes

No evaluar sólo win rate global.

Cohortes obligatorias:

1. símbolo;
2. acciones vs CEDEAR;
3. moneda;
4. sector;
5. hora de entrada;
6. primera hora / media rueda / última hora;
7. spread de entrada por bandas;
8. profundidad de book;
9. score por deciles;
10. momentum por deciles;
11. número de muestras;
12. volatilidad / ATR;
13. régimen de mercado;
14. tendencia multi-timeframe;
15. IOL confirmaba / advertía / no disponible;
16. economic gate pass/fail;
17. causa de salida;
18. duración;
19. MFE/MAE;
20. versión/config hash/commit del motor.

Métricas por cohorte:

- n
- win rate
- expectancy
- profit factor
- avg win
- avg loss
- median pnl
- median return
- max drawdown de la secuencia
- porcentaje de costo sobre pérdida
- MFE mediano
- MAE mediano
- MFE antes de stop
- trades que nunca tuvieron MFE > costos
- trades que habrían alcanzado otro target/stop
- tiempo hasta MFE / MAE
- falso positivo / falso negativo de gates SHADOW

No declarar causalidad con cohortes pequeñas. Informar `INSUFFICIENT_SAMPLE`.

---

## 5. Fase 3 — Replay de la señal factual

Reconstruir cada decisión con datos conocidos en ese instante, sin look-ahead.

Primero reproducir 1:1 el baseline actual:
- 6 muestras;
- ventana 90m;
- SMA3/SMA8;
- score 0.62;
- spread max 2%;
- stop 2%;
- target 5%;
- economía según modo vigente;
- reglas de riesgo/sector vigentes en la fecha del trade.

Sólo si el replay reproduce decisiones y fills históricos dentro de tolerancias se habilita experimentación.

Gate: **baseline replay reproducible**.

---

## 6. Fase 4 — Matriz de experimentos SHADOW

No elegir parámetros a ojo. Evaluar perfiles versionados.

### Entradas

- señal actual como control;
- velas PPI completas como señal factual candidata;
- tendencia 1H;
- confirmación 15m;
- timing 5m;
- volumen;
- ATR;
- régimen;
- amplitud;
- sector;
- hora de entrada;
- IOL contemporáneo como confirmación;
- combinación de features sólo cuando exista evidencia contemporánea.

### Barreras

- stop/target actual 2%/5% como control;
- ATR-based;
- time stop;
- salida por deterioro de señal;
- no-entry alrededor de apertura, si los datos lo justifican.

### Microestructura

Evaluar spread máximo por bandas, no promover un número sin replay:
- <=0.25%
- <=0.50%
- <=0.75%
- <=1.00%
- control vigente <=2.00%

Incluir profundidad y spread de salida, no sólo spread de entrada.

### Threshold

Explorar 0.62/0.66/0.70/0.74 únicamente después de mejorar/reproducir features. No optimizar threshold sobre la misma muestra usada para seleccionar features.

---

## 7. Validación anti-overfitting

Obligatorio:

1. separar entrenamiento / validación temporal;
2. walk-forward por jornadas;
3. comparar contra baseline congelado;
4. medir sensibilidad de parámetros;
5. exigir mejora robusta en más de una ventana temporal;
6. penalizar estrategias que mejoran PnL eliminando casi todas las operaciones;
7. no usar datos futuros para features de entrada;
8. reportar intervalos de incertidumbre;
9. no promover por una única métrica.

La selección final debe considerar al menos:
- expectancy;
- profit factor;
- drawdown;
- estabilidad temporal;
- sample size;
- turnover/costos;
- precision de gates;
- número de trades eliminados;
- impacto por moneda/familia.

---

## 8. Preguntas que la auditoría debe responder

1. ¿Cuánto de la pérdida acumulada es movimiento adverso y cuánto fricción?
2. ¿Qué símbolos/sectores destruyen más expectativa?
3. ¿El score 0.62 discrimina winners de losers?
4. ¿Existe relación monotónica score → outcome?
5. ¿El spread máximo 2% deja entrar operaciones estructuralmente inviables?
6. ¿Cuántos losers no tuvieron nunca MFE suficiente para cubrir costos?
7. ¿Cuántos losers se volvieron positivos después del stop?
8. ¿El stop fijo es el problema o la entrada ya era mala?
9. ¿Qué habría hecho ATR?
10. ¿Qué habría hecho el filtro de régimen?
11. ¿Qué habría hecho IOL si hubiera sido binding?
12. ¿Qué SHADOW gates tienen precisión suficiente y cuáles destruyen ganancias?
13. ¿La concentración sectorial vigente evitó pérdidas o sólo limitó tamaño?
14. ¿Qué hora de entrada concentra peores resultados?
15. ¿El target 5% es alcanzable en el horizonte real observado?
16. ¿El economic gate SHADOW habría bloqueado pérdidas con pocos falsos positivos?
17. ¿Hay drift entre versiones/configuraciones a lo largo de los 82 trades?
18. ¿Hay cohortes con expectativa positiva real o toda la estrategia es negativa?
19. ¿Qué señal candidata mejora out-of-sample?
20. ¿Qué cambio mínimo ofrece la mejor mejora con menor complejidad?

---

## 9. Entregables antes de implementar cambios

1. `rc6_trade_master.csv/json` — 82 filas o excepciones explícitas.
2. `rc6_trade_audit_summary.json`.
3. Reporte por símbolo/sector/hora/spread/score/exit.
4. Reporte MFE/MAE.
5. Reporte de costos y slippage.
6. Reporte de gates SHADOW.
7. Replay baseline.
8. Comparación de perfiles candidatos.
9. Matriz train/validation/walk-forward.
10. Propuesta final de cambio mínimo, con evidencia de qué mejora y qué empeora.

Ningún entregable autoriza un cambio productivo por sí mismo.

---

## 10. Criterios para recién entonces proponer implementación

Un candidato sólo pasa a propuesta técnica si:

- reproduce correctamente el baseline;
- tiene suficiente muestra;
- mejora expectancy out-of-sample;
- mejora o mantiene drawdown;
- no depende de look-ahead;
- incluye costos y precios ejecutables;
- no obtiene la mejora sólo eliminando casi todos los trades;
- conserva fail-closed y trazabilidad;
- tiene tests reproducibles;
- puede operar en SHADOW primero;
- no toca PPI Watch;
- no habilita dinero real.

La promoción sería, en otra rama y otro PR, primero **SHADOW**. Cualquier BINDING PAPER exigiría evidencia posterior y autorización explícita.

---

## 11. Lo que NO se implementa en esta rama

- no se cambia `PAPER_SCORE_THRESHOLD`;
- no se cambia `PAPER_SIGNAL_MIN_SAMPLES`;
- no se cambia `PAPER_SIGNAL_WINDOW_MINUTES`;
- no se cambia `PAPER_STOP_LOSS_PCT`;
- no se cambia `PAPER_TARGET_GAIN_PCT`;
- no se cambia spread máximo;
- no se promueve economía;
- no se promueve IOL;
- no se habilita IA intradía;
- no se toca sizing/risk;
- no se despliega al Droplet.

Esta rama existe únicamente para fijar el método correcto y evitar tuning por intuición.
