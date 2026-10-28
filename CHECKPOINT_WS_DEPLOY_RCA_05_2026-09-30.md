# CHECKPOINT WS-DEPLOY-RCA-05 — 2026-09-30

## Ownership
- WORKSTREAM_ID: WS-DEPLOY-RCA-05
- Mode at checkpoint: WRITE_OWNER
- Branch: `work/ws-deploy-rca-05-preopen-observability-20260930`
- Base/product SHA: `401d77699a04fcf726f46d3124323572e407628c`
- Scope: Deploy V2 preopen diagnostics + regression only.
- PAPER/SHADOW ONLY.
- real_orders_sent=0.
- Real routes: NONE / NOT_CALLED.
- PPI Watch: UNTOUCHED.
- FIX-FORWARD ONLY.

## Verified entry state
- Product branch `deploy/rc6-pr69-isolated-20260915` is exactly `401d77699a04fcf726f46d3124323572e407628c`.
- Product merge contains WS-DEPLOY-RCA-04 candidate `6ac7cb0f112e986fce6249aeba11a4ea627ed6ca`.
- Exact Predeploy V2 for that candidate: run `36757416581`, artifact `11116877894`.
- Deploy V2 run `36758005610` promoted the frozen candidate and then ended FAILURE.

## Last verified GREEN gate in run 36758005610
- Runtime image observer/dashboard: same loaded immutable image.
- PRODUCTION_PAPER.
- real_orders_sent=0.
- Full contract reconciliation: GREEN.
- READY: 6,955.
- Dashboard: 16/16 audited HTTP routes returned 200.
- Scalping stabilization: GREEN with a post-gate heartbeat.
- Immediate Zero-Known-Error audit: GREEN.
- PPI Watch not modified up to that point.

## Exact unresolved failure
Immediately after `RC6_ZERO_KNOWN_ERROR_IMMEDIATE=GREEN`, Deploy V2 invoked:
`rc6_preopen.py --phase T_MINUS_45`.

That command returned exit code 2. Because the workflow executed the command substitution while `set -e` was active, the shell exited before printing `PREOPEN_OUTPUT`. The actual RED preopen check is therefore not present in the run log.

## Fix-forward in this branch
Capture `PREOPEN_OUTPUT` and `PREOPEN_RC` under bounded `set +e`, always print diagnostics, then fail closed with the same non-zero RC. No readiness relaxation and no runtime mutation is added.

## Recovery rule
Do not rerun Deploy V2 blindly. First require this observability guard to be GREEN in Predeploy V2. On the next deploy attempt, if preopen remains RED, use the printed `red_checks` as the next RCA input.
