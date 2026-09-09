# W18 RC6 — No Automatic Rollback Gate

Current RC6 deployment policy requires **NO automatic rollback**.

A W18 control-plane deploy candidate is not deploy-ready if its installer contains an EXIT trap or failure handler that automatically disables services, removes units, destroys containers, or reverts the release. Failure must stop and surface explicit state for operator-controlled recovery.

Required invariants:
- `PRODUCTION_PAPER`
- `real_orders_sent=0`
- no `/Operar` or broker order routes exercised
- no automatic rollback
- pre/post observer and dashboard identity preserved unless the wave explicitly owns them
