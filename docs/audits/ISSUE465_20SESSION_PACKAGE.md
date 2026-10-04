# Issue #465 — sanitized twenty-session reproducibility

WORKSTREAM_ID: `WS-FIX-AUDIT-08-F-20261004`.
Isolated branch: `fix/issue465-historical-package-20261004`.
Input under review: `caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423` (#463).
Product base reconciled by the integration owner: `da697c6e6c2274579f9e4a112fabc4327475dd35`.

**Private historical master: `EXTERNAL_EVIDENCE_PENDING`.** No production DB,
runtime, host, PPI Watch, provider or private dataset was accessed. The actual
claims of 68 closes / 151 fills / 20 sessions remain unverified. All fixtures in
`tests/test_issue465_historical_package.py` are explicitly synthetic. Tooling
GREEN does not reproduce the inaccessible master or prove profitability.

## Authority and scope

Issue #465 §10 and audit #464 §5 / §17.6 require granular, sanitized evidence
and independent recomputation. The source repository's `paper_positions` and
`paper_fills` contain aggregate fill charges and embedded execution slippage;
they do not certify account fees, commission/rights/VAT breakdowns or provider
clocks for each fill. Missing data remains `NO_VERIFICADO` instead of being
estimated from a tariff, copied from capture time or invented.

`scripts/rc6_20session_evidence.py` is an explicit offline caller for the new
`rc6_audit_evidence` package. It has no default DB, environment credentials,
network transport, runtime child, schedule, migration or production writer.
It neither changes parameters nor grants trading authority.

This adapter covers the existing spot PAPER ledger, including its non-equity
families only with an explicit source cash multiplier. FUTURES, caucion, FCI,
cash/equity reconciliation and corporate actions require separate source
evidence and stay explicitly outside the package. No spot-accounting fallback
is used for a specialized ledger. Equities use the established share cash
convention; `units_per_lot` remains unverified rather than being inferred from
quantity step or multiplier.

## Export contract

The future authorized source owner supplies an offline PAPER snapshot, exactly
twenty explicit ART session dates, a new private output directory, and an
external high-entropy pseudonym seed. No calendar is reconstructed from fills;
empty sessions have explicit row counts and `source_coverage=NO_VERIFICADO`.
Missing dates, empty cohorts and incomplete source rows reject the export.

The exporter opens SQLite with `mode=ro`, `PRAGMA query_only=ON`, a SELECT-only
authorizer, a 25 ms busy timeout and a monotonic statement deadline. A snapshot
transaction requires `observer_state.mode=PRODUCTION_PAPER` and integer
`real_orders_sent=0`. Existing table names are required; views, writes, ATTACH,
DDL and alias paths are refused. A bounded LIMIT 2 check requires exactly one
canonical id=1 control row; duplicate or contradictory control rows reject the
snapshot. No schema initialization occurs. WAL readers
observe the existing source snapshot; the tool never checkpoints or modifies
its journal configuration.

The default budgets are 2,000 positions, 10,000 inspected fills, 32 MiB each for
source projections and package bytes, 64 KiB per row, and ten seconds for the
whole export including verification. All budgets have enforced upper limits.
SQL rows are capped with an extra sentinel row; SQLite checks oversized text
before returning it to Python. Every matching position and every inspected
fill counts against the budgets. A cap or interruption fails closed; a capped
prefix is never promoted as a complete package. Source queries, serialization,
hashing and recomputation share the export deadline.

Selection includes spot positions overlapping the interval from the first
session at 00:00 ART through the day after the final session at 00:00 ART,
exclusive. Original opening fills before the cohort are retained. Fills after
cutoff are inspected under the bounds but not exported. A position that closes
after cutoff is an `OPEN_AT_CUTOFF` survivor; future reported P&L is not copied.
A close inside the interval on a date absent from the session list rejects
selection. This avoids silently losing weekend or missing-session activity.

Only approved fields leave memory. Raw account columns, features objects,
source IDs, source filenames, credentials, chat IDs and free-text reasons are
excluded. Unknown exit text becomes `LEGACY_REASON_UNMAPPED` with an explicit
gap; native reason codes are retained. Domain-separated HMAC-SHA256 produces
stable position/fill pseudonyms and source-projection commitments. The seed,
its reversible mapping and raw source are never written or included in the
artifact. The seed file must be private, external to Git and outside the output;
the caller preserves it independently if stable future pseudonyms are needed.
Different seeds intentionally produce different pseudonyms.

## Package and provenance

The directory contains exactly:

| File | Evidence |
| --- | --- |
| `positions.jsonl` | Pseudonym, strategy/version, full monetary identity, entry/exit native times and sessions, lifecycle, quantity, multiplier/units, native exit code, recorded gross/net/entry/exit charges, whitelisted lineage clocks/hashes, source commitment and public row digest. |
| `fills.jsonl` | Pseudonymous fill/position links, native source append order, side, quantity, execution price, time/session, aggregate charge, unavailable fee components/provider clock, embedded slippage metadata, source commitment and public row digest. |
| `methodology.json` | Versioned arithmetic, selection, partials, privacy, lineage and scope limitations. |
| `manifest.json` | Twenty session dates/cutoff, per-session row counts and coverage status, counts including survivors, sorted currencies, enforced limits, SHA256/bytes/rows per data file, safety and missing-evidence declarations. |

The source append order is represented by a per-position sequence, not by raw
fill IDs. It preserves BUY-before-SELL ordering even for equal timestamps.
Files and rows use deterministic canonical JSON and ordering. Capture time,
output path, current wall clock and random output names do not affect bytes.

Staging contains sanitized data only, in a 0700 directory with 0600 files.
Every file is fsynced and verified before directory publication. Linux
`renameat2(RENAME_NOREPLACE)` publishes the complete directory atomically and
rejects even a destination created concurrently after the final existence
check. Unsupported platforms fail closed. Parent fsync follows publication.
Pre-publication failures remove only this tool's owned sanitized staging.
An error after rename can leave the complete directory present; verify its
retained manifest digest and report publication durability as uncertain. Do
not overwrite or delete the already published package to retry.

Packages and seeds must remain outside a Git repository. A future authorized
control-plane operation can upload only this four-file sanitized directory as
a private Actions artifact after source-owner review. No available, authorized
private source/control-plane export was present in this mission; no host
workflow, source snapshot or private artifact is claimed to have run. Source
authorization must come before such an operation; code readiness is not that
authorization.

## Independent arithmetic

`recompute` needs the sanitized package and an independently retained manifest
SHA256; it needs neither the seed, DB, fee model, broker nor strategy engine.
It enforces exact file sets, schemas, canonical rows, all file/row digests,
counts, twenty-session selection, monetary identities and provenance flags.

For each CLOSED position it reconstructs all BUY and SELL cashflows with
Decimal arithmetic at precision 160:

```text
purchase_cash = sum(buy_quantity * buy_execution_price * cash_multiplier)
sale_cash = sum(sell_quantity * sell_execution_price * cash_multiplier)
gross = sale_cash - purchase_cash
costs = sum(explicit_charge_for_each_fill)
net = gross - costs
```

Opening and closing quantities, recorded entry/exit charges, gross and net
must reconcile exactly; a one-cent mismatch is rejected. Duplicate fill IDs,
duplicate positions, orphan fills, SELL before sufficient BUY quantity,
over-sales, missing opening/closing legs, bad clocks and reordered records are
rejected. Partial exits remain separate fills in one lifecycle. OPEN survivors
retain fills, charges and remaining quantity; no closed or unrealized P&L is
invented, and their recorded closed-ledger amounts must all be null. Complete
liquidation before the final fill rejects a reopening hidden under one position
ID. Native trace clocks must remain ordered: signal/decision precede entry,
commit cannot precede entry, and no trace stage can follow the position's exit.
Native intent and commit may follow the ledger's recorded fill timestamp within
the transaction; their clock is not substituted for the ledger time. Declared
manifest row/byte quotas are enforced in addition to verifier-supplied budgets.
Spread/slippage already embedded in execution prices is not charged
again. All financial aggregates, win rates, profit factors and native exit
reason counts are currency-separated. No combined ARS/USD total is emitted.

Example interface for a future authorized offline artifact owner:

```text
python scripts/rc6_20session_evidence.py export
  --source /private/authorized-offline-paper.sqlite
  --output /private/new-sanitized-20session-package
  --pseudonym-seed-file /private/external-private.seed
  --session <ART-date> [exactly twenty --session arguments]

python scripts/rc6_20session_evidence.py recompute
  --package /private/new-sanitized-20session-package
  --manifest-sha256 <independently-retained-SHA256>
```

The control-plane owner executes these interfaces; this is not a requirement
for the user to operate a terminal. CLI failures return only fixed reason codes
and `EXTERNAL_EVIDENCE_PENDING`, never source values, SQL errors, paths or keys.

Recomputation validates the delivered bytes and arithmetic; it cannot detect a
fabricated dataset whose entire source and commitments were replaced. A source
owner must independently reconcile the selected snapshot, retain the manifest
digest and explain source coverage/lineage gaps. Account fees, cash/equity,
corporate actions, executable fills, edge/OOS and source authenticity remain
external evidence. Neither synthetic data nor a self-declared manifest closes
those questions.

## Permanent guards and tests

`tests/test_issue465_historical_package.py` exercises direct hand-calculated
partial fills and multiple currencies, a real local `PaperStore`/`PaperBroker`
ledger caller, read-only SQL enforcement, unchanged source bytes, exact
quantity/cost/gross/net reconciliation, bounded reads/time/bytes, deterministic
outputs, pseudonym privacy, same-clock ordering, cohort boundaries, survivors,
database lock, symlinks/hardlinks, private permissions, staged disk/permission/
fsync faults, and independent CLI recomputation. Negative tests modify payload
bytes, rows, monetary values and hashes, including rehashed manifests, to
attempt to bypass arithmetic, schema, source flags and lineage guards.

The first local run retained its RED result outside Git at
`/workspace/issue465-f-red.{log,xml}`: the existing canonical integration quote
fixture was in August, outside the explicitly selected September cohort. The
exporter correctly refused an empty cohort. Only that new integration fixture's
timestamp was corrected; selection and empty-cohort refusal remain guarded.
Final focused result/commit are recorded by the integration owner after freeze;
there is no front-specific PR, push, Predeploy or deployment.

A second adversarial probe found an exporter design error before freeze:
counting only accepted positions could consume a coarse SQL candidate outside
the exact cohort without charging its row budget. A SQL LIMIT could then hide
a later real position, while the smaller package still reconciled internally.
Fix: charge every inspected candidate before exact Python filtering; use an
inclusive SQL prefilter and exact timezone-aware microsecond cutoff in Python.
The permanent boundary/sentinel and microsecond regressions remain in the
suite. `/workspace/issue465-f-quota-red.{log,xml}` preserves a RED reproduction
against an explicitly derived temporary code copy with only the accepted-row
counter restored; it is not represented as an original #463 runtime test.
The preliminary `/workspace/issue465-f-rounding-red.*` probe is also retained:
its Julian-day equality assumption differs across SQLite versions and was
replaced by the portable exact-boundary sentinel test without weakening caps.

A third direct negative test found rounding at precision 80 near the allowed
numeric limits: a full liquidation at unchanged price with two partial sells
produced a spurious `-1e-9` gross. Its independent Fraction calculation gives
exactly zero. The source-input bound allows up to 129 significant digits in a
three-operand product plus five digits for bounded sums; precision 160 preserves
the arithmetic before subtraction. The original failing run is retained at
`/workspace/issue465-f-decimal-red.{log,xml}` and the regression remains active.

Independent read-only peer review supplied five further counterexamples, all
closed before freeze: decision/commit clocks after an already closed position;
rehashed OPEN-survivor closed P&L; rehashed declared quotas smaller than the
actual delivery; full liquidation and reopening hidden under one ID; and a
duplicated control-state row that hid REAL/1 behind PAPER/0. Direct RED tests
are retained at `/workspace/issue465-f-peer-red.{log,xml}` (six cases including
three separate quota attacks) and `/workspace/issue465-f-state-red.{log,xml}`.
The peer's unmodified first four probes subsequently pass at
`/workspace/issue465-f-peer-independent-green.{log,xml}`. They used synthetic
data only and performed no writes to this workstream or any runtime.

PAPER/SHADOW ONLY. `real_orders_sent=0`; real routes `NOT_CALLED`.
WRITE_OWNER is released with the frozen handoff. DEPLOY_OWNER NOT_ACQUIRED.
