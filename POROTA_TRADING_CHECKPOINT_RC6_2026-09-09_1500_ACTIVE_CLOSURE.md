# POROTA TRADING RC6 — ACTIVE CLOSURE CHECKPOINT — 2026-09-09 ~15:00 ART

Workstreams currently active in parallel:

- **W12 P0**: live baseline `9197aedf35fe1739b594c742902e80df069c47c9` certification workflow created; W12 live proof V2 created against that exact baseline. Candidate remains `/Cotizaciones/*` only, GET/HEAD/OPTIONS only, with `rc=4 -> reauth -> recollect` fix.
- **W10**: semantic sector-concentration binding proof created. Requirement remains BINDING for new PAPER entries only, never a reason to block exits/supervision.
- **W16**: final current-base readiness proof created for safe build-cache housekeeping; destructive data/image/container/volume operations explicitly forbidden.
- **W17**: formal current-base live UX proof created to turn already-live Wave8/UX functionality into a wave-specific evidence trail.
- **W18**: materialization v2 created to package control-plane components and explicitly reject any automatic rollback logic. No-auto-rollback policy documented in branch.

Absolute invariants unchanged: `PRODUCTION_PAPER`, `real_orders_sent=0`, no network order tests, no `/Operar`, no automatic rollback.
