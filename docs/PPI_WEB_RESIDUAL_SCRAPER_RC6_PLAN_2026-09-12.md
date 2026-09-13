# RC6 — PPI Web residual scraper preparation

Status: PREPARED_SCAFFOLD_ONLY
Branch: `ops/rc6-ppi-web-residual-scraper-20260912`

## Objective
Prepare a strictly read-only PPI Web residual pipeline to complement the canonical PPI API historical ingestion only after the API pass is complete and the exact residual manifest is generated.

## Non-negotiable safety rules
- Read-only navigation only.
- Never submit buy/sell/orders, cauciones, FCI subscriptions/redemptions, options/futures orders, bids or tenders.
- Never change account, security, password or 2FA settings.
- Never store or log credentials, OTPs, cookies, session tokens or secrets.
- Do not run a second historical writer while the PPI API historical writer is active.
- Do not scrape the whole universe blindly: consume only the residual manifest produced after API closeout.

## Pipeline stages
1. Finish PPI API pass across the 1960 canonical identities.
2. Closeout API ingestion and classify every residual identity into:
   - NO_PROVIDER_ROWS
   - PROVIDER_INVALID
   - PARTIAL_VALID
   - HARD_PROVIDER_ERROR
3. Generate immutable residual manifest keyed by symbol / instrument_type / market / settlement.
4. PPI Web authenticated read-only discovery for only residual identities.
5. Extract historical rows and contract/economic metadata where visible.
6. Normalize source evidence without repairing or synthesizing OHLC.
7. Reconcile against canonical API history, preserving provenance and rejecting duplicates.
8. Produce a second residual manifest.
9. Use IOL only for identities still unresolved after PPI Web.

## Prepared interfaces
The scraper scaffold expects a JSONL residual manifest. Each row must include:
- symbol
- instrument_type
- market
- settlement
- residual_class

Optional diagnostic fields may include provider_rows, valid_rows, rejected_rows and detail.

## Output contracts planned
- raw PPI Web evidence: append-only, source-attributed
- normalized candidate rows: no synthetic OHLC
- contract metadata: source-attributed, per-family
- reconciliation report: API vs Web vs remaining residual
- no direct promotion to executable status without existing family-specific validators

## Family handling
The Web layer is specifically intended to complement API gaps for families such as bonos, letras, ON, opciones, futuros, FCI, ETF and other PPI families discovered in the canonical universe. Presence in Web does not make an identity executable by itself; contract completeness, liquidity/risk and family-specific validation remain mandatory.

## Current execution state
No mass scraping is started by this scaffold. It is safe to prepare while the API ingestion is still running because it neither writes historical data nor touches the running writer.
