# History ownership / scheduling conflict — 2026-09-24

Status: **OPEN_CURRENT_CONTRACT_CONFLICT**

Scope: read-only architectural audit. No runtime, PPI Watch, database, systemd or production behavior was changed.

## Evidence

The repository currently expresses several different contracts for historical ingestion:

1. `systemd/porota-history-postclose-rc6.service` is quarantined by default and requires
   `/etc/porota/rc6-postclose-history-explicitly-authorized` before it can run.
2. `az_maintenance_scheduler.py` explicitly blocks internal historical write jobs
   `historical_refresh` and `historical_refresh_if_needed`, and schedules no startup/post-close catch-up.
3. `bg_paper_dashboard.py` states:
   **"Ingesta full histórica PPI: cerrada y en cuarentena."**
4. The always-on observer still calls `_daily_sync(reader, store)` and
   `_background_ingest(reader, store)` while market phase is CLOSED, and reports
   **"ingesta histórica disponible"**.
5. `_background_ingest_due()` currently implements at most one PPI historical attempt
   per business date after market close. It does **not** gate using
   `BACKGROUND_INGEST_SECONDS`.
6. Configuration nevertheless exposes competing cadence values:
   - observer / `.env.example`: `PPI_BACKGROUND_INGEST_SECONDS=21600`
   - mode-manager / scheduler catalog defaults: `7200`
7. `_background_ingest()` still reports "próximo intento no antes de N h", even though
   the implemented gate is date/post-close based.
8. `de_scheduler_catalog_hf6.py` still exposes `PPI_BACKGROUND_INGEST` and
   `PPI_PRODUCTION_HISTORY` as active internal jobs with cadence, despite the
   quarantine/blocking statements above.

## Interpretation

This is not treated as a test-only problem. It is a migration/ownership conflict between:
- one-time/full historical backfill;
- ongoing PPI historical refresh;
- the post-close History Store job;
- the live candle worker / locally accumulated evidence;
- dashboard scheduler representation.

Until ownership is reconciled, no automated cleanup or refactor should silently enable,
disable, or redirect historical writes.

## Required resolution before Deploy V2 promotion

Define exactly one post-cutoff owner for each dataset:
- historical backfill;
- daily/post-close candle persistence;
- live/intramarket candle accumulation;
- PPI read-only reconciliation.

Then make observer, systemd, scheduler catalog, dashboard labels and tests derive from the
same machine-readable policy.

## Safety

- PPI Watch remains out of scope and must not be modified.
- No real orders are involved.
- Fix-forward only.
- No runtime rollback.


## Root-cause history confirmed from Git commits

The observer behavior was deliberately migrated on **2026-09-22**:

- `a2916036da0401c4688486f1cec6b3419587e0b6` — made PPI history capture incremental, introduced the one-time cutoff completion guard, and added a writer lock.
- `f59f0d6e860e22f91c88687b07e5fcf07457c0bf` — replaced the old TTL-based eligibility with **at most one attempt per business date after market close**.
- `69c75ba74d54cae89f641693e4e2b0be6f9c8d3d` — made `force=True` respect the same daily close guard.

Therefore the daily post-close gate is intentional current behavior. The migration debt is that comments, dashboard/catalog cadence descriptions, and configuration defaults were not all updated consistently.

A final test-fixture issue was also isolated: a synthetic candle stamped `2026-09-24T17:00:00-03:00` was actually `20:00 UTC`, later than the CI execution instant, so the production validator correctly rejected it as future data. The fixture is moved to an already-observed timestamp; production validation logic remains unchanged.
