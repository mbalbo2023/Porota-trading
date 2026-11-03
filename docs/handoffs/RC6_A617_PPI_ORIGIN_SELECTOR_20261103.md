# A617 — Control-plane PPI provenance / COLD selector (03-Nov-2026)

## Status and scope

- **Owner:** A617, #732; branch `audit/rc6-a617-ppi-origin-selector-readonly-20261103` against `main` only.
- **Change:** one self-contained bounded host READ_ONLY probe, adversarial offline `unittest` and one manual-only main workflow.
- **Local TEST:** 8/8 GREEN; GitHub CI/runtime must be separately verified by exact SHA and run ID.
- **Not touched:** product, candidate #778, `bf_production_paper_observer.py`, PPI Watch, A516/#898, Docker/runtime/systemd/DB, deployment control.

## ERROR → RCA

`A385 #41701703493` established that `PPI.present=0` in an already-published full-universe **JSON** for 10,317 catalog identities. Its `rc6-a385-ten-family-snapshot-readonly.yml` workflow explicitly does **not** read the operational SQLite DB and validates `db_opened=false`. It cannot distinguish:

1. no rows in host `market_snapshots`;
2. rows with `source=PRODUCTION_PAPER` (execution **mode**, not PPI broker provenance);
3. native current/book captured but no scanner receipt published/read;
4. missing/private/invalid scanner receipt;
5. no `candidate_identity_v2` READY-eligible identities or COLD slot; or
6. no genuine additional PPI GET budget/lease/EXIT reserve.

The candidate code at #778 contains a strict scanner receipt writer, native Book journal and `primary_rows()` join. Those changes are **EN_GITHUB**, not deployed; never imply host source ingestion or 0/positive fills from their existence. Official PPI API existence is not evidence of a specific host response.

## FIX → GUARD

`scripts/rc6_a617_ppi_origin_selector_readonly.py` reads operational SQLite **only** via URI `mode=ro` + `PRAGMA query_only`, no copied or mutated WAL/SHM. It reports bounded samples of up to 4,096 latest `market_snapshots`, aggregate source labels and trade/book-clock presence. It runs the **exact** `_eligible_symbols` SQL grouping on `candidate_identity_v2`, with old `candidate_universe` fallback, and inspects the latest `universe_cycle_metrics` COLD/active-budget plan.

Private scanner receipt and Book journal reads use `O_NOFOLLOW`, 0600, size caps (2/8 MiB) and stable stat bounds. A scanner receipt is **an origin assertion**; quote correspondence across exact five-key, native/received clocks, bid/ask/last/sizes is only a correlation diagnostic. The private Book hash chain is checked structurally; hashes are **not** provider signatures, nor evidence of causal Book2/fee/fill.

Every result sets `provider_gets=0`, `orders_called=false`, `admission_authority=false`, `causal_fill_verified=false`, `source_provenance_authorized=false`, effective PPI lease/EXIT reserve **NO_VERIFICADO**. Thus even a matching Book row cannot promote READY, new PPI requests, exits, or orders.

`.github/workflows/rc6-a617-ppi-origin-selector-readonly.yml` runs CI tests on PR, host path only on a **manual main dispatch**. It uses configured strict SSH from GitHub Actions, sends the script over stdin, discards remote stderr, sanitizes every public field to allowlisted aggregate JSON, retains a bounded artifact. No host script persisted, no Docker exec, provider API request, or runtime write.

## TEST → EVIDENCE

Eight synthetic SQL/file tests cover:

- A385 JSON zero despite one mode row and one eligible SQL candidate;
- matching scanner receipt and mode snapshot remain **non-authoritative**;
- mismatching native quote fields cannot correlate;
- private 0644 and symlink rejection;
- Book journal hash tampering;
- missing / zero eligible candidate selector without new GET;
- absent DB returns `NO_VERIFICADO`;
- bounded/truncated snapshot sample is explicit.

Before any host execution, verify GitHub PR `contracts=SUCCESS`; `host_provenance=SKIPPED` on PR is intentional. Merge to `main` only with current WRITE_OWNER and governance; then manual A617 READ_ONLY workflow dispatch, inspect exact GitHub artifact JSON. Do not run Predeploy V2, Deploy V2, extra PPI GET or PPI Watch.

## Unresolved authority / next FIX

**A617 does not by itself produce fresh PPI fills.** A positive host PPI row needs a trusted scanner receipt and source capture after future coherent deploy; zero eligible/COLD slot cannot justify extra provider calls. Authorized bootstrap requires an actual policy decision granting quota and EXIT reserve plus a valid lease/shared ledger—or a separately governed genuine PPI primary identity importer that creates exact READY contracts. For a causal simulated fill, still require Book1 native, recorded decision known-at, Book2 native strictly after decision+latency, verified quantity unit/depth, fee/tariff and deterministic no-fill/partial-fill receipts. None is fabricated here.

The 819 financial contract gaps, FUND_TERMS/TARIFF, fixed-income SELL, COLD2 deploy quiescence and #478 retention are **other owners/scopes**; they remain open, not closed by A617.
