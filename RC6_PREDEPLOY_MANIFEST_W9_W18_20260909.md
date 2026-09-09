# RC6 clean predeploy manifest — W9-W18
Baseline live: `9197aedf35fe1739b594c742902e80df069c47c9`

Included runtime deltas:
- W9 `08112ca2ec44c7a856619ffc1cab9c13752e6822`: fixed-income nominal contract + cauciones contract.
- W10 `617244b23b54e4c227b1d76bc217460be4d78cd5`: sector concentration BINDING in policy gate and mode manager.
- W13 `7e2cb576a05622b1a8270036465dd6eea728867f`: history reconciler + backfill planner.
- W14 `2297871c21da8eddf03c4e5ea25b60c4f7013a90`: SRE split health/preopen/postclose host tooling.
- W15 `34316c10678774d51ea209687011a62a5a0bd2a1`: Forward Lab v2 + MFE/MAE provenance.
- W16 `ef40b43b7c17f3cfc6d78d6787481bd016cb78cb`: safe Docker build-cache housekeeping only.
- W18 `40b68b3893236455ed616bbe3342bae4d3e3a815`: critical control plane package with automatic rollback removed.

Not copied because already covered by live baseline/proof and no runtime delta:
- W11 Event Risk/GDELT proof.
- W17 tablet/Voice Access UX proof.

W12 Contract Evidence is intentionally excluded until fresh authenticated live proof is GREEN.
Safety invariant: `PRODUCTION_PAPER`; real broker order routes remain blocked; no automatic rollback.
