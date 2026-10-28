# POROTA TRADING — RC4 HF2

Version: `17.0.0-rc4-hf2`
Mode: `PRODUCTION_PAPER`
Execution: `SIMULATED`
Real-order capability: `BLOCKED`

## Scope

HF2 consolidates the proven Contract Evidence host runner into the repository, adds explicit Argentina operating windows, and corrects dashboard presentation semantics without changing trading logic.

### Contract Evidence
- Dynamic collection: Monday–Friday 10:40–17:00 America/Argentina/Buenos_Aires.
- Static/full-browser evidence: outside the dynamic window on weekdays only.
- Weekends: no authenticated browser collection.
- Missing contractual fields remain fail-closed and are never inferred.
- Auto activation remains disabled.
- Existing same-context browser safety remains: bounded auth, strict non-read blocking after auth, automated 2FA fail-closed, real orders zero.

### Dashboard
- `/en-vivo` uses `paper_decisions` as the primary live decision stream.
- BUY gates remain event-driven secondary evidence.
- Closed-operation drill-down shows only operations closed on the current Argentina-local date.
- Open operations remain visible regardless of opening date until closed.
- T+1 rows already beyond the conservative settlement boundary are not listed as pending.
- Validation collecting-evidence is neutral/informational.
- Historical partial coverage and event-driven learning are presented as progress/informational states.

### Safety
- No production-real path enabled.
- No PPI order capability added.
- No DB migration in HF2.
- No inferred contract terms.
- Rollback image/container retained.
