# Exact small-container append

WORKSTREAM_ID: `RC6_PACKED_SMALL_CONTAINER_20261005`  
Mode: `WRITE_OWNER`, offline local PAPER/SHADOW source only  
Branch: `work/rc6-packed-small-container-20261005`  
Base: `4ba47b87b592c75601ad6f4b9526caf7d48b3b01`  
Owned scope: `rc6_shadow_runtime/packed_storage.py`, the new associated
`tests/test_rc6_packed_small_container.py`, and this workstream's evidence.

The native BIG4ba whole-source run remains RED at the original 90-second
deadline. It published and restored PREOPEN, performed real slow fsync and
five synthetic PAPER EXITs inside that fsync, then entered the OPEN planner
without completing the full PREOPEN/OPEN caller. Its 40.850-second
`storage_prepare` duration is the aggregate of four initializers: funnel,
report, checkpoint and status. ENTER-to-next-ENTER intervals of approximately
27.628 seconds for report, 8.395 for checkpoint and 4.827 for funnel include
entry overhead and are not independent role timers. The raw result and all
previous failures remain preserved by the coordinator. No new performance
measurement or acceptance is claimed here.

After the unchanged alias/named-field handling, the proposed fast path uses
C canonical ASCII JSON for a small **exact builtin** dict/list/tuple containing
only exact builtin None/bool/int/float/str leaves. Dict keys must be exact str,
and none can be volatile/binding-producing. The value is appended as one block
only if its full canonical byte length fits the current template's remaining
space. With no bindings and monotonic byte growth, every old token fits in that
same chunk, so this does not move any cut, marker, literal or output byte. If it
does not fit, the prior recursive append remains the fallback.

Private shortcut heuristics limit each trial to 16 members, strings/keys of at
most 256 characters and integers of at most 4096 bits. They never reject an
input or change any acceptance/quota/expansion bound: all other values follow
the old walk, including container and scalar subclasses, default=str,
bindings, nested values and larger leaves. The complete shape/alias/capacity
checks, ordered canonical hashes, codecs, clocks, GC policy and deadlines are
unchanged. This adds no cache between cuts or calls.

A short named capture without literals can supply its bytes only when freshly
computed by `_named` in the current append, after the same strict filter and
fit check. Short cache-hit bytes are recanonicalized from the actual value:
the old append ignored them, and trusting a caller's short cache would enlarge
its authority or hide a later input mutation. The regression attacks that
cache directly and compares the original append.

The independent `_append` oracle is frozen from baseline Git blob
`42e92789d93cf5bbcb6fcf342ae8de5fd0fc02fb`, with its prior buffer contract.
Controls compare exact remaining capacity, one-byte overflow, prior literal
slots, all complete captures and encoded envelopes, aliases/named fields,
subclasses/default=str, typed signed zero/bool/int, Unicode/NUL, clocks,
MAX_BINDINGS, nonfinite rejection and static/mutable root behavior. Dispatch
count comparisons describe structure only. Native testing awaits a coordinated
slot on a whole Git archive, literal frozen Python 3.11 with all 157 locked
versions, original source hashes/modes/blob IDs, offline guard and raw JUnit.
A later fresh uninstrumented integrated BIG90 caller determines measured
performance; the complete retention horizon, UX/browser and immutable
artifact/runtime gates remain separate pending obligations.
