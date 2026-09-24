# POROTA TRADING RC6 — ALPHA TUNING CHECKPOINT

**Local date:** 2026-09-23 America/Argentina/Buenos_Aires  
**Purpose:** preserve exact continuity of the exhaustive PAPER-performance audit and alpha-tuning study.

## Canonical repository/deploy state

- Repository: `mbalbo2023/Porota-trading`
- **Canonical deploy branch:** `deploy/rc6-pr69-isolated-20260915`
- Canonical SHA verified at start of this audit: `a5b5d14aa2bd88056ec8e0350464546251a8ad4d`
- **DO NOT use `main` as the canonical deploy branch.**
- Audit branch: `audit/rc6-alpha-tuning-analysis-20260923`
- Audit branch was created directly from the canonical SHA above.
- Latest audit-code SHA before this checkpoint: `26b42510d4993fccec99461a1dfe41ce7242f9e3`
- No production deploy has been performed from the audit branch.
- No factual strategy parameter has been changed.
- No live broker route has been called.
- Every completed audit workflow verified before and after: `PRODUCTION_PAPER|0`.

## Safety / scope rules

1. PAPER only. Real orders remain forbidden.
2. All runtime SQLite access must use `mode=ro` and `PRAGMA query_only=ON`.
3. No PPI Watch changes.
4. No production restart or deploy for this audit.
5. No automatic promotion of SHADOW gates to BINDING.
6. No changing threshold, stops, targets, spread cap, sizing, risk, IOL binding, AI, session policy, or economic gates until the evidence phase is complete.
7. PPI remains primary. IOL may complement only where contemporaneous persisted evidence exists.
8. Never mix ARS and USD_MEP PnL into one monetary total.
9. Never infer missing historical context. Mark insufficient evidence explicitly.
10. Use isolated branch -> narrow PR to canonical -> tests -> diff review -> merge -> canonical workflow only if/when an implementation is explicitly approved later.

## Verified historical sample

Ledger authority: `observer_v17.db`.

Closed PAPER trades = **82**.

- ARS: 75 trades, 9 wins, 66 losses, win rate 12%, net `-52135.2627 ARS`.
- USD_MEP: 7 trades, 0 wins, 7 losses, net `-4.8069 USD_MEP`.
- Combined count: 82 trades, 9 wins, 73 losses, 10.9756097561% win rate.
- MFE/MAE measured for 82/82 trades.
- No duplicate paper_id.
- No missing BUY/SELL fills in the reconciled trade master.

## Reproducibility of the factual baseline

Historical `paper_decisions`: 53,750 rows.
Replayable with persisted frozen technical inputs: **16,532** decisions.
- action match rate = 1.0
- score match rate = 1.0
- mismatches = 0

Therefore the current factual decision formula has been reproduced exactly where inputs were persisted. This is the baseline control for all future experiments.

## Main quantitative findings so far

### Signal quality

- Overall score AUC, higher-is-better: **0.3637747336**.
- This is below 0.5: higher factual scores did not discriminate winners positively in this sample.
- Trades with stored score >= 0.70: **10 trades, 0 wins, 10 losses**, average return about -2.1888%.
- Conclusion: simply raising the 0.62 threshold is not supported.

### Friction / costs

ARS only:
- gross PnL before costs: `-16423.6727 ARS`
- recorded entry+exit costs: `35711.59 ARS`
- net PnL: `-52135.2627 ARS`
- gross-positive but net-negative trades: **18**

Costs materially worsen an already-negative gross edge. The problem is not only costs: gross PnL itself is negative.

### Target / MFE evidence

- MFE >= factual +5% target: **0 trades**.
- +5% target was therefore unrealistic for this historical holding horizon.
- Precedence-aware, full-depth, cost-modelled isolated target counterfactual:
  - gross +0.50% target: ARS delta +4131.7536; candidate ARS net -48003.5091
  - gross +0.75% target: ARS delta +6136.2173; candidate ARS net -45999.0454
  - gross +1.00% target: ARS delta +4299.6444; candidate ARS net -47835.6183
  - gross +1.25% target: ARS delta +4726.8277; candidate ARS net -47408.4350
  - gross +1.50% target: ARS delta +3087.4976; candidate ARS net -49047.7651
  - gross +2.00% target: ARS delta +825.3036; candidate ARS net -51309.9591
  - gross +2.50% target: ARS delta +1085.1912; candidate ARS net -51050.0715
  - gross +3.00% target: ARS delta +29.7640
  - gross +3.50% and +5.00%: no trade changed
- Best gross-target candidate tested so far was +0.75%, but it still leaves deeply negative PnL.
- Therefore exit tuning alone cannot fix the strategy.

### Stop evidence

There were 16 factual STOP_PAPER trades with post-exit tracking:
- measured 120m: 16/16
- recovered to entry within 30m: 0
- within 60m: 0
- within 120m: 1
- recovered net breakeven after costs within 120m: 0
- reached +1% within 120m: 0
- reached +2% within 120m: 0
- median max return after stop over 120m: about -1.7212%

Current evidence does **not** support widening the stop. Most stopped positions did not recover.

### Point-in-time candle filters

Point-in-time reconstruction uses only data known by original feature timestamp; candle versions are deduplicated to one latest-known version per bar.

Coverage:
- closed = 82
- >=21 usable 5m candles = 79
- daily-history coverage is weak: only 9 trades have >=20 daily bars, so daily trend conclusions are insufficient.

Simple filters tested on factual opened trades:
- 5m momentum 3v15 positive
- EMA9 > EMA21
- RSI 35-65
- rising RSI <70
- momentum positive AND EMA up
- EMA up AND RSI rising
- limited daily-trend variants

None produced a robust positive cohort. In several cases second-half validation was worse than first-half. Do not promote these simple filters.

### Entry context study

Coverage 82/82 for:
- book imbalance
- breadth
- same-day asset return
- fresh book <=120s

Filters tested:
- positive book imbalance
- positive/non-negative breadth
- positive same-day asset return
- breadth positive AND asset positive

All retained cohorts remained materially negative. Several second-half subsets had zero winners. These are not supported as simple BINDING filters.

## Architectural diagnosis confirmed

The factual entry engine is still dominated by:
- latest trades
- SMA3 vs SMA8
- momentum
- spread penalty
- threshold default 0.62
- min samples / signal window

while richer components remain SHADOW/read-only/non-binding:
- IOL decision input
- historical candles / richer technical context
- counterfactual learning
- MFE/MAE analytics
- empirical-learning statistics
- economic gate default SHADOW
- AI intraday remains OFF by policy

The current problem is primarily **alpha / entry selection**, with costs amplifying losses. Infrastructure/risk/ledger are much more mature than the factual predictive signal.

## Audit code currently in branch

Important scripts include:
- `rc6_trade_master_audit.py`
- `rc6_baseline_replay_audit.py`
- `rc6_alpha_tuning_analysis.py`
- `rc6_trade_cohort_analysis.py`
- `rc6_exit_target_counterfactual.py`
- `rc6_exit_grid_replay.py`
- `rc6_point_in_time_entry_features.py`
- `rc6_entry_context_audit.py`
- `rc6_entry_breadth_audit.py`

Tests exist for each active audit module.
Superseded duplicate candle diagnostic was removed.
Workflow:
`.github/workflows/rc6-alpha-tuning-readonly-audit-20260923.yml`

## Known completed workflow evidence

Important successful runs:
- run 12 / 35944902350: baseline replay
- run 15 / 35945441839: precedence-aware lower-target replay
- run 20 / 35945917444: consolidated alpha audit, GREEN
- run 21 / 35946098360: point-in-time entry context study, GREEN

Some intermediate runs were cancelled because later commits superseded them or the user stopped the UI. Do not treat cancelled runs as final evidence.

Run 25 / 35946456546 completed SUCCESS and GREEN from audit SHA `26b42510d4993fccec99461a1dfe41ce7242f9e3`.
- PRE_SAFETY=`PRODUCTION_PAPER|0`
- POST_SAFETY=`PRODUCTION_PAPER|0`
- cost-aware NET target replay:
  - +0.25% net: changed 20 trades; ARS delta +3884.1776; candidate ARS net -48251.0851
  - +0.50% net: changed 15; ARS delta +1911.7547; candidate ARS net -50223.5080
  - +0.75% net: changed 13; ARS delta +1907.5655; candidate ARS net -50227.6972
  - +1.00% net: changed 9; ARS delta +1200.8280; candidate ARS net -50934.4347
- Best cost-aware net target tested is +0.25%, but strategy remains deeply negative.
- point-in-time breadth coverage 82/82:
  - bearish breadth: 2 trades, 0 wins, -1286.4985
  - mixed/positive: 80 trades, 9 wins, -50853.5711
  - ARS AUC rising_fraction = 0.3552188552
- breadth positivity is not a useful positive discriminator in this sample.

Exploratory no-chase / mean-reversion cross-check using the same SUCCESS run-25 artifact:
- score<0.64: 37 trades, 6 wins, net -24189.7201; second temporal half 15 trades, 1 win, net -11633.0306.
- candle momentum<=0: 40 trades, 6 wins, net -25211.2742.
- same-day return<=0: 31 trades, 5 wins, net -13583.6706.
- candle momentum<=0 AND same-day return<=0: 24 trades, 5 wins, net -9618.9212; second half 11 trades, 2 wins, net -5266.7952.
- adding breadth<=0: 23 trades, 5 wins, net -9453.5912.
- low-score<0.64 AND same-day return<=0: 16 trades, 4 wins, net -6269.3458; second half 6 trades, 1 win, net -4117.1388.
- combining these cohorts with cost-aware net targets still leaves every tested cohort negative.
Conclusion: a simple inversion of the momentum logic is not a viable alpha either.

Coverage limitations that matter:
- immutable decision evidence: 13/82 trades
- IOL point-in-time snapshot: 13/82
- historical candle shadow persisted in trade features: 24/82
- daily history >=20 bars at entry: only 9/82
- MFE/MAE: 82/82

SHADOW diagnostics already observed:
- economic gate would-fail: 6 ARS trades, 0 winners, combined factual net -10028.1105 ARS. This is promising but sample is small; do not promote from six observations.
- historical candle shadow HOLD: 6 ARS trades, including 2 winners, combined factual net -2404.528 ARS. Not clean enough to bind.

## Current next step

1. Do NOT tune the old SMA3/SMA8 score further; both higher-score momentum and simple reversed/mean-reversion variants failed.
2. Build the next candidate as a genuinely new SHADOW alpha model, not a threshold tweak.
3. Before modeling, improve point-in-time feature coverage:
   - persist IOL/context evidence for every decision, not only 13/82 historical trades;
   - ensure versioned 5m candles and longer-history context are available at every decision;
   - capture market/sector regime, spread/depth and costs contemporaneously;
   - retain exact feature vector + config/SHA for every BUY and HOLD.
4. Generate outcomes not only for factual BUY trades but also for historical HOLD/candidate opportunities, otherwise selection-bias remains.
5. Use walk-forward by day. Do not select a profile on the same 82 trades and call it validated.
6. Candidate-model families for SHADOW study should include:
   - cost-aware expected move / probability model rather than hand-set score;
   - ranking across the contemporaneous universe instead of independent absolute threshold only;
   - features from return structure, volatility/ATR, book/spread/depth, regime, sector, time-of-day, and IOL where contemporaneously available;
   - calibrated net-return target / expected value after costs.
7. First success criterion: positive out-of-sample expectancy and profit factor >1 with adequate sample, not merely higher win rate.
8. Only after a robust SHADOW candidate exists, prepare a separate implementation branch/PR. No factual binding yet.

## User requirements / lessons

- Do not hallucinate or fill gaps.
- Verify current branch/SHA before acting.
- Never call `main` canonical.
- Prefer Actions for audits; no Droplet mutation.
- Preserve repo hygiene; no duplicate temporary analyzers/workflows.
- Do not repeat already-completed work.
- Keep user updated with concrete evidence.
- If UI says “system checks/comprobaciones”, explain that these are GitHub audit checks, not real trading, and continue from the last successful run.
