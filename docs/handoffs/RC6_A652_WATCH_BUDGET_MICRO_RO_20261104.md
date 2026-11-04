# A652 — PPI Watch / PPI budget / scanner last-cycle microdiagnostic (04-Nov-2026)

## Scope and immutable starting point

- **Ownership:** A652 `WRITE_OWNER`, exclusively the 4 new control-plane files named here. Branch `codex/a652-watch-budget-micro-ro`, base `main` `9e099bc1d2cab1183872475f4e2bf8b13e28730c`; no modifications to product, candidate #778, integration #929, or PPI Watch.
- **Input checkpoints:** [#732 A651-R53](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6640058408), [#909 A651-R52](https://github.com/mbalbo2023/Porota-trading/issues/909#issuecomment-6640015807), [#732 A652 PPI Watch/lease RCA](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6640891797), [#732 A652 observer and telemetry semantics](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6640915962).
- **Already executed, do not repeat:** A626 run `41895877546`, artifact `13355368533`; A331 capture.
- **Product/runtime line under audit:** product SHA `7e1e744d6ca9ab30c51b5747dd4467af5ece0145`. A GitHub SHA or CI GREEN never proves live runtime health.

## Why PPI Watch needs a diagnostic, not an asserted identity

Product `ops/policy/host-control-plane-reconciliation-v2.json` records owner `EXTERNAL_SEPARATE_OWNER`, `mutation_allowed=false`, `systemd_presence_expectation=UNKNOWN`, `expected_unit_names=[]`. Therefore neither a matching service name nor zero matches proves its identity or absence. A separate product script `ae_ppi_api_watch.py` monitors API change; `rc6_market_watch_silence.py`/`rc6-market-watch-readonly.yml` inspect scheduled/market observability. Those are not proof of the independent external PPI Watch service. To prove external identity and quota use, a future versioned inventory must record a canonical unit and an attribution receipt without exposing credentials.

The "no tocar PPI Watch" operating guard protects a service whose *effective identity and actual PPI quota impact are still unverified*. This is an explicit preservation boundary during PAPER remediation, **not** an assertion that PPI Watch is healthy, harmless, or necessarily running.

## Proposed single-capture workflow (control-plane only)

Files:

1. `.github/workflows/rc6-a652-watch-budget-micro-readonly.yml` — PR runs synthetic tests only; host job is manual `workflow_dispatch` from `main` with boolean opt-in **false** by default and shared `rc6-unified-paper-deploy` mutex. No schedule. No deploy or merge action.
2. `scripts/rc6_a652_watch_budget_micro_readonly.py` — single bounded SSH/stdin host probe. Pre-existing A617 private mount namespace is remounted read-only before any SQLite URI `mode=ro`; reuse A617 guards for mount and PAPER V17 DB identity. Existing A516 `sidecar_kind` is loaded **in memory** to `lstat` the sidecar metadata. No `docker exec`, sidecar SQL, import of provider SDK, PPI GET, lease, brokerage, PPI Watch restart/inspection of ExecStart, or host file creation.
3. `tests/test_rc6_a652_watch_budget_micro_readonly.py` — offhost sqlite fixture, systemctl stubs, adversarial raw secrets, fail-closed gate, mount/identity mismatch, and no business authority.

The SSH job sends A617, A516, and A652 source through standard input as ephemeral in-memory modules; it creates **no files on the host**. It enumerates systemd unit-file names with fixed PPI+Watch discovery tokens but publishes **only the number of candidate and active services** (capped, without names). `expected_unit_names=[]`, so Watch identity and its share of PPI calls remain `NO_VERIFICADO`, including when exactly one candidate is found.

SQLite returns from the **last recorded scanner cycle only**: selected/success/failed counts and timestamps, observer mode/state, reader `http_allowed`/`http_blocked` counters, COLD bootstrap state from A617, and sampled `paper_events` categories. `DATA_ERROR` detail is parsed in memory after the final `: ` and reduced to a fixed PPI error bucket; symbols, details, arbitrary exception classes, URLs, tokens, and SQL never leave the process. The observation is **not** evidence of successful GET, real orders, or PAPER readiness.

The budget result is intentionally limited to A516-style `sidecar_kind`: `ABSENT`, `REGULAR_SINGLE_LINK`, `UNSAFE_OR_ALIAS`, or `UNAVAILABLE`. It explicitly does **not** certify effective PID1 environment, service reservations, quota consumption, or sidecar snapshot validity. A626's existing budget/PID1 metadata remains the richer, separately pinned historical receipt; A652 does not rerun A626.

The runner rejects raw host output at 16 KiB, reduces again to a fixed allowlisted JSON structure under 16 KiB, uploads **only** that sanitized artifact (7-day retention), and destroys runner-only SSH credentials and raw stdout. Any missing RO mount, PAPER identity, systemctl metadata, or schema remains `NO_VERIFICADO`/`INCOMPLETE_NO_GO`. The completed receipt can be `DIAGNOSTIC_OBSERVED_NO_PAPER_GO`; there is intentionally no GO state.

## State and interpretation

As of proposal preparation, code is local/branch-pending and tests must be independently verified; there is **no A652 host run** and no new PPI quota evidence. Last previously reported observer sample at 16:35:01 ART was `process_state=DEGRADED`, `ppi_auth=OK`, with 144 grouped DATA_ERROR events and economics zero in a different window. Runtime source details did not permit cause attribution. A652 distinguishes `PPI_BUDGET_BACKPRESSURE`, session, not-found, HTTP 4xx/5xx, decode error, other PPI classes and nonclassified exceptions from the *latest scanner cycle*; the output can guide the next code fix but cannot itself enable 821 contract-gap instruments or certify PIT Book.

**New separately verified A626 evidence, not a new probe:** [#732 R11](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6641265668) records authentically recovered artifact `13355368533`, checksum-confirmed, and A516 budget snapshot at **16:20 ART**: `SIDECAR_ABSENT`, `sidecar_kind=ABSENT`, `policy_file_mode=OFF`, `runtime_effective_policy=NO_VERIFICADO`. A652 can test whether that filesystem metadata remains absent later; it must not transform this into a causal claim about external Watch or actual quota.

**Acceptance before one manual run:** PR DRAFT to `main`; exact SHA tests GREEN, code review RO, no branch scope overlap, explicit owner dispatch after merge by normal workflow. Parent deploy owner controls sequencing. Do **not** execute now; no Predeploy V2/Deploy V2 in this scope. On a future run link exact run/artifact and reconcile timestamp, sidecar, Watch discovery, error buckets, observed runtime SHA and business remediation in #732. Preserve `PAPER/SHADOW`, `real_orders_sent=0`, FIX-FORWARD, PPI Watch unchanged.
