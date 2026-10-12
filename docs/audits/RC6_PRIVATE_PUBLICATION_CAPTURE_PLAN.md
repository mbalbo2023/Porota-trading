# Private publication capture reuse — source plan

State: DESARROLLADO / SOURCE_ONLY. Native guards, performance, BIG PRE+OPEN,
browser, horizon and artifact/runtime acceptance remain NO_VERIFICADO.

WORKSTREAM_ID: `rc6-private-publication-captures-20261005`.
Branch: `work/rc6-private-publication-captures-20261005`.
Base: `c22485c9e2c99e9b7ad85a78ddaed8c425cfa438`.
Base tree: `61bb44001a6c25e1e96c7a3c4169f1a7fe3fbeaf`.
Independent encoder reference: Git blob
`1fde1525d74fb8d77ed39ed9b017f03472a347cf`.
Owner: PERS. Root authorized SOURCE-only work in `packed_storage.py`,
`persistence.py`, the new private helper, meaningful guards and this plan.

## Measured problem and source inference

BIG8764 remained RED at its original global 90 seconds. PREOPEN completed in
85.033568 seconds; OPEN did not complete. Inclusive totals were prepare
39.597 seconds/4 calls, encode 8.531/7, metrics 3.875/4, publication 52.588 and
restore 8.266/3. These spans do not attribute a percentage or a saving to any
individual encoder operation. Peak RSS was 1,606,672,384 bytes, five factual
PAPER EXITs completed within real fsync, and source/code custody was intact.
This change does not reclassify that result.

Source shows that worker report and checkpoint reference the same engine
graph: report expands `result`, while checkpoint assigns `result['engines']`.
The engine graph is built once. Existing publication shape memo and named
cache already reuse some work. Separate Prepared constructors still count
and capture their own root sections. No additional performance measurement
has established the cost of those particular repeated subwalks.

Report-only funnel observations additionally reference telemetry warmup
objects. Incoming alias counts can therefore differ between report and
checkpoint even when their engine root is the same object. Whole-engine
reuse is insufficient. The candidate replays large builtin subcollections,
such as instruments, only where their complete reachable alias context and
byte frontier match. Telemetry with an alias mismatch follows the original
walk. A native-small guard must demonstrate real instruments reuse, not only
equivalence through universal fallback.

## Exact boundaries and authority

1. The seam is only `EvidenceFiles.commit_generation(...,_take_payloads=True)`.
   The defensive-copy public branch is unchanged. The helper accepts no
   external reuse cache. It constructs all roles in their original order,
   through the real Prepared constructor, preserving constructor ENTER/EXIT
   benchmark wrappers.
2. Each role runs the complete original `_shape` and `_count`. Objects remain
   strongly referenced, incoming arrivals remain independent, and children of
   a shared parent are visited only on its first arrival. Tables are never
   borrowed from another role.
3. Activation uses a ContextVar with a single grant linked to the exact
   Prepared instance, root, plain-dict cache and exact builder. A stable
   original constructor code identity is checked on its execution frame.
   The grant is consumed before count, bound to its creating thread, reset in
   finally and stripped of all graph/builder references. No frame or builder
   closure is stored. A public nested constructor, nested helper or copied
   context in another thread cannot borrow the grant. There is no global
   monkeypatch of capture or builder methods.
4. Whole-role eligibility is checked after original count and before any
   replay. Containers/keys/leaves must have exact builtin types. Exact
   standard Decimal is additionally permitted at this gate only: its
   default-string serialization remains original, with sign, exponent,
   trailing zeros and decimal context preserved. Exact Counter is permitted
   only with original Counter/dict/object MRO, static inherited dict methods,
   original getset dictionary descriptor and no instance attributes or
   overrides. Lookup does not execute a substituted descriptor. Decimal and
   Counter subclasses and all other custom objects force the original role.
   A later callback in any section must never observe scalar/binding state
   omitted by a replay.
5. Reused subtrees themselves remain exclusively exact builtin containers,
   string keys and immutable builtin scalar leaves. Decimal and Counter are
   excluded from snapshots/replay. Each snapshot preserves strong identities
   of objects, keys and children, insertion order and `incoming>=2` for every
   reachable container. Mutation, changed aliases, unequal identity,
   different section/name/root or an unprovable context invokes fallback.
6. The hook is after the existing named-boundary/FIELDS and small-plain paths,
   and applies only to collections with at least 64 members. Original named
   cache lookups/inserts remain original plain-dict operations. A private
   trace records only the lookups required by a candidate; replay requires
   their verified monotonic final entries by identity and order. Fresh short
   named captures must have the exact builtin canonical bytes that a later
   cache hit would emit. No cache is repaired, normalized or trusted because
   it is caller-supplied.
7. Replay requires the exact input template bytes and literal sequence at the
   frontier. Existing result prefixes are retained intact. Immutable emitted
   suffix Captures and exact final template/literals are replayed; constants
   and encoder operations must match. There is no token tape, changed token
   cut, changed marker ordinal, altered literal clock or logical tick dedup.
8. Named cache ownership and release stay at the original post-projection/
   post-both-pack point. Snapshots are released after preparation. This
   preserves custom-object destructor timing on original fallback paths.
   Each role keeps its original ordinary `expanded` dictionary and `_proof`:
   every expanded occurrence is verified and contributes all bytes to length
   and SHA in order. Expanded-byte sharing is deliberately deferred.
9. All reader/codecs, durable/expansion/node/depth caps, quotas, source-copy
   policy, GC policy, clocks, financial calculations and admission are
   unchanged. Exhaustion of an optimization bound follows original capture;
   it does not reject a previously accepted input.

## Private work bounds and limits of inference

Snapshot bounds are 262,144 containers, 8,000,000 stored references and
128 MiB of explicitly accounted shallow snapshot/plan structures. These
figures do not bound total Python RSS, allocator padding, temporary queued
sets, candidate/dependency bookkeeping or the underlying input graph.
Transient snapshot accounting also checks pending/queued structures.
Additional bounds are 4,096 candidate roots/plans/disabled entries,
262,144 dependency lookups per candidate and a 65,536-byte frontier template.
Whole-role eligibility scans at most 1,048,576 counted containers and
16,000,000 key/child references; it does not allocate another graph.

These are optimization fallbacks, not enlarged contractual admission limits.
Bounds or native alias contexts may exclude actual BIG reuse. The source
plan and small producer fixture do not establish actual BIG reuse, RSS or
speed. Global BIG remains 90 seconds and 2 GiB, including PREOPEN and OPEN,
under the existing .25-second source deadlines and original live/archive/
scratch quotas. No UI/browser/horizon/artifact/deploy gate follows here.

## Intended native verification, not yet executed

Frozen independent PriorBuilder and PriorPrepared methods are from the exact
reference blob, with only global qualifiers/class names/import routing changed.
The new module compares complete per-role cuts/templates/literals, incoming
and strong object tables, named cache contents/order after each role, metrics,
full wire and full-reader logical canonical SHA/bytes. The cases cover real
warmup alias mismatch plus actual instruments priming/replay; changed context,
mutable headers and static freeze; DAG/equal-distinct identities; cut sizes
63/64/65 and 255/256/257/65536; prefix/literal/constants/cache mutation;
private-bound fallback; Counter/Decimal original serialization and negatives;
later callback state, cache poison, subclass/default-string call order;
single-use/wrapped constructor/thread/reentrancy/error cleanup; destructor
lifetime; cycle/depth/nodes/string-key/nonfinite/MAX_BINDINGS and independent
durable/expansion rejection. Counts will come only from actual collection and
JUnit, not from this plan or AST analysis.

Planned focal after a clean pin, exact Finance review and Root CPU slot:
`tests/test_rc6_publication_storage.py`,
`tests/test_rc6_packed_scalar_dispatch.py`,
`tests/test_rc6_packed_volatile_scalars.py`,
`tests/test_rc6_packed_small_container.py`,
`tests/test_rc6_packed_capture_allocations.py`,
`tests/test_rc6_packed_storage.py` and
`tests/test_rc6_funnel_storage_codec.py`.

Execute only a complete Git archive of that pin with literal
`/workspace/venv_rc6_frozen311/bin/python -I -B`, exact 157 locked distribution
preflight before fixtures, source SHA/modes/blobs/index custody before/after,
no cache/plugin/bytecode writes, offline/import closure guards and preserved
raw JUnit/receipts. Existing module coverage overlaps prior focal evidence.
The new source guard is not a financial scenario or a BIG acceptance witness.
Only Root's subsequently integrated, uninstrumented native BIG may determine
performance or global completion.

## Private type audit correction — source only

WORKSTREAM_ID: `rc6-private-type-identity-20261006`. Documentary parent:
`7791c00b381e1c84c0eddce7be487ec2bf786ccc`. The e24 focal D remains an actual
468-case qualified compatibility result; its source and A/B/C/D capsule remain
unchanged historical evidence. Root's subsequently integrated BIG1426 remained
RED at the original 90 seconds. Inclusive preparation was 49.283258 seconds/4
calls and publication 59.720053 seconds/1 call. Neither span establishes private
reuse hits, the cost of an audit, or a saving.

SOURCE review found that seven newly introduced predicates compared classes by
tuple membership. An unknown metaclass can run `__eq__` there before original
capture, mutate a marker field and change the resulting wire. This is an
unexecuted source-derived counterexample until the controlled native guards run.
The semantic reference is the pre-private 8764 encoder, frozen in PriorBuilder
and PriorPrepared from blob `1fde1525d74fb8d77ed39ed9b017f03472a347cf`; accidental
callbacks introduced by the e24 private audit are not retained as a contract.

Only the three Snapshot predicates, candidate-construction predicate, two
whole-role predicates and the packed private hook now use class identity.
Original memberships in count, shape, scalar, binding and small-plain paths
retain their frequency, order and effects. There is no class-keyed set/dict or
user equality/hash in the new type predicates. An exact-dict root check uses
`dict.items`, exact string names and `(id(member), name)` candidate pairs to
disable the private scope for a role with no activable root capture. Mutable
root fields were already excluded from those candidates. Candidate roles retain
the complete final-state eligibility audit. Counts, constructors, every
Snapshot.matches check, dependencies, frontier bytes, cache lifetime and all
expanded-occurrence proofs are unchanged. No count fusion is implemented.

The historical e24 helper is preserved byte-exactly at
`tests/fixtures/rc6_publication_storage_e24.py.source`, SHA256
`35ad9e2120d6d435798496568341d3426082e62c16425c9e0fa91f54833505be` (16,347 bytes).
It is compiled explicitly only by the controlled guard; it is a historical
reference, not the fixed producer. Fresh graphs isolate original, e24 and fixed
paths. A marker preceding the probe demonstrates the e24 full-wire difference;
fixed sections, complete role wire, callback traces, metrics, canonical hash and
full decode must match 8764. Baseline metaclass callbacks from original scalar
serialization remain observable, so the claim is zero additional private type
callbacks, never zero callbacks overall. Other controls cover private Snapshot/
candidate/hook isolation, non-candidate self-clearing Counter callbacks and
strong count tables, late count mutation with final candidate audit retained,
and standard Counter/Decimal serialization with actual instruments replay. The
existing native-worker guard still verifies factual Counter/Decimal groups,
publisher/full reader/projection and real reuse under the canonical small caller.

Source AST/compilation checks do not execute these guards. Native counts must
come from the assigned single collection/JUnit; only a separately authorized
integrated BIG can decide performance. The original 90-second, .25-second,
2-GiB, live/archive/scratch quotas and GC policy remain unchanged. The broad
enumeration incident remains UNKNOWN/NOT_VERIFIED as recorded in the capsule.
