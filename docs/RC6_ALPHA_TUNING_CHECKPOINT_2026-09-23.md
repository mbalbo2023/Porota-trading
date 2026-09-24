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

At checkpoint creation:
- run 25 / 35946456546 was running from audit SHA `26b42510...`
- purpose: cost-aware **net-return** target grid plus rerun of the full consolidated audit
- use its results only if it finishes SUCCESS and post-safety remains `PRODUCTION_PAPER|0`.

## Current next step

1. Check run 25 status first.
2. If SUCCESS:
   - capture NET_TARGET results for +0.25%, +0.50%, +0.75%, +1.00% **net after modeled costs**
   - capture separate point-in-time breadth result
   - verify POST_SAFETY=PRODUCTION_PAPER|0
3. Do not yet implement any tuning.
4. Next analytical question:
   - test whether avoiding chase / mean-reversion-style entry conditions distinguish winners better than current momentum score
   - evaluate joint entry + exit profiles only as SHADOW/read-only
   - preserve time split / walk-forward; no optimization on the full sample alone
5. Only after a candidate is positive and stable out-of-sample, write a proposal for a new SHADOW alpha profile.
6. Any future implementation must be a separate branch/PR and must not overwrite the canonical deployment until explicitly approved.

## User requirements / lessons

- Do not hallucinate or fill gaps.
- Verify current branch/SHA before acting.
- Never call `main` canonical.
- Prefer Actions for audits; no Droplet mutation.
- Preserve repo hygiene; no duplicate temporary analyzers/workflows.
- Do not repeat already-completed work.
- Keep user updated with concrete evidence.
- If UI says “system checks/comprobaciones”, explain that these are GitHub audit checks, not real trading, and continue from the last successful run.
