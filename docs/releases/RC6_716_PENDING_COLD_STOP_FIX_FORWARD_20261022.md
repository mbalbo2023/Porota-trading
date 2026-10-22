# RC6 #716 — pending PAPER caucion and COLD clean-stop continuity

## Incident and root cause

The exact #715 candidate `d1f9691404c4a9145e91a95ca1f2bbf9b137d138` passed Predeploy V2 [#39862817991](https://github.com/mbalbo2023/Porota-trading/actions/runs/39862817991): 4,549 governed tests, zero failures/errors/skips/xfails, five native containers `0|false`; artifact `12462969781` digest `sha256:021d3b29364e899092abb9f18c97d53db64c789ca7f222f468c04f44e3b77df5`. Merge #715 produced `e38a10b67ba243b25551f4004e870e9079569a6b`, tree `caf300b98e7392edad0422b6a7dbb3641959f21f`.

Deploy V2 [#39865578392](https://github.com/mbalbo2023/Porota-trading/actions/runs/39865578392) admitted `CLOSED_CAUCION`, drained cleanly, transferred and installed the bundle, then failed in `rc6_failed_cold_fix_forward.py` with `COLD_ARCHIVED_RECOVERY_NOT_PROVEN`. Its failure cleanup recorded `FIX_FORWARD_VALIDATION_REQUIRED`, zero real orders and no PPI Watch access; staging cleanup recovered 286,085,120 bytes, and a separate scheduler restoration check failed because the owned runtime was stopped. This is **not** validated runtime.

Read-only host Actions [#39866095743](https://github.com/mbalbo2023/Porota-trading/actions/runs/39866095743) confirmed both owned containers exited with code 0 on the same prior image, one OPEN PAPER caucion with maturity `2026-10-23T17:00:00-03:00`, reconciled allocation and auditor source SHA `b1d711899a5c85f4efc72e2632a9d81cdddf0a6dc71dee82d5c42923b5639865`. The live COLD status is `FAIL_CLOSED`, `cold_audit` `ValueError` line 360 at `22:32:42.761654Z`, 4.302 seconds before the observer's clean exit at `22:32:47.063944893Z`. The existing COLD cancellation classifier recognized flat, postclose and intraday caucion stops, but omitted this new exact pending caucion mode. The next Deploy V2 also needed to adopt the installed, stopped receipt with `allow_pending_caucion` in its read-only preflight.

## Fix and guards

- Admit COLD line 360 only for this exact pending caucion, its maturity, complete row/allocation receipt hash, reconciled allocation, audited source SHA, PAPER/zero real orders and a clean Docker stop within 15 seconds of the fail-closed write.
- Carry that proof to the next candidate only when the predecessor's durable receipt hash, exact container identities, stop evidence, caucion receipt and `fix_forward_from` all agree. Altered hashes, identity, timestamp, line or mode fail closed.
- Preflight the stopped deployment with the *candidate* auditor in memory, adopt the prior stop receipt, and recheck the same pending ledger before host installation. Keep the caucion open to actual maturity, with no simulated settlement or real order.
- COLD2's later archive boundary receives the same exact stopped pending receipt. It rechecks the allocation and full-row hash at each boundary; its closed/empty path now excludes a pending caucion explicitly. Archive admission accepts the exact pending maturity and auditor source while preserving PPI, resource and Docker identity checks.

Focused local tests cover the exact timestamp and negative mutations, plus workflow wiring and the pending PAPER ledger. The new candidate requires its own complete Predeploy V2 artifact, exact #624 authorization and Deploy V2. Integral RC6 remains `NO_GO` until original historical lineage and OPEN capacity evidence are available, independent of this deploy incident.
