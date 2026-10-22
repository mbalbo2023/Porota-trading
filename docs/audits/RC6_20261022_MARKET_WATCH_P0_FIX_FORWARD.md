# RC6 Market Watch P0 — independent critical approval image

Checkpoint ID: RC6-20261022-MARKET-WATCH-P0-FIX-FORWARD-01
Baseline main SHA: `5a87538d3816601a4fddf4aaafc975b4ebc674d7`
Product deploy branch SHA observed: `c16a093df2161fbb6ef17edd44d17adf64578f85`
Evidence: [read-only host inventory 39697722110](https://github.com/mbalbo2023/Porota-trading/actions/runs/39697722110), [historical P0 39648438228](https://github.com/mbalbo2023/Porota-trading/actions/runs/39648438228).

The observer and dashboard run the same image from `CURRENT_STATE_V2.image_id`. The critical approval broker is separately owned and has its own image, while remaining running, health checked and issues-only in the canonical product quiescence admission. Market Watch previously required all three images to match the product provenance, causing `CONTAINER_IMAGE_MISMATCH:porota_critical_approval_rc6` even for a correctly isolated critical broker.

The monitor now compares only observer/dashboard against the product image. It still requires the critical broker to be running without a new OOM and with a well-formed SHA-256 Docker image identity. Missing or malformed critical identity stays P0; a promoted observer/dashboard mismatch stays P0. The offline regression covers independent image, promoted mismatch and malformed critical identity. The probe is read-only and does not contact the broker or PPI Watch.

The product image installed by Deploy V2 39696980751 is still `DEPLOYED_VALIDATION_PENDING` due an expired v1 complementary contract sweep. Market Watch must continue to report `DEPLOY_PROVENANCE_INVALID` P0 until the canonical product fix-forward completes. The first genuine scheduled Market Watch run after product validation must be inspected for P0 and schedule silence; CI alone cannot establish cadence.

Safety invariants: `PRODUCTION_PAPER`, `REAL_ORDERS_SENT=0`, `CASH_SWEEP_ORDER_ROUTING_ALLOWED=False`, fail-closed. No real orders, host stress, rollback, or PPI Watch changes.
