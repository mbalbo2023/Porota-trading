# A651-R34 — PPI 5-key settlement authority at PAPER consumer (2026-11-04)

Scope: `bu_instrument_catalog.py`, `cr_contract_evidence_v2_mass_hf6.py`,
associated adversarial regression fixtures/tests. Owner and scope are declared
in issue #732 comment 6637288487 (extensions 6637399898/6637448031).

Baseline: candidate PR #778 `63beb5ff3e6dfbcbfe836deed406d15cd212347f`;
product `7e1e744d6ca9ab30c51b5747dd4467af5ece0145` unchanged.

## ERROR / RCA (code-reproducible, runtime NOT VERIFIED)

`normalize_record` keeps a SearchInstrument settlement request hint as an
instrument-catalog row when the provider payload contains no settlement. It
labels the evidence `REQUEST_CANDIDATE`; legacy `_candidate_has_ppi_primary`
legitimately treats that as *discovery* origin. Before this guard, consumers
mistook the requested settlement for an observed PPI full five-key:
`sync_candidate_universe` could publish `can_simulate=1`, final `lookup` and
`quote_terms` did not reject it, and `cr_contract_evidence_v2_mass_hf6` emitted
`PPI_STRUCTURED_API` evidence for the requested settlement.

An offline adversarial test (synthetic PPI response **without** settlement,
same ticker/family/market/currency) reproduced the premature `can_simulate=1`
projection. This is a code-level admission *possibility*, not evidence that
the actual droplet sent orders, had real PAPER admissions, or that PPI returned
the synthetic case during the 04-Nov session.

## FIX / GUARD

- Preserve `REQUEST_CANDIDATE` and unaltered historical discovery snapshots for
  SHADOW / scanner coverage. No PPI reader/recorder/policy changes.
- At PAPER candidate projection, final exact-key `lookup`, and `quote_terms`,
  require `PPI_FIELD` and matching literal five-key provider raw fields
  (ticker/family/market/currency/settlement), plus PPI_PRIMARY source marker.
- Request-only settlement fails closed with
  `PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED`; an inconsistent PPI_FIELD row fails
  with `PPI_FULL_KEY_NOT_PROVIDER_ATTESTED`.
- Mass Evidence-v2 refuses to emit full-key PPI contract records for those
  rows; it counts explicit skip reasons. Valid native `PPI_FIELD` keeps the
  existing evidence/contract path.
- Hard check at final lookup also closes a stale pre-fix candidate ledger
  where `can_simulate=1` until the next scheduler rebuild.

## TEST / STATUS

Before fix: 4/5 new adversarial tests failed because request-only or
contradictory full-key identities were promoted. After fix and correction of
legacy *positive fixtures* to contain genuinely explicit provider five-keys:
42/42 focused tests PASS (A651, A600, WS13, WS14, complement catalog),
`compileall` and `git diff --check` PASS. GitHub CI, artifact, deployment and
runtime validation remain **NO_VERIFICADO** until independent evidence.

This guard does not certify native PPI Current/Book timestamps, signed provider
PIT, execution economics, margins, NAV, futures/option series, or the physical
821 A626 gaps. `SOURCE_*` metadata cannot stand in for an authenticated PPI
receipt; PPI budget sidecar remains BLOCKED independently.

No real order route; PAPER/SHADOW ONLY; PPI Watch untouched; no deploy.
