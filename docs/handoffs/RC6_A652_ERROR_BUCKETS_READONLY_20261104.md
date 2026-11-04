# A652-R15 — Safe PPI DATA_ERROR class buckets, control-plane only

**04-Nov-2026 / owner:** `A652_ERROR_BUCKETS_RO`, `WRITE_OWNER` restricted to exactly:

- `scripts/rc6_a652_watch_budget_micro_readonly.py`
- `tests/test_rc6_a652_watch_budget_micro_readonly.py`
- `docs/handoffs/RC6_A652_ERROR_BUCKETS_READONLY_20261104.md`

Base main: `f988075a54c15b6f2d3b11d3965cb3856601b49a` (A652 v1 merged). Isolated branch: `codex/a652-error-buckets-readonly-20261104`. Related [A652 source/Watch audit in #732](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6641390601).

## Verified incident and limits of the evidence

Existing A652 [run 41908698382](https://github.com/mbalbo2023/Porota-trading/actions/runs/41908698382), authentic **sanitized** artifact `13361632072` (2,225 bytes), recorded the latest completed scanner cycle 16:59:28–16:59:57 ART:

- `eligible_total=8948`, `selected_count=20`, `successful_count=3`, `failed_count=7`, `DATA_REJECTED=10`.
- Seven DATA_ERROR events: `PPI_EMPTY_OR_NON_JSON_AFTER_RETRY=2`, `PPI_INSTRUMENT_NOT_FOUND=1`, `PPI_OTHER_CLASSIFIED=4`; `sample_truncated=false`, `metric_reconciliation=MATCH`, no observed `PPI_BUDGET_BACKPRESSURE` or `HTTP_BLOCKED` in that cycle.
- Budget sidecar `ABSENT` (A516 metadata only); observer at capture `WAITING_MARKET/MARKET_CLOSED`, `ppi_auth=OK`, `real_orders_sent=0`, cumulative `http_allowed_counter=9049` (pre-send counter, **not successful provider GETs**). Systemd matched zero PPI+Watch candidate units but external Watch identity/quotas remain **NO_VERIFICADO**, never `VERIFIED_ABSENT`.
- Product `bd_ppi_readonly_guard.py::classify_read_error` uses a fixed classification for budget/session/instrument/HTTP/JSON cases, then falls back to `"PPI_" + type(error).__name__.upper()` for every other exception. A652 v1 collapses every unknown safe `PPI_*` suffix into one `PPI_OTHER_CLASSIFIED` count. Additionally `ops_publish_introspection_rc6.py` deliberately removes `detail` from public `ppi_errors_1h` rows.

**RCA of diagnostics, not the underlying provider outage:** the four exact class labels have been intentionally destroyed by sanitization; we **cannot** retrospectively decide whether they were timeout, connection, local schema/type error, or OS/file failure. Neither A652 v1 artifact nor published runtime introspection permits attribution. No PPI GET, fresh host capture or database mutation was performed for this analysis. Do not backfill these four counts into any new bucket.

## FIX-FORWARD, GUARD and contractual scope

A652 v2 recognizes only exact `PPI_` plus known class names from the product fallback, then publishes **fixed aggregate symptoms**, not the original class name:

| New bucket (v2) | Exact class examples | Meaning and non-meaning |
| --- | --- | --- |
| `PPI_TIMEOUT_EXCEPTION` | `PPI_TIMEOUTERROR`, `PPI_READTIMEOUT`, `PPI_CONNECTTIMEOUT` | Timeout class observed; no conclusion about PPI provider or quota. |
| `PPI_NETWORK_EXCEPTION` | `PPI_CONNECTIONERROR`, `PPI_REMOTEDISCONNECTED`, `PPI_PROXYERROR` | Network-related class observed; not a confirmed provider response. |
| `PPI_SCHEMA_OR_TYPE_EXCEPTION` | `PPI_KEYERROR`, `PPI_TYPEERROR`, `PPI_ATTRIBUTEERROR` | Local class suggests mapping/shape/type symptom; cannot claim provider schema error. |
| `PPI_LOCAL_OS_EXCEPTION` | `PPI_FILENOTFOUNDERROR`, `PPI_PERMISSIONERROR` | Explicit local filesystem/OS exception class; no inference about PPI API. |
| `PPI_OTHER_CLASSIFIED` | Every nonlisted safe `PPI_[A-Z0-9_]+`, including ValueError or OperationalError | Required fail-closed residual; never forced into a category. |

Not a change to `bd_ppi_readonly_guard.py`, error production, retry, requests, PPI budget, sidecar, scanner business logic, contract/admission gates, other sources, motors, PPI Watch or Deploy V2. A652 remains one **opt-in READ_ONLY** GitHub Actions host diagnostic on main, with private RO mount, no host-written files, no GET, lease or orders. The code has no automatic host run at PR/merge time.

The receipt/host schema is explicitly advanced from A652 **v1 to v2** because `data_error_reasons` gains four keys; historical v1 artifacts remain unchanged and must not be reinterpreted as v2. The host output remains bounded (<16 KiB), fixed-key allowlist only, with duplicate-key/type guards. Suspicious class names containing token-like strings, suffix/prefix collisions, unknown classes, symbols and paths cannot escape into artifact output.

## TEST / CI / evidence and next boundary

- Local offline `pytest`: 21 A652 tests GREEN, 18 subtests, including exact-match categories, unknown-class fallback, secret injection, no symbol/unit leakage, v1/v2 separation, mount/PAPER identity fail-closed, raw/JSON-size bounds, duplicate JSON keys and module-alias collision.
- GitHub CI status: **pending** until PR head SHA and full-suite runs are attached here or in #732; local tests do not imply GitHub or runtime validation.
- Acceptance: isolated DRAFT PR to main; formal owner receipt with the three exact paths; GitHub full-suite and checkpoint attestation GREEN; no merge without integration governance; no action dispatch until separately authorized and needed. The existing A652 run and A626 run must **never be repeated** just to test this classifier.
- Any future genuine A652 v2 capture must include exact run/artifact SHA, UTC cycle timestamps, metric reconciliation and only aggregate symptom buckets. A nonzero category count is a lead for code/RCA investigation, **not** a PAPER admission or PIT/Book GO.

**Status:** DESARROLLADO locally, runtime NO_VERIFICADO. PAPER/SHADOW ONLY; no real orders, PPI Watch unchanged, FIX-FORWARD. Ownership released after the PR/CI handoff; deploy ownership remains external to this branch.
