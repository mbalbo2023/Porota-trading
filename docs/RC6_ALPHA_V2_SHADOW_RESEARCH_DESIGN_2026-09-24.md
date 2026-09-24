# RC6 — ALPHA_V2_SHADOW Research Design

**Date:** 2026-09-24  
**Base canonical:** `deploy/rc6-pr69-isolated-20260915@a5b5d14aa2bd88056ec8e0350464546251a8ad4d`  
**Branch:** `feat/rc6-alpha-v2-shadow-research-20260924`  
**Mode:** DESIGN / SHADOW ONLY / NO FACTUAL AUTHORITY

## 1. Why this exists

The completed RC6 audit shows that the factual alpha family should not receive further parameter tuning as a primary strategy.

Verified audit evidence:

- 82 closed PAPER trades: 9 wins, 73 losses.
- ARS: 75 trades, 9 wins, 66 losses, net -52,135.2627 ARS.
- Current factual score AUC on closed trades: 0.3637747336.
- Stored score >= 0.70: 10 trades, 0 wins.
- No closed trade reached executable +5% MFE.
- Gross ARS edge was already negative before costs.
- Lower gross/net take-profit levels improved losses but did not create positive expectancy.
- Widening stops is contradicted by post-stop evidence.
- Momentum/EMA/RSI/breadth/simple mean-reversion filters did not pass temporal robustness.
- Predefined factual-family profile comparison returned no candidate passing the train/validation gate.
- Decision-level evidence shows factual BUY selection missed the recent executable-opportunity set:
  recent strict decision labels contained BUY 0 positives while HOLD contained positive opportunities.
- A cross-sectional `history_trend50 LOW` rank increased opportunity hit rate but failed full sequential economic replay.
- Adding reversal triggers and 30/60/120-minute time-stops also produced zero profiles passing the expectancy gate.

Therefore ALPHA_V2 must not be a new threshold for SMA3/SMA8 and must not be a hand-tuned inversion of that score.

## 2. Objective

Build a new SHADOW-only alpha research pipeline that estimates:

1. probability that a candidate can achieve a positive executable NET return after costs;
2. expected executable NET return over defined horizons;
3. expected adverse excursion / downside risk;
4. a cross-sectional rank against the simultaneously observable universe.

The first implementation has **NO authority** to alter factual BUY/HOLD, risk, sizing, execution, exits or broker routes.

## 3. Principle: learn from BUY and HOLD

Training only from executed trades creates selection bias.

The current evidence infrastructure already persists recent immutable decision snapshots. Audit results showed:

- 53,750 historical decisions in total.
- 6,506 recent immutable evidence snapshots.
- 6,096 decisions with point-in-time quote plus future executable-BID path suitable for a later label study.
- Strict recent unit-opportunity research included thousands of HOLD decisions in addition to BUY.

ALPHA_V2 uses decision-level opportunities, not only realized positions.

Historical rows with missing evidence remain excluded. No backfilling by inference.

## 4. Point-in-time feature contract

For every candidate observation used by ALPHA_V2, persist or consume only information known at that instant.

### Identity
- decision_key
- decided_at / observed_at
- symbol
- asset_class
- market
- settlement
- currency
- strategy_version
- code/config provenance

### Market microstructure
- bid / ask
- bid_size / ask_size
- spread
- book imbalance
- quote age / book age / trade age
- observable depth
- participation feasibility

### Return / volatility structure
- short factual momentum
- 5m returns / candle momentum
- multi-timeframe return structure
- ATR / average range
- average absolute return
- 20/50-bar trend context when point-in-time history exists
- volatility regime

### Cross-sectional context
- rank within contemporaneous candidate set
- market breadth
- sector
- sector-relative movement
- time of day
- session state

### Costs
- full-leg fee rate
- intraday rebated-leg rate when eligible
- spread cost
- modeled entry slippage
- modeled exit slippage
- flat-price round-trip cost floor
- minimum gross move required for each requested NET target

### External complements
- IOL only if a contemporaneous persisted snapshot exists and is fresh
- macro/GDELT only when already cached/persisted and timestamp-safe
- missing external context remains UNKNOWN, never inferred

## 5. Labels

Labels are generated only after the observation horizon and never become entry features.

Primary labels:

- executable NET +0.25% before stop / EOD
- executable NET +0.50% before stop / EOD
- maximum executable NET return over 30/60/120 minutes
- minimum executable NET return / MAE
- time to first positive NET return
- time to target
- stop-before-target
- no-opportunity / insufficient evidence

Execution assumptions must use:

- ASK for hypothetical long entry;
- BID for hypothetical long exit;
- displayed depth;
- slippage;
- canonical `au_fee_schedule`;
- applicable intraday PPI rebate;
- session/EOD precedence.

These are historical opportunity labels, not synthetic factual orders.

## 6. Model family — deliberately simple first

Do not begin with an LLM or opaque neural network.

Baseline candidates:

1. calibrated logistic regression for target probability;
2. regularized linear model for expected NET return;
3. a tree/gradient-boosting model only as a challenger if enough independent sessions exist.

Every model must have an interpretable baseline and preserve feature attribution.

The current factual score remains a benchmark, not a feature that automatically receives positive weight.

## 7. Cross-sectional decision form

Instead of:

`score >= fixed threshold -> BUY`

ALPHA_V2 should produce, conceptually:

`expected_net_return`
`p_net_25`
`p_net_50`
`expected_downside`
`cost_floor`
`rank_within_cycle`
`evidence_quality`

A candidate can only become an ALPHA_V2 SHADOW candidate if:

- evidence is complete;
- expected return is net of costs;
- expected move clears the cost floor with margin;
- rank is competitive within the same cycle;
- downside is acceptable;
- no factual/risk authority is exercised.

If no instrument has sufficient expected NET opportunity, ALPHA_V2 emits no candidate.

## 8. Temporal validation

The 21–23 September evidence demonstrates large day-to-day regime variation. Three sessions are not enough to promote a model.

Before a promotion proposal:

- accumulate at least 10 independent operational sessions with the current evidence contract;
- use day-level walk-forward validation;
- never random-shuffle observations across the same day;
- never tune on the validation day;
- report every fold;
- require enough positive labels in validation to make the metric meaningful.

A model must beat the factual baseline and naive rank baselines consistently, not only in aggregate.

## 9. Research gates

A SHADOW model is worth continued evaluation only if, out of sample:

- expected NET return is positive;
- profit factor > 1 in sequential executable replay;
- train and validation are both positive;
- sample size is sufficient;
- performance is not generated by one day or one symbol;
- performance survives canonical costs/slippage;
- max drawdown is acceptable versus baseline;
- the result is not obtained by eliminating almost all opportunities.

No single AUC or win rate is sufficient.

## 10. Runtime integration if research later passes

The current scanner evaluates symbols sequentially in `bf_production_paper_observer.py`.

A cross-sectional SHADOW evaluator belongs **after the per-symbol scan completes**, when the complete cycle is available, and before/around cycle-level bookkeeping. It must not replace or intercept `broker.on_quote()`.

Future SHADOW integration should:

- collect the cycle's immutable decisions/evidence;
- calculate ranks after the whole selected universe has been observed;
- persist to a separate ALPHA_V2 shadow table/artifact;
- mark `decision_effect=NO_FACTUAL_BINDING`;
- never call `_open()`;
- never alter factual decisions;
- never call broker order routes;
- keep AI intraday OFF.

## 11. Current experimental lessons

### What is rejected
- raising the current score threshold;
- widening the stop;
- lowering target as the only fix;
- positive momentum as a simple gate;
- EMA/RSI as simple gates;
- simple market-breadth gate;
- simple inversion/mean reversion of the factual score;
- `TREND50_LOW` ranking by itself;
- `TREND50_LOW` plus simple candle-momentum confirmation;
- short 30/60-minute time-stop as a standalone fix.

### What remains useful
- decision-level BUY+HOLD labels;
- cross-sectional ranking architecture;
- explicit cost floor;
- volatility/opportunity features;
- long-trend context as a research feature, not a rule;
- exact point-in-time evidence;
- sequential executable replay;
- walk-forward by session.

## 12. Next implementation step

Do **not** change the factual motor.

Next work item:

1. audit current post-2026-09-21 decision evidence completeness by feature and by day;
2. ensure future score-ready HOLD decisions preserve the same rich feature contract as BUY candidates;
3. define an ALPHA_V2 daily research dataset produced after the labeling horizon;
4. accumulate additional sessions;
5. train the first interpretable probability/expected-NET model offline;
6. replay it sequentially before any runtime SHADOW scoring is proposed.

This branch is intentionally a clean design branch from the canonical deploy lineage. No deploy is authorized by this document.
