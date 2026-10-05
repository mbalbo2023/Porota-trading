The publisher commits four immutable roles under the same `CURRENT.json` and
local durable custody: native report, native checkpoint, native status, and a
derived `projection.sqlite`. The index has no financial authority. It contains
all planner rows, all native lab rows, complete cohort/aggregate stage counts and
denominators, and bounded report headers. Native five-part identities and clocks
are preserved. Rows are compressed individually without changing their JSON.

`read_committed_generation(root, roles=None)` verifies every role, recomputes
native semantics and projection derivation, and declares
`FULL_LOGICAL_SEMANTICS`. Selecting returned roles does not omit verification.

`read_committed_projection(root, filters=None, offset=0, limit=10, deadline=None)`
verifies CURRENT, local custody, manifest, all four file hashes, native codec
hashes/CRC/expansion limits, typed cross-role headers and safety, status links,
projection header and exact dataset cardinalities. It returns report headers,
status, `dataset_pages`, and `funnel_scope`; it explicitly declares
`WIRE_AND_PROJECTION_SEMANTICS`, rather than claiming to decode and recompute the
large original report. `export_contract.derivation` links the three native
payload digests. All four digests are exposed in `verified_payloads` and equal
the manifest records. The schema is `rc6.shadow-ui-committed-projection.v1`.

Visible pages contain at most ten rows. Counts cover all matching native rows.
Table offset is separate from `filters.funnel_offset`; the selected funnel
cohort and its complete denominators are chosen from all matches independently
of the page. No matching cohort returns `NO_VERIFICADO`. Dimensions that the
native dataset does not publish return `FILTER_NOT_PUBLISHED_FOR_DATASET`, with
no fabricated factual empty population. Planner state applies to planner rows;
session and cohort selectors apply to funnel rows. Planned revisits remain
visible when achieved clocks are unknown.

Readers open protected source bytes with `O_NOATIME`; the verified SQLite index
is deserialized only in memory. They do not create source WAL/SHM files, repair
custody, or alter financial sources. The deadline is absolute monotonic time;
it is checked between reads, within codec expansion, during SQL and after output
encoding. This is a bounded failure contract, not a hard realtime scheduling
guarantee. Output is guarded at 4 MiB, offset at 100000, durable members at 64 MiB,
projection rows at 200000 and each expanded row at 256 KiB.

The lossless native codec is `rc6.lossless-json-storage.v1`, with canonical ASCII
JSON, logical SHA256/byte count, compressed storage SHA256, gzip CRC/trailer,
duplicate-key/nonfinite rejection and typed length fields. Native generation
logical expansion is explicitly bounded at 512 MiB, 32 million nodes and depth
64; the funnel retains a 32 MiB durable bound and 64 MiB logical expansion bound.
These storage contracts preserve every identity, entry, cohort, denominator and
causal clock. The index's logical encoding is independently versioned.

This checkpoint makes the API and small native positive/negative tests
reviewable. Large 12000/60000 completion, 1-second UI, 2-second health and resource
receipts must be rerun on the final committed candidate. Earlier degraded or
deadline-limited diagnostics do not close those requirements. Local custody is
not WORM or external authentication; compromise of both local roots remains an
explicit trust boundary.
