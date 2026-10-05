# RC6 historical convergence: review and operations evidence

This change covers AUD-468-06/07/08/09/10/11/12/13/17/18/20/21 and U24. The
historical store contains research evidence. Its coverage and SHADOW features
have no READY_PAPER or execution authority. No production database, host,
service or PPI Watch data was used by this work.

The controlled implementation is `cu_history_store_v2_hf6.py`, its PPI,
Data912 and A3 adapters, close-only evidence, SHADOW readers and the SQLite
export copy helper. Root integration owns the observer, main engine and
scalping revision consumer. Source-version semantics for AUD-468-19 use the
same rule: append a changed revision with an immutable known_at, select the
latest revision available at the cut, then evaluate its quality.

## Contracts for callers

Identity is `(symbol, instrument_type, market, currency, settlement)`. Currency
is an explicit quote cash currency, including the distinct USD_MEP and USD_CCL
bases. There is no ARS fallback. For A3 CEM, reference/underlying currency is
separate from quote currency. Price series also include price_basis and
adjustment_basis. RAW uses RAW_NO_ADJUSTMENT; an adjusted row without action
provenance remains an isolated UNKNOWN_ADJUSTED series. Source conflicts are
recorded without averaging prices or replacing incomparable bases.

`Candle` retains the legacy positional field order and accepts explicit
`currency`, `price_basis`, `adjustment_basis`, `volume_kind` and `provider_at`
keywords. `append_many` validates every row first, caps a batch at 10,000 and
commits versions, canonical rows, deduplication references and its attempt
ledger in one transaction. A committed explicit attempt_key is replayable
after another correction without resurrecting the earlier value. A→B→A is
three causal versions when it is a new observation.

`observed_at` and `version_known_at` remain the original availability time.
An identical retry updates last_checked_at. Instants are timezone-aware UTC
with microseconds; comparisons do not use floating-point epochs. Future
availability, future/session-invalid days, nonfinite/invalid prices or volume,
partial OHLC and inconsistent OHLC fail before row writes. BYMA holidays are
audited for 2026; other years carry HOLIDAYS_NO_VERIFICADO. Legacy writes share
the numeric/session gate but remain advisory when their identity is missing.

Provider metadata is part of revision identity: unit, multiplier, turnover,
currency provenance or publication changes/removals create a causal version
even with identical OHLCV. Only engine history_quality,
session_calendar_status and copy_migration_currency annotations are excluded.
Existing native payload hashes remain immutable; compatibility/replay compares
the actual stored payload when an earlier hash lacks metadata coverage. It
never supplies a missing old unit or moves first_known_at.

`ingest_ppi_payload(..., currency=..., history_store=...)` returns its valid and
rejected counts after the universal sink gate. PPI close salvage is restricted
to OHLC/volume defects; it cannot rescue identity, date, clock or conflicting
daily rows. A full-key saga retains first/last attempt clocks and retry count.
The observer ledger and rejection details are separate physical commits.
Checkpoints may lag a completed physical step after a crash; the actual row
counts and idempotent replay provide recovery evidence.

`read_as_of(connection, ..., currency=..., as_of=..., price_basis='RAW')`
selects the latest known revision per source/day and then comparable source
authority. Equal source rank uses the latest UTC instant and a stable lexical
source tie. SHADOW candle features select a single exact series, count distinct
bars and evaluate latest quality after revision selection. A latest conflict
revokes a bar; earlier valid revisions and unrelated series do not rescue it.

`collect(store, quote, at, *, history_store=...)` reads only the managed
historical path via a private copy, with a one-second deadline. It never calls
the historical write adapter's connect(). An already query_only SQLite
connection from a private snapshot may be reused for one evaluation cycle.
The aggregate delta remains SHADOW/OBSERVE_ONLY.

`coverage_inventory(connection, identities5, *, as_of, consumer, session=None,
minimum_rows=30)` retains the exact cohort digest, denominator, duplicate
count, currency groups, session coverage and cut. It reports research coverage
and minimum observations separately, with readiness_implication=NONE and
runtime_verified=False. Root observer ingestion consumes this canonical
inventory after successful sink commits; its legacy three-key cache is
nonbinding.

Data912 accepts five-field targets. Its ticker/family request cannot establish
multiple currencies or settlements for the same request; the complete target
cohort is checked before a batch limit and ambiguous requests fail without
fetching. Reported provider currency conflicts are rejected. Absent provider
currency remains REQUESTED_IDENTITY_ONLY provenance and must not be presented
as independently verified provider currency. Volume UNKNOWN remains unknown.
IOL montoOperado is preserved as cash_turnover, separately from volumen.
Liquidity needs explicit currency and a money/quantity unit. Nominal and
contract volume require a contract multiplier; unknowns do not produce cash.

## Finding-to-regression mapping

All new cases use temporary stores and forbid network connections. Test names
below refer to `tests/test_rc6_history_convergence.py` except U24.

`history_convergence/owned_exact_node_matrix.json` gives each owned finding's
RCA, final fix paths, exact collected pytest node IDs (including each parameter
case), PASS receipts and external limits. It contains thirteen assigned
findings, six separately declared autocorrections and the three
restored owned scenario IDs. Its 95 distinct native guard nodes are verified
against the 268-test broad receipt plus one separate sampler PASS (269 distinct
nodes); they are not interchangeable with scenario or
finding counts. The matrix includes verified seed/current hashes for all
twelve #344 paths from the independent UX preservation receipt. The core
phase review's exact integration HEAD at test start was not recorded, so its
41 PASS checks must not be presented as exact-candidate CI.

| Finding | Current regression/control |
| --- | --- |
| AUD-468-06 | test_AUD06_adjusted_secondary_never_overwrites_raw_and_action_bases_are_separate |
| AUD-468-07 | test_AUD07_currency_identity_is_exact_and_missing_currency_is_rejected; migration/quarantine; A3 quote/reference currency; Data912 cohort currency collision before batch/fetch |
| AUD-468-08 | test_AUD08_retry_preserves_version_known_at_and_only_updates_check_clock |
| AUD-468-09 | test_AUD09_offsets_order_absolute_microsecond_instants_and_replay_stays_causal |
| AUD-468-10 | parametrized universal invalid-row gate, legacy numeric batch rollback, duplicate daily PPI quarantine with exact row indices, nonlist typed failure |
| AUD-468-11 | future-known revision exclusion, ambiguous legacy exclusion, explicit dedicated path without calling its write connection |
| AUD-468-12 | distinct latest bars, conflict revocation, causal old/new cut and one exact series rather than mixed bases/currencies/sources |
| AUD-468-13 | test_U_IOL_ARCHIVED_LIQUIDITY_keeps_money_and_quantity_separate |
| AUD-468-17 | 24 concurrent writers and enforced database deduplication; real DB lock and recovery |
| AUD-468-18 | whole-batch trigger/validation rollback, actual process exit79, ABA revisions, full/close/observer failure recovery and committed replay after a later correction |
| AUD-468-20 | exact row rejection reasons, partial completion false, four-key target rejection, bounded retry taxonomy for 429/timeout and no raw secret diagnostics |
| AUD-468-21 | exact cohort, known cut, currency/session/consumer differences and no READY implication |
| U24 | tests/test_rc6_history_snapshot_copy.py: WAL without SHM; unchanged bytes/mode/inode/times; read-only directory; sidecar aliases; source changes; byte cap while growing; finite deadline; platforms without no-atime capability |

The three historical controls removed between the original 96-scenario suite
and the 90-scenario candidate comparison were explicitly rerun:

| Original scenario ID | Preserved current check |
| --- | --- |
| H_LEGACY_ASSET_CLASS_COLLISION | collision raises and the original family/price remains intact, with transaction rollback |
| S_CRASH_AFTER_VERSION_INSERT | actual child exits 79 after INSERT, restart has zero partial versions/canonical, exact retry yields one |
| U_IOL_ARCHIVED_LIQUIDITY | price 100, amount 100000, quantity 1000 returns cash 100000 for explicit ARS, with unknown/different currency unavailable |

`history_convergence/restored_controls.json` stores the three PASS receipts,
original scenario results, node names and the original harness SHA256. The
remaining three omitted controls have root/UX/SRE owners. These fixtures do
not estimate runtime cadence, win rate or economic edge.

Fixture changes preserve their original assertions. Old Candle fixtures now
declare currency; adjusted/raw assertions use separate comparable series.
The preopen fixture now uses the actual writer and true revision clocks. Its
eventless case retains provider_at=None and volume UNKNOWN. Its correction is
CUT+1 second, retaining the original causal check without ingesting a future
wall-clock timestamp. Archive-count fixtures use completed local business
sessions rather than weekend/future calendar rows. The exclusive-lock export
fixture now performs a dirty write so a nonempty rollback journal causes a
real fail-closed copy. No ignores or skipped requirements were added.

The twelve files protected by #344 are ten byte-for-byte files and two declared
fixture/expectation reconciliations in this convergence. In
`tests/test_rc4_acceptance.py`, the Candle fixture explicitly declares ARS in
metadata. Root's `tests/test_candle_archive_v17.py` retains the missing-identity
denial, empty-history and event assertions, updating its expected marker from
HISTORY_MARKET_IDENTITY_MISSING to HISTORY_FULL_IDENTITY_MISSING. All original
assertion purposes remain. This report does not claim twelve literal files.

## Independent preopen review and correction

After the first history convergence, independent review found two additional
preopen defects: direct source SQLite mode=ro created SHM and changed atime on
frozen main+WAL without SHM; adjusted Data912 volume9999 replaced RAW PPI
volume100 because adjusted=True preceded source rank. These are separately
declared NEW_PREOPEN_U24 and NEW_PREOPEN_AUD06, rather than retroactively
presenting the first convergence as complete for this additional reader.

Both exact native `ShadowRuntime.tick` regressions were RED with the prior
reader and GREEN with the correction. The shared readonly_copy helper now
guards the source. Named SQL columns bound native JSON to 32 KiB per row and
all JSON reads to 16 MiB. One shared two-second preopen deadline and the 512 MiB
source ceiling fail closed; data are published only after successful context
exit. Latest revisions are selected before metadata quality, exact currency
comes from the native row and provider_at/version_known_at remain explicit.
Only RAW/RAW_NO_ADJUSTMENT participates in the comparable preopen profile.
An oversized historical source cannot resurrect old revisions or reconstruct
partial authority. Source-rank ties use a stable lexical source choice.

The native worker cut is Friday 20:00:00 UTC. Its fixture's final daily receipt
at 20:00:02 is excluded, leaving nineteen causal rows; the standalone 20:00:05
cut includes twenty. No clock was backdated to inflate coverage. An unused
DELETE writer lock permits a verified committed byte copy; a dirty nonempty
rollback journal instead yields SOURCE_SNAPSHOT_BUSY. The existing bounded
and empty-result assertions are retained with that sanitized taxonomy.

`history_convergence/preopen_autocorrection.json` retains the exact two RED
nodes, six new GREEN controls, all native preopen/caller receipts and source
hashes. The broad historical/caller run passed 249 tests. The independent core
phase review inspected ec9ea4a5 and passed 41 native regressions, without
finding another phase defect in that inspected scope. Neither result proves
live deployment or capacity. Strict all-stat source invariance applies to
historical/export/preopen copies; active trading SQLite readers have their own
SQL read_only/query_only and WAL locking contract. This change does not claim
all-stat invariance for every active runtime reader or copy gigabytes on an
unmeasured runtime cadence.

## Native large-reader and revision-lookup correction

`history_convergence/scale_deadline.json` retains the exact native RED/GREEN
receipts and final source/test hashes. The previous-revision lookup inspected
10,263 SQLite operations after 2,000 unrelated same-provider identities,
compared with 263 operations before them. The full series/source/known/id index
removes that scan; the native regression checks query work rather than a
hardware-specific writer speed threshold. A completed 60,000-version fixture
now writes six actual 10,000-row transactions in 15.001 seconds in the final
run. An exploratory earlier generator reached 222 seconds at 30,000 rows and
was interrupted only on its own temporary source.

SQLite progress callbacks did not bound Python row validation or final
selection. Native row loops now check the same monotonic processing deadline
and retain 100 ms of the two-second total target for scratch/object cleanup.
Revision/authority selection uses one window over immutable IDs and exact
series fields at the UTC known cut; wide values and bounded metadata are
fetched only for its winners. A later bad winner cannot resurrect an older
acceptable version. Source authority still compares only the same five-field
identity, date and RAW/adjustment basis.

The final broad receipt is 254 PASS, with no skips. It includes 12,000 native
identities, 60,000 versions and exactly 12,000 real September 30 bars, with five
revisions per bar. A cold latest-cut read returned all 12,000 latest bars in
0.988 seconds; an earlier microsecond PIT cut returned all 12,000 prior
revisions in 1.343 seconds. Both have no source truncation/unavailability.
Their one-session-per-identity coverage is not five sessions or a fabricated
72-bar history. A native negative with bounded CPU work on each real row
exhausted the actual clock in 1.982 seconds, after 1,834 validations. It returned
no partial history and explicitly reported TIME_BUDGET_EXHAUSTED and
NO_VERIFICADO. All three nodes compared the full source member inventory,
hash, mode, size, inode and atime/mtime/ctime before and after; SQLite never
opened the source. This fixture contains MAIN only; WAL-with/without-SHM
invariance remains covered by the separate native snapshot/tick controls.

The artifact also preserves the intermediate two-window implementation's
isolated PASS and broad-run failures. Its insufficient timing margin was
corrected before the final one-window receipt. Exploratory earlier timings
without recorded exact source hashes are explicitly marked as such and are
not exact-candidate CI. Measurements describe this offline environment;
production size, contention, cadence and capacity remain NO_VERIFICADO.

`history_convergence/dry_run_exact_index.json` reruns all three copy migrations
and eight actual physical crash/resume stages with the indexed writer. Its
full source inventories remain unchanged and its quarantine/count/semantic
digest results reproduce the prior committed dry run. The original receipt is
retained. No production snapshot, provider request or runtime switch occurred.

## Semantic metadata and resource sampler correction

Independent review after the indexed/deadline correction demonstrated that a
SHARES→UNKNOWN metadata-only correction retained the original SHARES version.
The native RED receipt also covers quantity unit/multiplier, volume semantics,
turnover value/currency, provider currency provenance and publication clocks.
The digest now includes provider metadata. Its removal and A→missing→A changes
are causal versions. Typed identity, basis, quantity-kind and provider event
fields were already included and remain protected.

`history_convergence/semantic_metadata_revision.json` records 14 exact semantic
guards, source/test hashes and the full native receipt. The actual preopen
reader returns volume10 SHARES before the correction's known cut; after it,
the source row is UNKNOWN and median_volume has zero samples, value=None and
NO_VERIFICADO. Both reads preserve source hash and every recorded stat/member.
Compatibility tests retain old payload_hash and first_known_at on an identical
retry, replay the original attempt after a later correction without restoring
SHARES, and reject a changed semantic payload on the same attempt_key. Missing
old units are never backfilled from newly supplied unit metadata.

The final broad run is 268 PASS with no skips. Its large cold positive/PIT reads
take 0.934/0.821 seconds and its actual CPU deadline negative takes 1.935
seconds, still without partial history. The earlier indexed receipt is retained
as a dated software control; the latest matrix points to the final receipts.

The physical rerun exposed a separate sampler race: deleting private scratch
during Path.rglob descent could stop the periodic disk thread while the old
probe still printed a receipt. The intermediate resource receipt is explicitly
rejected. The sampler now handles that directory race, synchronizes peak
updates, reports unexpected failures and requires a completed thread before
publishing costs. Its native RED→GREEN guard is a separate one-test receipt.
The accepted rerun completes 176 samples with no errors, three copy migrations
and eight actual crash/recovery stages. `dry_run_semantic_metadata.json` retains
unchanged full source inventories. Its repeated new-copy content digests match
each other; they differ from the earlier digest because full content includes
the newly generated metadata-aware payload_hash. Existing source hashes are
not rewritten. Production capacity remains NO_VERIFICADO.

## Copy-only migration and physical recovery

`migrate_copy(source, new_destination, *, currency_map=None, seconds=60,
max_source_bytes=512*1024*1024, max_rows=1000000)` never opens SQLite on source.
It transports verified main+WAL bytes into scratch, backs up the copy and
rebuilds only the new destination. Destination main/WAL/SHM/journal paths must
all be new. Old tables and unrelated evidence remain preserved. The reviewed
four-field currency map is frozen and hashed. Explicit row currency may
resolve a multi-currency cohort only when it belongs to the reviewed mapping.
Missing, ambiguous, conflicting or invalid rows go to quarantine with original
version-ID mapping and provenance. Legacy close-only tables are also migrated.

The snapshot helper hashes every source member twice, compares inode/mode/
size/times and preserves atime with O_NOATIME. It requires regular single-link
files with no path/sidecar symlinks. SHM is verified but not transported; SQLite
rebuilds it only in private scratch. A nonempty rollback journal, concurrent
change, byte-budget excess, unsupported atime preservation or deadline failure
rejects capture. Growing files cannot write beyond their inventoried size.
`readonly_copy(validate=False)` omits quick_check for bounded UI reads while
retaining the filesystem coherence guards. Its omitted deadline defaults to
ten seconds; NaN/Infinity deadlines are rejected.

The executable `history_probe.py` has no source-path option. It creates only
synthetic temporary files, forbids network access and exercises the actual
writer/migration APIs. `history_convergence/dry_run.json` records full
main/WAL/SHM inventories before/after, including hash/mode/inode and
atime/mtime/ctime, semantic destination digests and physical-stage counts.
The selected sources are filesystem copies that SQLite never opens.

In the recorded run, each of three migrations retained nine legacy full
versions, migrated four, quarantined five and produced three canonical series.
Three legacy close versions yielded two migrations and one quarantine. WAL
with SHM, WAL without SHM and repetition produced identical semantic content;
the same destination was rejected without modification. A process exit88
after the first migration commit retained a partial destination. Recovery used
a fresh destination from the unchanged source, reproduced the final content
and made no provider request.

Eight actual child exits cover version INSERT, full commit/checkpoint, close
commit/checkpoint, observer attempt commit, rejection commit and final saga
checkpoint. Every resumed/repeated ingest produced one full version, one
close-only version, one observer attempt and one rejection. Diagnostic reads
of a crashed hot journal recover only a second private copy. The crash image
inventory remains unchanged during diagnostics.

The artifact records wall/CPU cost, OS peak RSS, Python allocation peak and
disk occupation sampled every 5 ms plus phase captures, including two actual
local synthetic evidence bundles. These are fixture measurements. Production
image size, reserve, dataset size and runtime capacity are NO_VERIFICADO.

## Operational plan and remaining external evidence

1. Obtain an authorized frozen offline main/WAL snapshot and explicit catalog
   currency mapping for the same cut. Retain its complete member inventory and
   digest before any migration. Do not infer quote currency from a suffix or
   reinterpret CEM reference currency.
2. Measure free staging bytes and the actual source, destination, two concurrent
   bundles, container image and operator reserve. Require
   `free >= copy + destination + 2*bundle + image + reserve` using measured
   values and an explicit growth margin. Missing values block the capacity
   claim. Verify financial cash/fee reserves independently by currency using
   the financial contract owner's evidence.
3. Run migrate_copy once on a new isolated destination under audited time,
   byte and row ceilings. Retain legacy/quarantine/map tables, mapping digest,
   source/destination inventories, integrity result and exact count deltas.
   A crash leaves an unpublished partial copy; preserve it and restart in a
   fresh destination from the same immutable source. No provider backfill is
   part of this migration.
4. Recompute PIT comparisons and coverage for the same exact consumer cohort,
   currency/basis, session and cut. Keep missing source units, calendars,
   corporate actions and provider identity as unknown evidence. Do not reuse
   the October 1 four-key audit denominator as current five-key coverage.
5. Supply the concrete copy and code revision for root review before any
   runtime activation. An old monetary schema deliberately fails closed with
   COPY_MIGRATION_REQUIRED. This work has not switched a live database path or
   deployed a service.

No complete current historical dataset was supplied. Current coverage, gaps,
source discrepancies, delisted instruments, corporate actions, real decision
cadence and observed consumer benefit therefore remain NO_VERIFICADO. The
2026 BYMA calendar does not establish calendars for earlier years. Fixture
GREEN proves the specified software controls; it does not prove market P&L,
Sharpe, drawdown, break-even performance or production capacity.
