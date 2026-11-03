# RC6 A169: staged gzip failure and exact held-stop recovery

28 October 2026, 17:05 ART. Owner-controlled incident in canonical Deploy V2 [#40755524824](https://github.com/mbalbo2023/Porota-trading/actions/runs/40755524824), attempt 2, candidate `b778c8063e96574ddf859907abb99fae6f4e3484`, tree `48ddaa1a55a267f6f0044eb111d88234e103a384`. The matching [Predeploy V2 #40751608804](https://github.com/mbalbo2023/Porota-trading/actions/runs/40751608804) completed 5,330/5,330 gates with zero failures, errors, skips or xfails and artifact `12844723709`. Predeploy does not attest host installation.

## RCA / IVAC

| Phase | Evidence and decision |
|---|---|
| Investigate | Exact Droplet `594077619/nyc1` passed identity, owner literals, 7,133,745,152 bytes available against 4,056,457,980 required, 16 timers suspended, old #751 pair drained, frozen bundle transferred and staged hashes checked. Promotion stopped before `quiescence install` and before `docker load` at `gzip -9 -c "$STAGE/scripts/rc6_deploy_scheduler_suspend.py"`: GNU gzip returned exit 2 with `file timestamp out of range for gzip format`. The deterministic tar builder set member mtime to zero; passing the staged pathname makes gzip inspect that mtime. |
| Verify | The failure-safe recovery omitted the staged A125 candidate source needed by `a143_stopped_continuation` for the exact A151 hold; it persisted `RECOVERY_UNVERIFIED`. Exact attempt-only payload cleanup removed staged transfer bytes and raised free space from 7,100,399,616 to 7,389,655,040 bytes (+289,255,424). It preserved shared Docker layers, data, backup evidence, PPI Watch and the two exited old containers. Barrier remained `IDLE` for explicit reconciliation. No new image or container was installed. |
| Act | [A169 one-time Action #40759308274](https://github.com/mbalbo2023/Porota-trading/actions/runs/40759308274) checked the old image and container IDs, marker/frozen/receipt chain, PAPER/0 and flat three books, A151 receipt, all 16 timers, PPI state and exact barrier. Under the canonical host mutex it changed only the prior candidate receipt to `PRIOR_A143_UNVALIDATED_HELD_STOPPED` and retired that exact barrier. Runner-side `rg` was missing after the host returned `RECONCILED`, so this Action's overall conclusion is red; its mutator output is recorded. |
| Control | [Independent read-only Action #40759770637](https://github.com/mbalbo2023/Porota-trading/actions/runs/40759770637), job `123068218629`, succeeded after the mutator: barrier absent; durable receipt and both old Docker IDs exited; process `STOPPED/BOOT_CALENDAR`; PAPER/0; 16 timers held; PPI Watch observed state only; 7,389,327,360 bytes free. Runtime remains **not validated**, with operational **NO_GO**. |

## Fix-forward requirements

1. Compress with `gzip -9 -n -c` on the staged source. The `-n` flag avoids encoding the original filename and timestamp; merely redirecting the mtime-zero file through stdin still exits 2 on this GNU gzip. Preserve the uncompressed source hash, and test an mtime-zero fixture under `pipefail` so a warning becomes a regression failure.
2. Supply and verify the staged A125 callback in the failure-safe recovery; retain exact held-stop proof and selective payload cleanup after any failure.
3. Admit the already stopped #751 pair only from the exact A169 receipt and immutable prior proof before the independent 20:00 ART close, preserving PAPER/0, disabled operator and all 16 timers. Never restart the failed #751 image; after the cutoff require a fresh postclose route and proof.
4. Run full Predeploy V2 for the new SHA, verify exact artifact and host reserve, then collect the new SHA/tree-bound #624 owner literals and use the canonical two-parent Deploy. Keep host stress deferred by its exact owner decision and keep operational NO_GO.

The IOL OAuth refresh [#40748415682](https://github.com/mbalbo2023/Porota-trading/actions/runs/40748415682) succeeded without local port forwarding; this does not renew the stale host collector. IOL source freshness is an explicit AMBER alert under the previously agreed non-primary-source policy. Neither this observation nor a connected MCP read-only response authorizes orders.
