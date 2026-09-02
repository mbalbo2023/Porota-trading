# Porota Trading 17.0.0-rc3-hf6 — Acceptance Record

## Decision

**ACCEPTED**

## Runtime

- Version: `17.0.0-rc3-hf6`
- Mode: `PRODUCTION_PAPER`
- Execution: `SIMULATED`
- PPI market data: read-only
- PPI orders: blocked
- Real orders sent: 0
- Database quick check: OK
- Open positions at acceptance: 0
- Orphan fills: 0
- PPI authentication after stabilization: OK
- Observer state after stabilization: WAITING_MARKET
- Session: MARKET_CLOSED

## Risk configuration

- PAPER_RISK_PER_TRADE: 0.002
- PAPER_MAX_OPEN_POSITIONS: 5
- PAPER_MAX_HOLD_MINUTES: 360
- PAPER_DAILY_SOFT_STOP_PCT: 1.5
- MAX_DAILY_LOSS_PCT: 2.5
- PAPER_AI_GATE_MODE: OFF
- PAPER_ECONOMIC_GATE_MODE: BINDING
- PAPER_SCALPING_MODE: ACTIVE_PAPER

## HF6 deployment corrections

1. The transient systemd launcher was corrected from
   `TimeoutStartSec=0` to `TimeoutStartSec=infinity`.
2. The missing HF5 export manifest was restored only after all
   246 HF5 source files matched the audited manifest hashes.
3. The soft daily stop is no longer implicitly forced to 1.5 when
   PAPER_DAILY_SOFT_STOP_PCT is absent; DailyRisk may inherit the
   hard limit in that compatibility case.
4. The legacy exit-supervision clock test now explicitly requests
   the historical 180-minute horizon. Production HF6 remains at
   360 minutes.
5. The complete pytest suite passed before runtime activation.

## Provenance

- Base audited Git commit: `60cfb13e03f3bfe6dbfcbe3158dca9273279fb7e`
- HF6 retry3 payload SHA-256:
  `64b40f14131d098d15b757d73d27595529bfd2bc9b20dfa609f6d61498b83e49`
- Accepted Docker image:
  `sha256:4de66950561e73410d16e3cee3c2fa592ec6027dd076faaf7286af950f094d07`
- Deployment result:
  `data/deployments/v17_rc3_hf6_deploy_20260901T235104501896Z.json`
- Acceptance health:
  `{"status":"ok","version":"17.0.0-rc3-hf6"}`

No secrets, databases or runtime data are intended to be stored in
the release branch.
