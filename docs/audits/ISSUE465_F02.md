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
- The initial Front B freeze `c474a0f676bda53e5090ba27fef89a1410d9b7a6` was revoked
  after a further self-attack found recovery-metadata loss. The same product/test/
  RCA scope was reacquired before repair:
  <https://github.com/mbalbo2023/Porota-trading/issues/465#issuecomment-5981655796>.
  The previous HF3 fixture hunk was preserved without further edits.

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
- The probe's attempt count, current session/configuration, last-read clock and
  renewed cooldown are durable before the wire. A process death during that read
  therefore cannot trigger another read a second later after restart. Completion
  records the same attempt, with no double count. The original first negative
  and full-identity provenance remain available.
- A valid, current response establishes a new causal epoch. The probe starts
  with zero observations, overlap and new source samples. Source points at or
  before that epoch cannot confirm volume, supply indicators or revive an old
  BUY candidate. Confirmation and all 15 native indicator samples must come from
  later source points. Same-session closed-minute revision rejection remains
  binding; historical evidence is preserved.
- Recovery has the durable status `INTRADAY_CAPABILITY_WARMUP_PENDING` until both
  native interval confirmation and the existing 15-sample requirement are met
  entirely after the epoch. Its bounded `COUNT`/`LIMIT 15` check adds no financial
  parameter. This status mandates valid, unique-key recovery metadata even if
  the detail is replaced with otherwise legitimate legacy human text. Zero fresh
  points, partial interval confirmation, restart or metadata corruption cannot
  grant confirmed entry authority. Genuine never-negative legacy warmup remains
  compatible and has a real-worker PAPER-entry positive control.
- That recovery status survives every native transition, including empty and
  stale responses or invalid-payload/global/session errors. A closed-minute
  revision uses `INTRADAY_CAPABILITY_WARMUP_REJECTED_CLOSED_POINTS` and retains its
  binding rejection counter. The underlying native volume state is recorded
  separately in detail; an EMPTY transition cannot erase the recovery epoch or
  its request-scoped catalog-rebinding guard.
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

The initial compatibility check used historical Front A
`558cf04b6807fd83ab0952aed032a00906a9cc53`, subsequently revoked for a separate
durable EXIT-floor finding. The repaired final focal check instead binds released
Front A `ade87a8325195c91249c947c9b5af4b2f7c11a93`, tree
`3df15db9bc76f5a7a2d2427921728da14551cdb6`. Its two owned modules were loaded
read-only from that exact clean worktree, verified before and after the run;
Front B did not edit them. The unchanged public reader contract and all six
native SDK transport cases are GREEN against the replacement. Only the
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

Continuing self-attack after the first B freeze found a second real defect:
truncating recovered `PENDING_LIVE_CONFIRMATION` detail to `{` and restarting
made the parser accept it as legacy. Two reads then confirmed retained history
and produced a premature PAPER fill with only two post-epoch source minutes.
Replacing the detail with empty, plain human or scalar JSON text had the same
authority-loss risk. Partial interval confirmation before 15 fresh samples also
needed protection. The durable recovery status above fixes the ambiguity;
recovery epoch must equal the recorded successful-read clock, and duplicate keys
are rejected. Fourteen permanent tests corrupt seven metadata forms at the probe
and partial-confirmation stages, restart the actual worker and assert rejection
before any next read or fill. The original premature-fill JSON and 14-case RED
JUnit are preserved; the revoked head is historical evidence only.

Independent peer review then found five related RCA groups: an ordinary EMPTY
transition could lose the durable recovery status and permit the same early fill
(and catalog rebinding could lose lineage without any corruption); invalid native
`checked_at` or nonfinite retained lineage could fail both the transition and its
failure recorder, escaping the worker; malformed session/fingerprint context
could masquerade as a genuine change and shorten TTL; and process death after
pending publication could repeat a wire read one second later after restart.
The original RED witnesses are retained. All nine unmodified peer witnesses pass
after the repairs. Seventy-four additional bounded deep-JSON boundary probes
found no further counterexample.

Restore now validates the native clock, causal epoch, bounded integer counters,
exact session date, lowercase SHA-256 fingerprint, complete/coherent bounded
request and origin identities and cooldown/backoff agreement. Strict duplicate-key,
`parse_constant` and `parse_float` guards reject NaN, both infinities and numeric
exponent overflow anywhere, including unused nested fields. Validation happens
before probe admission or strict serialization, so malformed data stays a
contract-scoped worker failure rather than causing recursive failure recording.

A final distinct local fault injection replaced the native checked clock with
`invalid token`. Chained datetime error text was then mistaken for SDK
authentication, rotating readers and logging in every cycle even though no new
intraday read had occurred. The RED witness recorded four reader instances
instead of the two legitimate process runs. Only an exception from the actual
`intraday` read now consumes the SDK classifier/current-read transport property.
Local decoder or persistence exceptions use a sanitized exception-class code;
they cannot reuse a prior HTTP status or rotate a session because stored text
resembles an authentication message. The permanent actual-worker guard asserts
three closed error cycles, no extra wire reads, no extra login and no fill.

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
candidate timestamps and the actual SHADOW event consumer. The recovered-state
restart matrix is
`test_recovered_warmup_metadata_replacement_cannot_borrow_old_history_after_restart`;
it covers truncated, empty, plain legacy, scalar JSON, missing-schema,
missing-epoch and duplicate-key replacement at both recovery stages.
`test_never_negative_legacy_contract_retains_native_warmup_and_paper_entry` is the
compatibility positive control. The transition matrix is
`test_native_transition_cannot_erase_recovery_barrier_before_restart`; it covers
empty, stale, invalid payload, 429, session-invalid and an actual post-epoch
closed-minute revision. Clock and toxic/context matrices cover the decoder
failure classes above. Four
`test_process_death_after_durable_probe_start_renews_cooldown_without_double_count`
cases cover TTL, D+1, config changes and currency rebinding through process death,
restart at +1/+899 seconds and safe read-only recovery at +900 seconds.
The rotation-pressure
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
| `f02-final-focal.xml` | Historical first freeze, 119/119 GREEN | `219cbb294f2cf298546d2d03afeca3d566d8d243a47a1233dfd0c8f39120871e` |
| `f02-recovered-truncation-red.json` | Actual premature PAPER fill after two fresh source minutes | `92d650cec234876e87da7022c290dbc1ba578db45aeeb530937aced601ef85f0` |
| `f02-recovered-state-corruption-red.xml` | Revoked B head, 14/14 RED | `da996aa79439bf01fda9ac6ceeae63f791a76706f67f80167d60ac1994ae49eb` |
| `f02-recovered-state-focal.xml` | Repaired non-native pass, 48/48 GREEN; no skips | `00c54a8422063c310e6729e5daff51ba79734522afe89b3e20d98f9f9649e155` |
| `f02-empty-transition-red.xml` | Actual premature PAPER fill after native EMPTY transition | `2cbaeae147fe6178d078e5595fc13a2332dc45ef14d9e982e752c28116e3465d` |
| `f02-native-clock-red.xml` | Native checked-clock corruption escapes worker | `a56246537946f61168ad8f82911eca3005ebed5769d22b6db5b5230eb68340d9` |
| `f02-nonfinite-lineage-red.xml` | Nonfinite origin fails serialization and recorder | `3542d4f6db85cc29f1b46a12408a9a34acef67fe9d4a72dc7cc2dc05c0b1be7b` |
| `f02-invalid-context-red.xml` | Four malformed session/fingerprint early probes | `99e7e8f24b5d0fd0a731579768125a9802e9a4214e520bbcc9fcf438306a15c9` |
| `f02-pending-restart-red.xml` | Process death permits repeated wire at +1 second | `05c80ecc69107f608932ead40b4e04082965f14a323d6f2fbffc911a86fd2d1d` |
| `f02-complete-nonnative.xml` | Decoder/transition pass, 70/70 GREEN; no skips | `398e12f550d707a0452e5ed078165afa33655e9e43a9b5aed94c406ae35e7ec9` |
| `f02-durable-attempt-matrix.xml` | Seven TTL/restart/death checks GREEN; no skips | `87a1e46a34d6a62b84139e975e78ad4926f0c21b1d18032f614a39122fd71144` |
| `f02-local-auth-masquerade-red.xml` | Local clock text wrongly rotates authentication | `76c3fa0e3523047100955b4e8f300ee293a5b04f264c065b7fcf374a10a01823` |
| `f02-local-auth-masquerade-green.xml` | Actual-worker local/native diagnostic separation GREEN | `e34120c10c11fb18ea569ff23407cb54b697132c7e053a583a5d9820064c2cf0` |
| `f02-final-repaired-a-ade87a8.xml` | Final repaired focal, 186/186 GREEN; no skips | `9ab4f9244ceacbe0246ca81a8fa4b7bc49766ab5ec12f4535854ecafb608ded8` |

The local passes use the pinned dependency environment with Python 3.12 and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. The historical first freeze covered 39 F-02
cases and 80 existing checks in 17.49 seconds. The repaired final run covers
81 F-02 cases, including six native SDK cases bound to the replacement released
A head; 75 cases are independent of that native transport API. All 186 checks
pass in 20.869 seconds with zero errors, failures or skips: the 81 F-02 cases and
105 existing checks. The latter cover HF3/intraday integration, freshness,
contract policy, SHADOW wiring/entry, dashboard semantics, factual approved
callers and capacity promotion. Compile and `git diff --check` also pass.
The authoritative complete Python 3.11 suite and exact artifact/Predeploy V2 are
the integration owner's remaining gates; focal results do not claim their result.

`real_orders_sent=0` is asserted through the real temporary observer state on
every harness run. Native wire evidence excludes order and Account Movements
routes and verifies that credentials, SDK tokens and broker bodies are absent
from recorded telemetry. There were no provider calls, product DB writes,
production/runtime mutations, front PRs, pushes, merges or deployments.
