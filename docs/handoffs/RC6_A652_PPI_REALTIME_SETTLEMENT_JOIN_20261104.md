# A652 — PPI realtime emitted settlement: OFFHOST five-key PoC

04/11/2026. Isolated DRAFT stacked above #931. Never deployed, never opened an HTTP request, WebSocket, subscription or touched external PPI Watch.

## Official PPI API contracts, actual distinction

- Official [PPI REST documentation](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/), `GET MarketData/SearchInstrument`: response lists `ticker`, `type`, `market`, `currency`, `description`; **no settlement**. Its query options cannot be silently reclassified as provider response.
- Official [PPI Python SDK documentation](https://itatppi.github.io/ppi-official-api-docs/api/documentacionPython/), “Realtime Subscription to Market Data” and `Response Books` (around lines 941–1050): realtime event **includes** `Ticker`, `Type`, `Settlement`, `Date`, `Bids`, `Offers`, `Trade`; `Date` example is timezone-aware `-03:00`. Official [Python example](https://itatppi.github.io/ppi-official-api-docs/api/ejemploPython/) reads `msg['Settlement']`.
- **Bootstrap circularity:** the official example calls `ppi.realtime.subscribe_to_element(Instrument(ticker,type,settlement))`. The subscriber must supply the settlement **before** receiving the event. Thus PPI's emitted `Settlement` is a useful observed field **but may echo the requested selector**: it cannot independently discover unknown tenors, validate that they are tradable or prove the server did not merely reflect subscription state. Nothing in the docs establishes an unlimited quota of subscribers or that 8,821 instruments can be subscribed simultaneously.
- Push message lacks documented `Market` or `Currency`; only an independently obtained PPI `SearchInstrument` result can supply those. If same `(ticker,type)` has more than one `market/currency` sibling, no safe cross-source selection exists: **hold both**.

## Proposed architecture (no authority promotion)

Pure function `rc6_ppi_realtime_settlement_join_offhost.propose_five_key` accepts a previously staged Search response, one Book-shaped realtime event and explicit preselected subscription selector. It checks:

1. Strict supported ticker/type/settlement selectors and **literal** emitted ticker/type/settlement equality; unknown or contradictory fields block.
2. Exactly one unambiguous Search `ticker/type/market/currency` four-key; contradictory market/currency siblings block even when stream settlement matches.
3. One trade-disabled Book-shaped event envelope, aware native `Date` (Argentina offset accepted), bounded Search→Book→receipt→evaluation clock order and age. The caller claims receipt clocks; they are **not independently signed**.
4. Exact structural candidate may be assembled as `ticker,type,market,currency,settlement`, **but** every output remains `EVIDENCE_ONLY_NO_GO`, `full_five_key_authoritative=false`, `settlement_independently_discovered=false`, `source_capture_signature_verified=false`, `provider_session_authenticated=false`, `pit_book1_decision_book2_verified=false`, `paper_instrument_ready=false`. Runtime order count is `NO_VERIFICADO`; operations performed = 0.

The supplied `subscription` already contains settlement; this does **not** prove PPI emitted that value independently of a request. The code is an analytical prototype to prevent accidental false-ready in a future capture/replay pipeline; **it is not a replacement** for `ppi_full_key_evidence_reason` or for original Current/Book/PIT guards.

## Synthetic tests and negatives

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -o addopts='' tests/test_rc6_a652_ppi_realtime_settlement_join_offhost.py
```

Local synthetic result: **16 passed**. Cases cover one matching Search4+push3 result, currency/market sibling ambiguity, cross-family same ticker, duplicate identical Search, absent/mismatched settlement/type/ticker, missing/invalid Book envelope, naive/future/stale timestamps, no Search key, malformed market/currency and missing/unknown subscription tenor. No broker, SQLite, sockets or provider GET. All cases, including matching positive, are non-admitting.

## Conditions before proposing runtime integration

1. **PPI Watch boundary:** independent owner and deployment topology verified; current external Watch systemd unit and provider consumption remain **NO_VERIFICADO**. A new socket/subscription may compete for provider session/limits even if it uses no REST GET. Do not connect or alter Watch to test this.
2. **Budget/availability:** independently verify permitted realtime subscription count, session sharing, rate/backpressure, reconnection, authorized source endpoint and credential boundaries with PPI documentation/support or existing passive receipts. 8,821 simultaneous subscriptions is **not established**.
3. **Capture authenticity:** future protected producer must pin signer, Git SHA, immutable artifact digest, source session and receiver clocks independently; a self-signed payload is never proof of provider source. `Settlement` stream emit may only attest request echo unless API explicitly guarantees validation.
4. **Five-key identity:** Search four-key and emitted three-key must bind with no duplicate market/currency siblings. Unknown settlement bootstrap requires another authoritative source/protocol; never guess from DEFAULT_SETTLEMENT or ticker or mark `PPI_FIELD` from request.
5. **Economics/PIT:** quote/book positive depth, versioned economics, strategy, risk, single writer, PPI Book1 `known_at` ≤ decision, independently persisted decision receipt BEFORE Book2, Book2 strictly after latency, same session, price tick/fees/volume and family-specific fill policy all remain necessary. Realtime structural join alone certifies none.

**State:** `DESARROLLADO_LOCAL` until DRAFT GitHub; source docs `DOCUMENTED_SCHEMA`, live push `NO_VERIFICADO`, five-key executable authority `BLOQUEADO`, PIT `NO_VERIFICADO`, PAPER readiness `NO_VERIFICADO`. No Predeploy V2 or Deploy V2.
