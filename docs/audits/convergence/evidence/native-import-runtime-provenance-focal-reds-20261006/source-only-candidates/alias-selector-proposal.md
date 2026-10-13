# SOURCE proposal: require only alias selectors observed by the original frame walk

This is one proposed change to private publication capture reuse. It has not
been implemented or executed. Product baseline is Source
`1e2fddc10d0e313bc5a6298e8fa60fe7c9f560f8` /
`b984539931a25a5f77eae55116c4fa9543d53748`; the semantic oracle remains the
independent frozen pre-private 8764 encoder. The historical 1426 Native RAW is
`/workspace/rc6-canonical-1426b284-big-20261005-raw/native-big-result.json`, SHA
`db1f92f24c407869887fca99dd44c5eb1a0b99ffaa939a17d94ed8063a78cb7c`.
Its aggregated preparation span is 49.283258 seconds / four calls and its
publication span is 59.720053 seconds. These inclusive measurements do not
identify snapshot cost, candidate frequency, hits, or a saving from this plan.

## Exact repeated-work opportunity

`publication_storage._Snapshot` stores `incoming[id(node)] >= 2` for every
reachable container, and `matches()` compares every stored alias bit.
`packed_storage._CaptureBuilder._append` uses that bit only for a container
visited with `root=False` and a name outside `_FIELDS`. A successful named
capture with template length at least 64 emits an immutable Capture and
returns; its descendants are serialized by `_named` or taken from the exact
named cache entry. Their alias bits do not choose a token, slot or boundary.

The current snapshot is therefore conservative beyond the encoder's actual
alias selectors. One changed alias below an unconditional `stages` named
boundary can reject an otherwise identical large instruments frame, forcing
the second role to traverse that frame again. This is a SOURCE counterexample,
not a statement that the historical BIG executed it.

## Single change and private schema

Keep the complete structural snapshot and the full before/after first-use
checks. Add an ephemeral alias-requirement map to the existing first-frame
dependency trace, then use that map only to select alias comparisons on replay.
It is not a wire format, persisted proof, count cache, or caller authority.

The closed conceptual schema is `PRIVATE_FRAME_ALIAS_REQUIREMENTS_V1`:

- `valid`: builtin bool, initially true; any uncertainty makes it false.
- `required`: plain dict keyed by builtin `id(node)` integers. Each value is
  `(strong_node_reference, expected_alias_bool)`; the node must already be the
  exact node in the full snapshot and original builder.objects.
- `limit`: at most the existing private container/dependency limit. Actual map
  and entry overhead is added to the existing snapshot budget. Exhaustion only
  disables a reuse plan; it never changes input acceptance or reruns callbacks.

Record only original **container decisions**, not scalar tokens. Use the
original named branch result, without evaluating `incoming.get` a second time:

1. In the existing true named branch, a private active trace records true only
   when the exact string name is outside `_FIELDS`.
2. In the paired false branch, it records false only for a container with
   `root=False` (thus not an unconditional `_FIELDS` named visit).
3. Root visits and unconditional `_FIELDS` visits need no alias requirement.
   A child's visit after a short named capture is still observed normally.
4. Repeated visits to the same container must agree. Unknown/out-of-frame
   nodes, non-plain builder lookup tables, names or conflicting decisions
   invalidate the trace and preserve the complete original capture result.
   No additional input lookup or user callback is made to decide eligibility.
5. Before committing the plan, every recorded alias requirement must equal
   that node's alias bit in the full pre-capture snapshot. The existing full
   post-capture `snapshot.matches(builder)` and constants/dependencies check
   still run. Transiently inconsistent observed selectors cannot prime a plan.

Replay still checks the full strong node/edge/key/order snapshot for **every**
node, original object identity, the exact named-cache dependency identities,
constants, prefix, literals, suffix and tail. The only relaxed comparison is
the alias bit for nodes that were not capable of selecting the original walk.
Every recorded selector must match this role's independent original incoming
table. All structural matches, first-use post-checks and role proofs remain.

There is no merged incoming table, count fusion, deferred eligibility audit,
post-snapshot omission, graph ownership assumption or expanded-byte sharing.
The full final-state whole-role gate remains unchanged. The public constructor,
original `_count`, `_shape`, callbacks, private named-cache lifetime, mutable
headers, current cut layout, every logical occurrence SHA, reader, quotas,
GC policy and deadlines remain unchanged.

## Equivalence argument

Both roles count their own complete graphs and run the same whole-role gate.
Full structural matching proves the same builtin nodes, ordered keys and child
identities at replay. Starting from the same prefix and constants, induction
over the original visited containers gives the same named/non-named decisions:
root visits do not use aliasing; `_FIELDS` selects named capture unconditionally;
all other visited containers have the recorded and independently matched alias
bit. The same cache entry identity then supplies the same named Capture and
effects. A named >=64 boundary returns before any descendant append decision.
The recursive paths, short/fresh named handling, direct leaves, slots and cuts
therefore remain equal. Existing dependency checks preserve exact named-cache
contents and insertion order after each role. The replayed immutable bytes are
still expanded, counted and hashed in full original order separately per role.

The argument is conditional on unchanged constants, valid bounded trace and
the existing callback-free role/subtree gates. Any unproved case takes the
original path. The schema stores strong references and primitive identities;
it never hashes or compares input classes with user methods.

## Qualification boundary: stable wrappers are not a purity proof

The current `_constants()` permits a stable FunctionType wrapper. Such a
wrapper can inspect an otherwise unvisited child's incoming bit from a frame
and make a later scalar or volatile decision depend on that bit. Recording
only the original named selectors would not cover this observation. Therefore
**the selector map alone is not sufficient to authorize implementation**.
Before enabling a narrower alias comparison, a private Source-defined
capability must prove the traversal operations and canonical JSON dispatch
are the original, unwrapped operations. Tokens must be established eagerly in
the packed module before benchmark/user wrapping, never by the lazily imported
helper or caller. Wrapped/modified operations or an unproved canonical dispatch
must keep the complete current alias matching. The real constructor's benchmark
wrapper need not disable this domain because its original frame capability is
already separate and it does not supply a frame-walk operation.

This is an unresolved Source proof requirement, not an implemented capability.
The plan remains SOURCE_ONLY_UNQUALIFIED until Root and Finance accept a bounded
way to prove that domain. It does not propose weakening the existing wrapper
mutation guards, increasing their scope by assertion, or treating stable code
identity as callback-free authority. If that requirement makes the change too
large, keep the current alias snapshots; do not implement the mask alone.

All shortcuts must be observed before they stop the original walk: named true
and false decisions are recorded before >=64 returns; a <64 named result that
continues recursion records all later visited containers; `_small_plain` visits
its container gate but contains only builtin scalar children; volatile children
are bound canonically and have no descendant `_append` alias selector. Existing
fresh/short cache dependency checks still apply, including the <64/no-literal
fresh versus cache-hit case. Distinct paths/names for one node contribute to the
same required alias bit whenever any path can depend on it.

The real worker definitely shares engines between report/checkpoint
(`worker.py:354,361`) and exposes warmup_progress from planner telemetry in the
report-only funnel (`funnel.py:172`). Warmup_progress is not an `_FIELDS` name;
its actual visited selector must remain guarded. These Source facts do not
prove a hidden named-frontier alias mismatch or mask eligibility in BIG.
The proposed positive structural counterexample therefore needs a real small
worker check in addition to the independent constructed roles; absence of that
production case must be reported as non-applicability, not a saving.

## Independent controls required before Native qualification

1. **Positive hidden-alias counterexample with full prior wire.** Fresh roles
   share `engines={'instruments': rows}` with at least 64 distinct rows. Each
   row contains an unconditional `stages` named object whose canonical
   template exceeds 64 bytes. A nested child occurs once under this shared
   stage; report has an additional root field pointing to that child, while
   checkpoint does not. Original count makes the child's alias bit differ.
   Frozen 8764 builders share a plain named cache as the original publisher
   does. Require identical engines Captures across roles in that baseline,
   actual old-e24 snapshot fallback on the child bit, actual new replay, and
   full sections/metrics/representation/envelope wire/decoded JSON/hash plus
   strong ordered named-cache contents equal to each independent prior role.

2. **Observed selector mismatch must still reject.** Put the extra alias on a
   container actually visited with a non-FIELDS name, including short named
   templates on both sides of the 64-byte boundary. Require an actual baseline
   Capture difference when the selector changes, no private reuse, and each
   role equal to its own prior wire. Include mixed names/root visits of the
   same container and DAG arrivals; do not compare only a boolean flag.

3. **Structural mutation behind the frontier remains protected.** With a
   stable builtin FunctionType wrapper during first capture, change a hidden
   child A to B and keep B through the first post-check; restore A only between
   roles. The first full snapshot post-check must invalidate that plan. Also
   change keys/order/leaves between roles behind a named boundary; full replay
   structure checks must reject reuse. Compare complete old/current/prior wire
   and cache effects, rather than treating a status flag as evidence.

4. **Trace/context adversaries.** Unknown nodes, duplicate/conflicting selector
   observations, private budget exhaustion, changed constants and reentrant
   same-builder capture preserve original traversal without a second callback
   invocation. Non-plain internal lookup tables must not gain callbacks from
   tracing. Public/caller cache, copied ContextVar in another thread, subclasses
   and custom/default=str roles retain the original path.

5. **Existing factual native caller.** Keep the real small worker guard with
   actual Counter/Decimal funnel state, reporter/checkpointer, every role wire,
   full reader and projection. A limited native graph guard may report actual
   priming/replay versus fallback counts without sampling or timing attribution;
   it is not a surrogate for the large producer. Do not inject a fake funnel
   aggregate or label structural counts as speed evidence.

All existing independent 8764 oracles, callback order/frequency, signed zero,
Unicode/NUL, volatile literals, mutable headers, MAX_BINDINGS, expansion,
complexity, alias/depth/node and rejection guards continue to apply. The only
proposed additional metadata is bounded container selector state; there is no
per-token tape or cross-publication cache. Only a later authorized complete
PRE+OPEN BIG90 can assess performance, and the proposal does not predict that
it will close that gate.
