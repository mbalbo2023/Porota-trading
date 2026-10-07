# RC6 error registry and permanent-guard ledger

Status at creation: `DESARROLLADO` on an isolated governance branch. Integration,
workflow execution and runtime deployment remain `NO_VERIFICADO` until exact-SHA
evidence says otherwise.

## Closure rule

An incident is closed only after this complete chain exists:

`ERROR -> RCA -> FIX -> GUARD -> TEST/PREFLIGHT -> EVIDENCE`

A manual cleanup, a chat explanation, a rerun, or a single lucky PASS is not a
closure. A repeated failure must be linked to its prior incident ID. Unknown,
missing, stale, RED or pending evidence blocks the expensive next stage.

## Active ledger

| ID | Failure and evidence | RCA | Permanent guard / next proof | State |
|---|---|---|---|---|
| ERR-RC6-015 | Local Codex filesystem reached zero free bytes on 2026-10-06; on 2026-10-07 a new focal reached 100% and returned `RETENTION_NO_SPACE`. Evidence: #471/#473 comments 6026803239, 6026849744 and 6048816683/6048817016. | Heavy producers retained large generated fixture trees. Prior handling reclaimed space but left no mandatory capacity admission, owned namespace or receipt. | Run `scripts/rc6_heavy_test_preflight.py` with a measured comparable peak plus 4 GiB residual reserve and 10% free inodes; unique namespace; cleanup only owned generated fixtures; preserve evidence/unowned paths. Integrate this before every local heavy producer. | `GUARD_DESARROLLADO`; integration pending. |
| ERR-RC6-016 | Commit `f6ce6b87` caused `CLOSURE_GUARD_INVALID`; Predeploy collected no tests. | Finding 20 used six file names where the validator required declared pytest node IDs (`tests/file.py::test_name`). | Inventory/admission must finish before full Source/test work; malformed node IDs remain a negative regression case. Corrected by `dfc240a4`; exact integrated guard evidence still required for the next candidate. | Fix in GitHub; guard evidence pending. |
| ERR-RC6-017 | BIG workload (12,000 instruments / 60,000 observations) repeatedly exceeded the unchanged 90-second budget. Clean focal311 on local candidate `7fc6731d` was 939 PASS / 1 BIG RED at second `storage_encode`, child CPU 88.294 s. | Dominant measured cost is publication/storage capture/encode, not family routing. A single isolated 81.789 s PASS lacks safe headroom. | Preserve workload, assertions, 90 s, 2 GiB and 128 MiB evidence limits. Require local/focal qualification target <=75 s, then exact-SHA same-runner canonical evidence. An isolated PASS above 75 s is diagnostic only. | Optimization under qualification by active owner; no PASS claim. |
| ERR-RC6-018 | Retained namespace measured 124,358 entries; another partial check crossed 100,000. | Aggregate retention was not attributed and bounded before promotion. | Require complete post-FIN owned-namespace count <=100,000 with scope/method receipt. Missing/partial counts block. | Open. |
| ERR-RC6-019 | Horizon stopped after 1,049/1,202 ticks with `RETENTION_ARCHIVE_CAPACITY_REACHED` at 512 MiB. | Archive-capacity implementation was unchanged by the later browser/source sharing patch. | Complete the original 1,202-tick horizon with archive capacity proof; do not substitute process-close evidence for retained-capacity evidence. | Open. |
| ERR-RC6-020 | Material and Predeploy started from the same PR synchronize event; a full expensive run could begin before focal evidence was known. | Workflow ordering did not enforce same-SHA focal receipts as prerequisites. | Full Predeploy must depend on GREEN admission, Source preparation, focal311, focal312, retention and BIG-browser receipts for the same SHA. Missing/RED/pending blocks. | Active owner has an unpublished proposal; integration pending. |
| ERR-RC6-021 | Runner-performance assumptions were not recorded consistently. | Configuration discussions mixed local Codex capacity, actual Actions observations and documented GitHub runner class. | Record label, CPU count, memory, filesystem and load in receipts. Canonical free public `ubuntu-24.04` baseline is 4 CPU / 16 GiB / 14 GiB. A faster diagnostic runner is not promotion proof by itself. | Policy developed; workflow integration pending. |

## Required evidence on every new entry

- UTC time, exact SHA/tree and owner/workstream.
- First failing invariant and stable signature.
- Runner/environment identity and resource measurements.
- Whether the attempt is valid evidence or quarantined infrastructure noise.
- Fix paths and semantic invariants preserved.
- Guard location, focused regression and artifact/receipt link.
- Explicit state from `PROPUESTO` through `VALIDADO_RUNTIME`.

This registry does not authorize deployment or any real-order route.
