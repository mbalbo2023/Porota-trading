# RC6 architectural reconstruction from remote evidence

Repository: `mbalbo2023/Porota-trading`. Workstream:
`WS-RC6-CONVERGENCE-468-469-470-20261005`. Integrator:
`CODEX_RC6_ARCHITECTURAL_RCA_20261008_1205UTC`.

## Evidence boundary

This reconstruction starts from PR476 remote commit
`dfc240a478cf08e0c6a9b0ba1b760307b09a9565`, tree
`192ea26348c8fe10db55857cdd6029edd8344708`. PR477 remote commit
`756d37b93aa26bb6395dac481bf3c2dda9d034d7` has five governance paths;
they are reconciled individually, without merging that PR. Product remains
`da697c6e6c2274579f9e4a112fabc4327475dd35`. Historical unpublished
`59ad88ce583383e915e1a051811786ec140e2afc` is unavailable through GitHub and
supplies no recovered code. Issue478 is POSTDEPLOY backlog only.

The integrator read issues471/473 and PR476/477 in full, including all311
PR476 commits, before writing. Acquisition receipts:
[#471](https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-6059477308)
and [#473](https://github.com/mbalbo2023/Porota-trading/issues/473#issuecomment-6059477667).
Fresh renewal receipts govern the lease; this document is not ownership authority.

## Verified causal tree

```text
Repeated equivalent Source consumers
  -> repeated main/WAL copying, hashing, verification and scratch acquisition
  -> capture charged to inconsistent query allowances
  -> sensitivity to fixture WAL checkpoint / open committed writers
  -> SOURCE_SNAPSHOT_BUSY or TIME_BUDGET_EXHAUSTED

Publication performs preparation and encode for multiple factual roles
  -> repeated Python capture traversal / room and volatility checks
  -> repeated matching / record encoding of identical native captures
  -> publication dominates the second BIG cycle
  -> BIG exceeds outer90s or lacks internal75s headroom

Generated fixtures remain retained across producer/test lifetimes
  -> cumulative retained bytes/entries rather than live-workload peak
  -> historical narrative17,299,099,648B; authenticated allocated17,298,976,768B
  -> exhausted filesystem; later RETENTION_NO_SPACE

Archive emits new exact compressed components on each operational clock
  -> whole page-pack CID changes with target/base/index/payload
  -> report/checkpoint frames also change, including after the CLOSED transition
  -> ten-hour retention cutoff has not reached the first PREOPEN cut
  -> legitimate zeroGC plus accumulated new bytes reaches512MiB before1202cuts
  -> RETENTION_ARCHIVE_CAPACITY_REACHED at1049 executed cuts

Heavy producers start before exact cheap/focal qualification
  -> expensive work discovers architecture and fixture defects too late
  -> incomplete RAW preservation obscures the primary failure

Remote Predeploy37674493598
  -> one BIG deadline FAILURE
  -> READONLY_SOURCE10_CHANGED during readonly-fixture teardown
  -> pytest INTERNALERROR / exit3
  -> execution stops at2490 of6215 collected identities
  -> collected/executed mismatch (downstream consequence, never waived)
```

The concrete remote Source10 member/stat mutation was not preserved. Delayed
allocation is a hypothesis supported by an unfsynced fixture constructor, not
an established remote attribution. The `st_blocks757840 ->757848` example in
[#471/6046286130](https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-6046286130)
is explicitly historical LOCAL evidence. Preserve all10 stat fields, prepare
new owned fixture bytes durably before freezing, and emit exact member/field
differences on any future violation. Never restore or omit changed fields.

Remote focal37674493603 closed actual physical FIN (269 descendant reaps,
ECHILD, exit3, no timeout/signals). Its primary abort RAW/JUnit was excluded
from the artifact; the primary cause remains `NO_VERIFICADO`. Keep
`FOCAL_COLLECTION_JUNIT_IDENTITY_MISMATCH` RED and preserve original RAW after
physical FIN even when logical qualification is RED.

## Original negative artifacts

ZIP SHA256 and CRC were independently checked; selected original RAW and a
member/hash index are retained outside the code tree. The Actions artifact is
the persisted source; the table does not claim a successful candidate.

| Run / artifact ID | Original ZIP SHA256 |
|---|---|
| [37674493598](https://github.com/mbalbo2023/Porota-trading/actions/runs/37674493598) /11506363002 | `8ad80f710a6ec9ef3ee26c073e9a8e48a85c629a58d9e1e44ce9759515ae5920` |
| [37674493603](https://github.com/mbalbo2023/Porota-trading/actions/runs/37674493603) /11506797953 | `beb504e95c68f8a920e2630b1e525c35806ba9528c53f6138b472f87c2962e35` |
| [37674493603](https://github.com/mbalbo2023/Porota-trading/actions/runs/37674493603) /11506983141 | `b3c3843a0eb38dc1e64500b94d812572197a4e4d3d17d0c3a14c3991defce348` |

The failed remote BIG measured publication45.0904s, including storage_prepare
34.9183s; storage_encode6.8195s and funnel8.9912s. The fifth CAPTURE event had
childCPU88.8030s. Inclusive nested values cannot be added to their parents.

## Capacity and Horizon attribution

The exact closed-retained diagnostic preserved by evidence-only commit
`71c47dac9f86b2d3a3913db8a01b2e1d459864aa` is
`raw/root-preparations/004-rc6-gov311-closed-capacity-diagnostic-20261006.json`,
SHA256`dec02e7d2d47614c7a8e6b978bcbf828f36961fcf4f2e5b3992baee8ecf4aa60`.
It measures17,298,976,768 allocated bytes,17,141,710,438 logical bytes and226,749
entries in a closed LOCAL full-governed retained namespace. It did not measure
the temporal maximum and is not an Actions runner result. The earlier narrative
17,299,099,648 differs by122,880B and is not substituted for this measurement.

Actions run37633249899, artifact11492830480 preserves a complete closed CI
retained inventory of5,976,211,456 allocated bytes and124,358 entries. Partial
311 enumeration ending at the entry cap is not an aggregate upper bound.
Comparable admission must identify the producer graph, runner origin, Source
and additional allocation bounds; an unknown comparison blocks before start.
The4GiB reserve is additional to the accepted comparison, never consumed as its
unproved workload allowance.

Actions [37633310327](https://github.com/mbalbo2023/Porota-trading/actions/runs/37633310327),
artifact11502637203 has verified ZIP SHA256
`5738a959b1483e46125b2a44a3b536067d6f5bf87b01cde68888c218023f0bb2`.
The original Horizon1200catalog/6000observations planned1202 cuts and executed1049,
ending RED at archive capacity. Peak observed archive535,810,048 allocated bytes,
3151 entries; live107,696,128 allocated bytes,188 entries; RSS402,898,944B.
At the failure, the GC cutoff12:18:30 precedes the first PREOPEN13:20:00.
Zero expirations is correct. Advancing clocks, reducing cuts, or raising the
512MiB archive quota would not close this cause.

Four small native cuts PREOPEN→OPEN→CLOSED→CLOSED restored all five original
members exactly and retained Source invariants through real FIN and authenticated
cleanup. Splitting the page-pack into16-page groups reused no CID and added
320–400B per cut before recipe cost. That variant was rejected without integration.
The third small CLOSED cut then produced projection13,306B, checkpoint91,500B
and report51,356B. Both transition and steady-state bytes therefore need proof;
projection-only estimates do not qualify full Horizon capacity. These are
development diagnostics, not a replacement workload or a material GREEN.

The six-cut native diagnostic used the canonical Horizon1200/6000 configuration
and productive read contract, then replayed the same persisted original bytes.
COPY/LITERAL storage reduced total allocated archive bytes18.9%, but did not
reduce the two steady CLOSED cuts. That result did not close sustained growth.
The subsequent narrow XOR opcode operates on authenticated, equal-length
original SQLite bytes, in64KiB C integer blocks. Its inverse restores original
bytes without regenerating SQLite or gzip. It is enabled only for projection
SQLite; checkpoint/report component sharing remains under the global physical
cost selector. Independent anchors, legacy decoding and the original maximum
dependency depth32 remain mandatory. A common-chain guard forces all candidate
members independent before a mixed-codec chain can exceed32, including when
generation numbering skips the modulo anchor.

The changed-code replay of those same six originals allocated11,730,944B with
the legacy writer and9,314,304B with the candidate (20.60% reduction). Its two
steady CLOSED cuts changed499,712→397,312B and462,848→368,640B. File/entry counts
were unchanged20/21; all five original member hashes and Source invariants
matched through original FIN and authenticated cleanup. Peak replay RSS was
155,320,320B. Inclusive build CPU21.042→22.327s over six cuts does not demonstrate
a CPU improvement. The replay result SHA256 is
`c502ac578a1f0a027cc083a832da2eaa204313e4294a18716028b3c57da536a7`.
OPEN steady growth and full1202 capacity remain `NO_VERIFICADO`; a savings
percentage on six early cuts is not a full-window capacity proof.

## Architectural fixes and permanent guards

* One thread/process-bound private verified primary Source image per tick.
  Consumers use separate read-only connections to that image. Genuine concurrent
  Source writes fail CLOSED; final verification and scratch cleanup finish
  before publication. Distinct history images require individual identity,
  reasons, aggregate live scratch admission and authenticated owner lease reuse.
  The primary private main/WAL files are sealed read-only and their inventory
  and SHA256 are checked before publication, within the same final VERIFY
  allowance. Private SHM is explicitly mutable SQLite coordination state;
  modifying private main/WAL is rejected even when the original Source is stable.
* `SourceReadContract` distinguishes CAPTURE, final VERIFY and each QUERY.
  Its productive document is fingerprinted in the worker configuration and
  actual capture receipts. Existing query allowances are preserved; a fixture
  override cannot diverge from an active worker contract. No sleeps or outer
  deadline changes are introduced.
* Fixture writer connections are explicitly committed and closed before WAL
  checkpoint/capture. Garbage collection is not quiescence authority.
* Native initialization adds an expression index on `julianday(event_at) DESC`.
  EXPLAIN guards require index search without a temporary sorting tree and
  compare original ordering, ties, timezone offsets and causal boundaries.
* Private bounded publication reuse and native-only traversal fast paths retain
  original callback/foreign-input fallbacks, cut limits, bytes/digests, atomic
  staging and fail-closed behavior. Planner prefix reuse retains all identity,
  event/stage and financial fields. Small-case savings are not material proof.
  Native traversal remains eligible when the optional replay snapshot exceeds
  its original container cap. Replay caps are unchanged; no large report gains
  replay authority merely because its values have native types.
  Exact string values use the original C ASCII JSON encoder only inside an
  authenticated native capture. Changes to callbacks, function/default/global
  identities, JSONEncoder operations, trace/profile hooks or native classes
  restore the original path. Twenty thousand Unicode/escape/surrogate values
  produced2,080,000 identical bytes and SHA256
  `21e564768f1fe0e778be671a24194d05a3de81ef809a606e381df2011a246a21`.
  Pure encoding CPU fell59.0%/48.5% in3.11/3.12. The synthetic128 publication
  showed a3.12 total CPU variation+0.6%; it does not prove dual timing headroom.
* Producers measure capacity on their actual filesystem, against a cited
  comparable peak plus4GiB and at least10% free inodes. Only authenticated own
  namespaces may be retired after actual FIN and sealed required RAW/hashes.
  Intraphase fixture retirement must prevent cumulative growth; end-only
  cleanup alone does not close ENOSPC. No global prune or foreign deletion.
* Push admits only G0/G1. Later stages require actual same-SHA/tree predecessor
  receipts, exact collection/execution/JUnit identities, native FIN, Source
  invariance, live capacity and canonical runner observations. Workflow success
  alone does not satisfy a gate. Identical blind reruns are rejected.

The known retained-allocation references exclude bootstrap/environment
construction. Current installed157-distribution environments and `.git` are
complete observations, not preparation temporal peaks. Four sdist build
temporary outputs, full-Git cloning/repacking preparation and full browser
ZIP/cache/extraction need actual comparable bounds. Capacity profiles remain
blocked while any component is unknown; the4GiB reserve cannot be relabelled as
an unproved allowance. The admission comparator binds the exact Git Source
manifest, selected cheap files, producer graph, model and original RAW bytes;
hash-only assertions or an unknown profile cannot authorize bootstrap/heavy work.

Physical fixture retirement is conditional on actual last-consumer teardown,
closed Source leases and positive original kernel infrastructure census. An
unavailable local own-children kernel interface deliberately blocks that
positive proof. Negative unit guards passing under this condition do not qualify
G1; canonical Actions must preserve its native positive probe and real retirement.

The productive contract fingerprint is
`5ee524e7370ecd908a590dc0aad007ff4858d3337adcf901231623a01ffc5358`.
The worker, preliminary reader, PREOPEN and material receipt checker consume
this contract. A tiny real BOUNDED_READ/PREOPEN/OPEN integration reproduced
the unregistered `direct_readonly_copy` PREOPEN consumer before the fix; the
registered `preopen` consumer and active contractual default close that gap.
No allowance is slept or added to the outer deadline.

| Productive phase / consumer | Seconds |
|---|---:|
| CAPTURE | 1.5 |
| Final Source + private-image VERIFY | 1.5 |
| Runtime QUERY | 0.5 |
| Metadata QUERY | 0.15 |
| Stages QUERY | 0.25 |
| Families QUERY | 0.5 |
| Lab QUERY | 0.25 |
| Entry signals QUERY | 0.25 |
| Funnel QUERY | 0.25 |
| PREOPEN QUERY | 2.0 |

The PREOPEN processing reserve remains0.1s and its default processing deadline
remains1.9s. Committed-but-open writers in its historical test fixture reproduced
real `SOURCE_SNAPSHOT_BUSY`; explicit close preserves the transaction and keeps
the concurrent real-writer guard CLOSED. These are product/fixture contracts,
not independent stress-only timeout overrides.

SQL-only diagnostics used the exact12000/60000 database without ticking or
publishing. Ten sequential reads per Python consumed the productive0.5s runtime
QUERY budget. Maximum query time was0.187068s on3.11 and0.178267s on3.12;
maximum full capture/query/verify/cleanup was0.277997s and0.273683s respectively.
All20 reads preserved the same semantic SHA256
`33ac67aec7ad398599c3d1e85a2a1acd528eacb645813eb830f5b241218d5f18`
and used index SEARCH without SCAN or TEMP B-TREE. This demonstrates the index
and budget contract, not BIG cycle headroom or remote qualification.

## Qualification boundary

Required order for one frozen SHA/tree: G0 -> G1.311/G1.312 -> G2focal311 ->
G3focal312 -> G4BIG -> G5required material -> G6full governed -> G7Predeploy /
build once -> G8exact artifact. The heavy literal BIG pytest case is deferred
from focal collection by an explicit coverage ledger and remains mandatory
in full automatic repository-root discovery; it is never skipped or xfailed.

BIG remains exactly12000 catalogue /60000 observations, complete OPEN cycle,
five factual PAPER exits, outer<=90s, internal qualification<=75s, RSS<2GiB,
evidence<=128MiB, retainedentries<=100000, Source unchanged and real cleanup.
Any RED stops expensive successors. New fixes require a new frozen SHA and
cheap proof, with no substitution of a stronger runner or smaller workload.

This document records code intent and verified historical RCA. Candidate
closure and G0..G8 remain `NO_VERIFICADO` until their exact native Actions
payloads are verified. `FINAL_CANDIDATE_ELIGIBLE=false` and deploy is blocked.
PAPER/SHADOW ONLY; PRODUCTION_PAPER / SIMULATION; real_orders_sent=0; real
routes BLOCKED / NOT_CALLED; PPI Watch untouched; FIX-FORWARD only. No
DEPLOY_OWNER is acquired and no deploy/runtime validation is claimed.
