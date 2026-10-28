# RC6 A171: COLD2 receipt handoff after a held stopped deploy

## Incident and impact

Canonical Deploy V2 [#40765366297](https://github.com/mbalbo2023/Porota-trading/actions/runs/40765366297) promoted frozen candidate `33ea6d365c47d415fc5480a23b1338c208c2a29f` from successful Predeploy [#40761278095](https://github.com/mbalbo2023/Porota-trading/actions/runs/40761278095). The host passed identity, owner, PAPER, scheduler, disk, transfer, bundle and image checks. Promotion failed before runtime replacement at `rc6_cold2_apply_guard.py:receipt_for` with `COLD2_QUIESCENCE_RECEIPT_INVALID`. The deploy is RED, and operational NO_GO remains in force.

The A143/A169 inherited clean stop wrote an installation receipt without `inflight=ABSENT_OWNED_RUNTIME_STOPPED`, although COLD2 required it. After adding that field, the generic CLOSED COLD2 branch would still reject the exact inherited `STOPPED/BOOT_CALENDAR` PAPER state. Neither a missing field nor a stopped BOOT_CALENDAR state is evidence of healthy runtime.

## Verified containment

Failure cleanup marked the candidate `FIX_FORWARD_VALIDATION_REQUIRED`, held 16 timers, and removed only attempt-owned staging. Free disk rose from 5,639,102,464 to 5,928,448,000 bytes (+289,345,536). Frozen image tags were retained because they alias the stable image; shared layers, backups and data were not pruned. The separate read-only [#40766759373](https://github.com/mbalbo2023/Porota-trading/actions/runs/40766759373) verified Droplet `594077619` in `nyc1`, receipt SHA `2052b78c67f4385e662caeb1448d357f804d0605e6f2c710447b4130fa1df0e8`, the original two stopped Docker IDs with exit 0, PAPER `STOPPED/BOOT_CALENDAR`, real orders 0, 16 held timers, an active/enabled IOL OAuth guardian, absent attempt barrier, PPI Watch state-only, and 5,928,427,520 free bytes. It made no host mutation.

## Fix-forward and IVAC

The new A143 receipt sets `inflight` only after both stops, the flat PAPER book and the operator hold are proven, and requires the exact value at replay/install. The COLD2 archive path accepts this one pinned inherited stop as intraday-flat NO_GO only while the original A169 receipt, the failed A170 receipt hash, both Docker identities, the exact A151 hold, PAPER book and critical broker still agree. Each archive boundary rechecks admission. The prehost classifier, scheduler adoption, drain and install additionally bind the installed A170 source and loaded Docker tags before a host write. The prior A170 receipt remains unchanged, including its missing field, so evidence is not rewritten to make it pass.

Regression cases cover positive handoff into `receipt_for`, partial and altered stops, missing/forged `inflight`, changed intermediate receipt, source/image identity, held timers and COLD2 intraday-flat admission. The focused 295-case suite passed locally; full Predeploy V2 and a new frozen artifact are separate required gates. No rollback, real order or PPI Watch mutation is permitted. Stress remains deferred by the exact owner approval; NO_GO is not lifted by a deployment.
