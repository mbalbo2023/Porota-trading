# Issue #465 — Front B / F-02 remediation

This report covers the intraday negative-capability lifetime defect only. The
integration owner is responsible for the single consolidated candidate, complete
suite, artifact and exact Predeploy V2. This front has no merge, deployment,
production/runtime, PPI Watch or provider authorization.

## Authority and ownership

- Base: `caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423`, isolated branch
  `fix/issue465-capability-20261004`.
- Authority: complete issues #465, #464, #458, #460 and #462; PR #463's
  conversations, attestations and independent audit; absorbed PR metadata;
  `AGENTS.md` and `ops/policy/porota-policy.yaml`.
- Initial owned scope: `cf_intraday_scalping.py`, exclusive
  `tests/test_issue465_capability_cache.py` and this RCA.
- WRITE_OWNER declaration:
  <https://github.com/mbalbo2023/Porota-trading/issues/465#issuecomment-5981112240>.
- The original HF3 test fixture was reconciled only after scope expansion:
  <https://github.com/mbalbo2023/Porota-trading/issues/465#issuecomment-5981207335>.
  Its assertions remain unchanged in meaning. Its real ephemeral SQLite fixture
  replaces a fake store without `connect`; no admission or persistence guard was
  disabled for the remediation.

## ERROR → RCA → FIX → GUARD

The base worker added the literal PPI tuple `(ticker, instrument_type, settlement)`
to a process-lifetime set after `Instrument not found`. Every later cycle skipped
that request. Session-scoped contract reset could not reset the independent set.
The same running worker therefore made one call on D and none on D+1 even when
the provider boundary would return valid data.

The retained RED test `test_new_session_recovers_same_worker_without_restart`
uses the real long-lived worker, selection/calendar gates and SQLite. At the
base SHA its calls were `[0]`; the required calls were `[0, 2]`. The new behavior
passes the same test without restarting or replacing the reader.

The replacement combines bounded exponential negative TTL, local-session change
and semantic catalog/configuration fingerprints:

- Initial cooldown: 900 seconds; same-context failed probes renew it to 1800,
  then 3600 seconds, with a 3600-second maximum.
- A changed Argentina trading date or relevant catalog/configuration permits a
  read-only probe after at least 60 seconds from the last attempt. Clock rollback
  denies a probe. Timestamp-only catalog refresh and changes to another request
  do not invalidate the negative.
- Negative metadata lives in the existing full-identity contract row's `detail`;
  no schema migration or catalog AVAILABLE rewrite is introduced. A bounded query
  also preserves the same literal wire's negative if a catalog alias is retired
  and rebound to a different currency/market identity. Original identity remains
  provenance; sibling history and confirmation counters are never inherited.
- Memory copies use an LRU capped at 2048 entries. Eviction and restart preserve
  the durable cooldown and fresh-warmup barrier. Attempts/evictions saturate;
  durable retry counters, timestamps, identities and metadata sizes are guarded.
- Before a probe, prior entry authority is invalidated durably. The ordinary
  native read scope and global PPI budget still admit the read; a probe has no
  application retry and never calls signal evaluation or promotion.
- A valid, current response establishes a new causal epoch. The probe starts
  with zero observations, overlap and new source samples. Source points at or
  before that epoch cannot confirm volume, supply indicators or revive an old
  BUY candidate. Confirmation and all 15 native indicator samples must come from
  later source points. Same-session closed-minute revision rejection remains
  binding; historical evidence is preserved.
- Empty, stale, future or malformed probes remain closed and renew cooldown.
  Direct persistence cannot recover a negative from stale/future input either.
  A positive control later produces one PAPER fill after genuine native warmup,
  confirming that recovery remains live.

No score, fees, spread, TP, SL, EOD, MaxHold, size, open-position limit, strategy
family, paper broker guard or production capacity setting was tuned.

## Transport and SHADOW integration guards

The actual SDK turns some HTTP errors into generic body exceptions. Front B
consumes Front A's public, sanitized, current-read `last_read_error_code` property.
HTTP 429/401/403 take precedence even when the body misleadingly says
`Instrument not found`. Global budget circuits retain their precise HTTP code;
the worker preserves its canonical authentication/login-cooldown behavior.
Other genuine instrument-not-found results remain request-scoped and do not
degrade global PPI health. Transport responses, tokens and broker bodies never
become capability telemetry.

The recorded local compatibility checks used Front A
`558cf04b6807fd83ab0952aed032a00906a9cc53`. Its two owned modules were loaded read-only
from that worktree; Front B did not edit them. That A head was subsequently
revoked for a separate durable EXIT-floor finding, so these compatibility results
are historical. Before integration, the checks must be repeated against A's
replacement released head and its unchanged public reader contract. Only that
replacement belongs in the single final candidate.

The initial expanded event framing appended telemetry after `shadow_identity`.
The real `ShadowRuntime._metadata` reader rejected it with JSON `Extra data`.
The retained RED became a permanent integration test. The final event preserves
the native reason and terminal identity JSON exactly; detailed bounded capability
telemetry uses a separate event kind. No SHADOW parser relaxation is required.

Fault injection into the durable consecutive-failure counter also reproduced an
unhandled conversion failure after the probe, followed by the same error while
recording failure. Restoration now rejects malformed/out-of-range integer
counters before the read and reports an ordinary closed worker failure. The RED
and actual-worker guard remain in the evidence package.

## Required F-02 cases

All names below are in `tests/test_issue465_capability_cache.py`. The harness uses
ephemeral real schemas, catalog/readiness selection, normalizer, persistence,
evaluator, broker and exit supervisor; only its controlled provider boundary and
clocks are substituted. The HTTP cases use the real facade/SDK/transport guard and
SQLite global budget over an intercepted fake wire.

| #465 case | Permanent executable evidence |
| --- | --- |
| 1. Instrument not found on D | `test_instrument_negative_is_scoped_observable_not_global_failure` |
| 2. No hammer within TTL | `test_many_cycles_inside_ttl_never_hammer` |
| 3. TTL expires and re-probes | `test_ttl_boundary_performs_one_readonly_reprobe`, `test_failed_reprobe_renews_bounded_exponential_cooldown` |
| 4. D+1 without restart | `test_new_session_recovers_same_worker_without_restart` |
| 5. Valid D+1 recovery | `test_valid_next_session_exits_negative_requires_new_native_points` |
| 6. Fresh warmup from zero | `test_old_confirmed_history_cannot_supply_recovery_warmup_or_candidate` |
| 7. No immediate entry | `test_reprobe_never_invokes_signal_or_promotion` |
| 8. Relevant catalog/config change | `test_semantic_catalog_or_config_change_revalidates_before_ttl`, `test_catalog_rebind_of_same_wire_request_requires_readonly_fresh_warmup` |
| 9. Other identity stays independent | `test_catalog_refresh_and_other_identity_do_not_contaminate_exact_request`, `test_same_ticker_other_settlement_has_independent_negative_capability` |
| 10. Existing 429/session semantics | Six parametrized `test_native_sdk_reprobe_preserves_global_breaker_and_session_semantics` cases, `test_explicit_session_invalid_reprobe_closes_reader_and_obeys_login_cooldown`, `test_generic_global_error_never_creates_instrument_negative_cache` |
| 11. Restart safety | `test_restart_preserves_cooldown_and_fresh_warmup_safety` |
| 12. Bounded memory | `test_memory_bound_eviction_cannot_cancel_durable_cooldown`, `test_rotating_universe_pressure_preserves_durable_denials_after_lru_eviction` |

Additional attacks cover empty/stale/malformed probes, 60-second spacing,
rollback, truncated/oversized/wrong-identity/missing-epoch/future-due/corrupt-counter
metadata, closed-minute rejection, source-age validation, invalid recovered
candidate timestamps and the actual SHADOW event consumer. The rotation-pressure
case has 256 distinct wire requests, a 16-entry LRU and 20 native selection cycles
inside TTL: exactly 256 provider-boundary reads, no repeats, durable negative rows
for all 256, bounded memory, no candidate and no fill.

## Evidence and local validation

The front's sanitized JUnit files are retained in the integration evidence
directory, `/workspace/issue465-authority`, for packaging. They contain synthetic
identities and temporary stores only. Required SHA-256 anchors:

| Evidence file | Result | SHA-256 |
| --- | --- | --- |
| `f02-baseline-red.xml` | Base SHA, 1/1 RED | `f0dac9920a948e783a304e37e443f19af730dca78ab40e385579bec732314ad8` |
| `f02-baseline-red.log` | Base reproduction log | `46b018fd17b074447c4de072cedb2cc05dc6ac0ee633af2310acc4c597d2e414` |
| `f02-initial.xml` | Original fake-store incompatibility, 36/37 GREEN | `1e16af9641d70b67bcc7102446e49f9dbd1f62035caf4923bbddeca0cf20f74c` |
| `f02-shadow-framing-red.xml` | Actual SHADOW consumer, 1/1 RED | `a4a6a928733c0cb72eeed844dcbc4c80dfd8e0649651910e3d787bc43b29048b` |
| `f02-corrupt-counter-red.xml` | Durable-counter injection, 1/1 RED | `131f758f3c7ca77bb752ac5d7ff9a8ac627fbe00e537afe46ea4528b14d397d6` |
| `f02-final-focal.xml` | 119/119 GREEN; errors/failures/skips = 0 | `219cbb294f2cf298546d2d03afeca3d566d8d243a47a1233dfd0c8f39120871e` |

The final local pass uses the pinned dependency environment with Python 3.12 and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`: 39 new F-02 cases and 80 existing HF3/intraday
integration, freshness, contract-policy, SHADOW wiring/entry and dashboard
semantics cases. It takes 17.49 seconds. Compile and `git diff --check` also pass.
The authoritative complete Python 3.11 suite and exact artifact/Predeploy V2 are
the integration owner's remaining gates; focal results do not claim their result.

`real_orders_sent=0` is asserted through the real temporary observer state on
every harness run. Native wire evidence excludes order and Account Movements
routes and verifies that credentials, SDK tokens and broker bodies are absent
from recorded telemetry. There were no provider calls, product DB writes,
production/runtime mutations, front PRs, pushes, merges or deployments.
