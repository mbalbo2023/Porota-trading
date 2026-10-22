# RC6 storage, historical authority and code retirement · candidate policy

Status: **candidate only**. No runtime deletion, migration or reprojection is authorized by this document. PAPER/SHADOW only; PPI Watch outside scope; backups remain disabled by operator.

## Database identity and retention

- Preserve original provider evidence, revisions, causal clocks, attempt ledgers, conflicts, PAPER ledger and audit receipts. A repeated payload after an intervening revision can be a valid ABA event. Do not call it a duplicate by hash alone.
- A deletable duplicate requires the same full monetary identity (`symbol`, family, market, currency, settlement), date, price and adjustment bases, source, payload hash, previous version and first known clock, with all foreign keys and downstream PIT references accounted for. Exact database uniqueness constraints are the first prevention gate.
- Collect read-only page size/count, freelist, DB and WAL bytes, index inventory and bounded samples using `scripts/rc6_storage_wiring_audit.py`. Its sample cannot certify whole-database uniqueness or justify a DELETE. Do not use `VACUUM`, `REINDEX`, checkpoint or a full table scan during OPEN.
- Before a space-reclaim plan, compare storage across like-for-like market phases, identify writers by PID/cgroup, separate write amplification from file growth, and demonstrate no lost lineage or required retention. Use a consistent **offhost copy**, manifest/hash and bounded budget for any deletion rehearsal; never mutate the production DB as an exploratory step.

## Historical policy and reprojection gate

- `RC6_HISTORICAL_AUTHORITY_V2_20261022`: validate exact identity, session, PIT clock and the consumer's minimum quality before PPI → IOL → BYMA/A3 → governed fallback. `CLOSE_ONLY`, `FULL_OHLC`, and `FULL_OHLCV` are distinct; volume needs a verified quantity, nominal or contract unit.
- The canonical pointer contains a **materialized numeric `source_rank`**. A code change alone cannot retroactively repair prior rows. Compare every affected exact series to the newest eligible revision per source at the same `as_of` on a verified offhost copy, record old/new pointer and hashes, check conflicts, and prove PIT cutoffs before any promotion.
- Legacy `history_versions_v2` without currency, price basis, adjustment basis and `version_known_at` cannot be silently assigned these values. The observed annual legacy rows remain quarantined until original provider evidence supplies them by row. The 512 MiB default copy budget must not be bypassed on the live host. No mass reingestion or postclose PPI unlock.
- A promotion needs a separate approved, idempotent fix-forward migration with exact SHA/tree, disk/RAM admission, atomic swap plan, read-only before/after counts and foreign-key checks, fail-closed partial outcome, and direct runtime evidence. No rollback and no overwrite of original revisions.

## Python/module retirement

- Static imports alone do not define reachability: inspect workflow triggers on `main` and deploy branch, systemd units, shell/CLI entrypoints, dynamic imports, tests and external callers. The existing `rc4_module_inventory.py` and the bounded wiring report provide review candidates, not deletion authority.
- For a proposed removal, record the call graph and runtime/job evidence, a replacement or justified loss of functionality, exact affected paths, package size before/after and tests for missing imports/entrypoints. Retain unique audit evidence and open PR heads. A module kept for emergency use is explicitly classified, not removed for lack of normal runtime imports.
- Delete only through an isolated PR and exact artifact tests. A code deletion does not reclaim old Git history or host disk by itself; cleanup of referenced images, DB or volumes remains prohibited.
