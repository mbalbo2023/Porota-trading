# WS-MOTOR-09 — candidate review

State: PARTIAL / DRAFT / HOLD FOR INTEGRATION. PAPER/SHADOW only. No merge or deploy.

Base: a03142bc440bbb871714e4ae931d73513d073a56. PR #357 was reviewed at
98ecf90859fe33dd66986b70f1c0010b0321fdb9 and remains untouched.

## Problem and changes

The observer could select catalog rows that the candidate ledger rejected. The
candidate now persists the decision at the full catalog identity in
`candidate_identity_v2`. Selection and lookup use that same gate. Distinct
settlements resolve by the operation; currency/market ambiguity in the current
three-field Quote API remains blocked. The legacy summary stays available for
compatibility and must not be treated as the full-key admission decision.

Contract Evidence v2 now feeds a read-only adapter into catalog reconciliation.
It verifies evidence hashes, declared sources, exact identity, field provenance,
current observation, changes and essential conflicts. A blocked claim invalidates
an older contract, including conflicting currency claims. Contract minimum and
increment are independent and consumed by PaperBroker. Numeric formatting alone
does not create a conflict. Generic spot cannot become a future, fund or caucion.

An explicit `PaperBroker.place_caucion_from_evidence` operation reads the current
v2 cache, verifies its persisted primary identity and pending changes, builds a
CaucionOffer with a fresh executable timestamp and explicit principal/fee budget,
and uses the existing PAPER placement/maturity ledger. No automatic allocation,
new policy, external order client or runtime writer was added.

The public BYMA collector follows response-declared page numbers and verifies
source totals. Public-bonds returned 563 records over 3 pages, previously only
189. Full capture: 3424 rows vs 3050, preserving currency, book quantities and
raw settlement/time-only codes without guessing their meanings. Per-page hashes
and source/normalized totals remain in capture metadata. Provider errors and HTTP
errors do not produce successful records. No capture timestamp becomes a price
timestamp.

IOL fixed-income normalization no longer confuses quote units_per_lot with order
minimum/increment. Options also require separately sourced quantity terms and
premium price basis; adjusted/unverified series stay blocked. The collectors
rotate full fixed-income identities and option information requests. PPI prefix
rotation advances disjoint blocks (36 prefixes in 9 daily batches of 4), rather
than overlapping batches that required 33 days. Identity TTL remains 24h;
rotation coverage does not itself establish identity validity.

The descriptive CEDEAR conversion ratio and fund manager/custodian were moved to
enrichment in cq_family_contract_rules. Price/cash/quantity, lifecycle and risk
requirements remain. Dynamic false/CLOSED/nonpositive/future evidence fails
closed. Observed-only dashboard rows cannot authorize PAPER by a READY string.

## Evidence and limits

Live read-only IOL metadata/quotes were captured for GD30, YMCJO and D30N6,
a 116-row GGAL option chain, 22 FCI inventory entries and caucion rates. They do
not expose all independent quantity terms and executable timestamps required
for promotion. No live family is certified complete by these captures.

Official BYMA option circular/series, A3 DLR/RFX20 contract guides, Argentina
Clearing margins and Adcap class-B terms were read and hashed. They provide
contract fields, not PPI identity, current broker acceptance, executable books or
broker-specific margin. Full evidence is in the downloadable WS-MOTOR-09 handoff.

Synthetic integration tests traverse persisted v2 evidence -> real reconciler ->
full-key candidate -> lookup -> actual PAPER open/close. Caucion tests traverse
v2 cache -> exact primary -> existing placement -> locked cash -> maturity ->
idempotent settlement. These tests certify code paths, not real market terms or
profitability. Existing risk/classification controls stay active; the synthetic
GD30 test supplies an explicit synthetic sector-map fixture.

Remaining integration blockers (do not merge as a complete family promotion):

- `_candidate_has_ppi_primary` still supports legacy source markers. Durable
  endpoint/snapshot binding and its migration require review before deployment.
- Evidence v2's original current key lacks currency. The adapter rejects visible
  conflicts, but cannot recover a value already overwritten under the old key.
- cq_contract_readiness and cq_family_contract_rules remain divergent schemas;
  the enrichment correction in one evaluator does not certify the other.
- Existing catalog-backed dashboard comparison still lacks full identity binding.
- Fixed-income coupon/amortization event handling and options expiration/exercise
  are not certified by the intraday spot test. No assumption of forced liquidity.
- FCI subscription/redemption executor is absent in this chain. Futures have
  arithmetic but no integrated daily-variation/margin ledger executor here.
- Real caucion quote/depth/start/cost/step mapping and FCI exact class binding are
  still missing from accessible captured data. No synthetic term was inserted
  into production or presented as a recovered broker term.
- No current raw productive DB replay was possible from the sanitized published
  snapshot; A/B/C use a separately identified 12-row archived raw fixture. Global
  productive deltas are NOT VERIFIED, and runtime D was NOT EXECUTED.

The candidate is reviewable, not deployment-certified. CI must run against its
exact SHA and coordinator must resolve overlapping WS08 scopes before integration.
