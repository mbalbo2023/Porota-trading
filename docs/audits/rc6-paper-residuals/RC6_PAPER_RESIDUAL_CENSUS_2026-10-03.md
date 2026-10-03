# RC6 PAPER residual census — 2026-10-03

Base runtime: `deploy/rc6-pr69-isolated-20260915@da697c6e6c2274579f9e4a112fabc4327475dd35` (VALIDADO_RUNTIME).

## Evidence

- Census Actions: `37133980328` — SUCCESS.
- Census artifact: `11277786088`, digest `sha256:2e6f241ded4738fa22b957b945a61da61225eb8e6c87458491930ec2d53cfcd3`.
- Offline analysis: `37134079802` — SUCCESS.
- Analysis artifact: `11278400398`, digest `sha256:d6f70bdf537e79fdb279d4ed68a02b8315c78596fe3c1b71695ba4a157fa1c9d`.
- Runtime read: query-only, bounded, 0 DB writes, 0 broker calls.
- No readiness promotion was performed by this workstream.

## Current census

Total identities: **14,294**. READY/can_simulate: **9,710**. Non-simulable: **4,584**.

| Family | READY | Non-simulable |
|---|---:|---:|
| ACCIONES | 162 | 35 |
| CEDEARS | 1,237 | 1,179 |
| BONOS | 2,342 | 597 |
| LETRAS | 37 | 11 |
| OBLIGACIONES | 3,560 | 1,342 |
| FCI | 1,060 | 22 |
| OPCIONES | 1,307 | 1,101 |
| CAUCIONES | 5 | 5 |
| FUTUROS | 0 | 200 |
| ON legacy | 0 | 91 |
| INDICES | 0 | 1 |

Most residual rows are complementary BYMA/IOL/legacy identities and may not override PPI identity.

## Futures RCA

Of 200 blocked futures, 148 are current AVAILABLE PPI_PRIMARY identities and 52 are stale/unverified.

For the 148 current identities:
- 134 already contain PPI_STRUCTURED_API + DERIVED_OFFICIAL_RULE evidence but remain BLOCKED by missing `cash_multiplier`, `expires_at`, and `underlying`.
- 14 additionally lack minimum/step and the PAPER margin policy.
- Contract evidence is not the only blocker. The live scanner calls `PaperBroker.on_quote`, while `be_paper_engine.PAPER_POSITION_FAMILIES` intentionally excludes FUTUROS and returns HOLD because futures require a specialized financial lifecycle.
- `rc6_paper_family_lifecycle.FamilyPaperExecutor` already provides a PAPER_ONLY/idempotent futures state machine (OPEN → MARGIN_RESERVED → DAILY_VARIATION/MARGIN_* → CLOSE/EXPIRY) with no real-order route, but it is not wired to the live PAPER scanner.

Therefore a contract-only bulk promotion would be misleading and is prohibited by this audit.

## Safe next scope

Start with exact standard contracts only. For DLR, A3's current official product guide establishes a USD 1,000 contract size, one-contract trading unit, ARS quotation, cash settlement, and periodically changing real margins. Current PPI identity/description is used only as exact identity evidence; spreads, `M`/`A` variants, stale identities and 2027 expiries without an exact published calendar stay fail-closed.

Broker margin remains NO_VERIFICADO. The existing simulator policy `CONSERVATIVE_NOTIONAL_RATE=1` may reserve full notional only in PAPER, never masquerading as a broker term.

Before any readiness promotion, the specialized futures PAPER lifecycle must be bound end-to-end with fresh book/session/risk/expiry/exit guards and tests proving `real_routes_used=[]`.

## Status

This document is AUDIT_ONLY. No product branch, runtime, DB, systemd, Docker or PPI Watch mutation was made.
