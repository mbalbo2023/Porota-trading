# A652 — OFFHOST contract-term RCA and option-regression guard (04/11/2026)

## Authority, ownership, boundaries

- Integration baseline: [DRAFT #929](https://github.com/mbalbo2023/Porota-trading/pull/929) HEAD `94c634f295089e066cec7d0c138e72e38af511f9`; A652 scope is **one isolated follow-on DRAFT**, not `main` or `product`.
- Evidence: [A626 run 41895877546](https://github.com/mbalbo2023/Porota-trading/actions/runs/41895877546), artifact `13355368533` (sanitized consolidated aggregates). A626 execution is complete; **do not repeat** A626/A331.
- **PAPER/SHADOW only, zero real orders desired; runtime real-orders counter not verified by this audit.** No PPI GET, PPI Watch change, host/SQLite/runtime/DB mutation, GitHub workflow dispatch, merge, Predeploy or Deploy.
- The script is purely OFFHOST and operates on A626 JSON aggregates, optionally on separately staged evidence supplied locally; it has no network, database or broker interactions. Output has only counters and safelisted reason labels; no instruments, private source refs or payloads.

## Verified denominators and boundary of causal knowledge

| Family | A626 gaps | Terms present, but not admitted | Terms missing | No exact Current |
| --- | ---: | ---: | ---: | ---: |
| BONOS | 8 | 4 | 0 | 4 |
| OBLIGACIONES | 45 | 0 | 0 | 45 |
| OPCIONES | 576 | **568** | 4 | 4 |
| FUTUROS | 166 | **3** | 163 | 0 |
| CAUCIONES | 5 | 0 | 5 | 0 |
| FCI | 21 | 0 | 0 | 21 |
| **Total** | **821** | **575** | **172** | **74** |

All 821 are **unresolved** in the A626 diagnostic, not PAPER READY. These three partitions sum to 821; `terms_present` is a primitive presence check, **not** proof of valid amount/scale/effective date, provenance, complete contract, executable lifecycle, full provider five-key, PIT or Book. `provider_authenticity_checked=false` and `causal_pit_or_book_checked=false` in A626. The A626 artifact has **no itemized rejection records**, so it cannot establish the physical cause of any of those 575 at instrument level. Assigning the 568 options to a single guessed error would violate the evidence contract.

## Code-level diagnosis (hypotheses to verify, not explanations for 575)

`rc6_contract_bridge.normalize_group` already has these meaningful mutually *non-exclusive* failure labels; this analysis **does not change** any production decision:

1. Option-only IOL chain evidence `IOL_STRUCTURED_API` is ignored for contract admission without effective-dated, series-specific adjustment proof; output `OPTION_IOL_SERIES_CONTRACT_UNVERIFIED`, possibly together with `MISSING:*`. This is correct fail-closed behavior, **not** proof that all 568 come from IOL.
2. Missing / invalid numeric terms (e.g. `cash_multiplier`, `quantity_step`, `minimum_quantity`, `strike`, expiration, underlying), invalid provenance/hash, currency contradiction, identity conflict, missing future margin, unknown option lifecycle, future effective timestamps, changing series or conflicting source claims remain distinguishable by existing bridge reason labels (`MISSING:*`, `INVALID_*`, `CONFLICT:*`, `CHANGE_REVIEW_REQUIRED`, etc.).
3. Some supplied evidence can normalize yet remain blocked by a downstream catalog, strategy, quote/Book, temporal PIT or risk gate. Offline bridge normalization alone **does not prove PAPER admission**; report `NORMALIZED_BUT_CATALOG_GAP_UNEXPLAINED` until the downstream cause has independent trace and evidence.
4. The 74 missing exact Current and the 172 absent offline terms are **separate cohorts**; this script does not misclassify them as failed bridge validation.

To attribute the 575, provide a **separately staged, sanitized, immutable OFFHOST per-item diagnostic cohort** with copy custody / WAL checkpoint independently verified by the authorized existing pipeline, already linked to A626 run and five-key identity. A caller-controlled sample is *not* itself copy custody, provider authenticity or live PIT. The script accepts optional JSON schema `rc6.a652.offhost-gap-evidence-sample.v1` with `a626_run_id`, `measured_at_utc`, and `cases[]`; each case has `identity={ticker,family,market,currency,settlement}`, `catalog_capability`, and `records[]` containing compatible V2 evidence rows with full five-key plus provenance. The script is read-only and checks family-denominator bounds, full-key consistency, source envelope and sensitive fields; a row identity is never included in output.

Invocation on a **previously obtained local artifact**:

```bash
python scripts/rc6_a652_offhost_contract_gap_diagnose.py --aggregate /absolute/path/to/a626-sanitized.json
# Only if an independently checked OFFHOST per-item cohort exists:
python scripts/rc6_a652_offhost_contract_gap_diagnose.py --aggregate /absolute/path/to/a626-sanitized.json --sample /absolute/path/to/a652-offhost-sample.json
```

Never feed this script the runtime SQLite database, provider tokens, raw private logs or direct PPI GET output. A bridge classification from a synthetic row remains illustrative, not operational evidence. Unknowns are preserved as `unattributed` with `paper_instrument_ready=false`, `ppi_full_five_key_verified=false`, `causal_pit_book_verified=false` and `runtime_real_orders_sent=NO_VERIFICADO`.

## Additional RCA: 4 failed #929 option tests, not a production-guard defect

**ERROR** on exact #929 SHA: `pytest -q -o addopts='' tests/test_rc6_ppi_option_contract_policy.py` yielded **4 failures / 12 passes**. Prior test positives stored `settlement_source=PPI_FIELD` but raw `metadata_json` omitted `settlement` and literal full identity. The new `PPI_FIELD` full-key fail-closed check rejected these fixture rows correctly. The official PPI `SearchInstrument` documentation returns ticker/type/market/currency but **not settlement**; inventing settlement in real PPI responses or dropping that guard would be incorrect.

**FIX**: only synthetic positive fixtures now explicitly include all five fields with a marker `_fixture_synthetic_full_key=True`, accompanied by a comment disavowing real provider attestation. `REQUEST_CANDIDATE` negative models an actual search result without settlement and is rejected. **GUARD**: contradictory synthetic full key (market ROFEX vs catalog BYMA) rejected; normal PPI response still must fail closed without veracious settlement authority.

**TEST** local: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -o addopts='' tests/test_rc6_a652_offhost_contract_gap_diagnose.py tests/test_rc6_ppi_option_contract_policy.py tests/test_rc6_a651_settlement_authority.py` → **34 passed**. **State DESARROLLADO_LOCAL** until DRAFT PR / Actions evidence; this is not a claim of runtime validation. The unchanged governed Predeploy V2 discovers tests automatically; exact-candidate focused checks select changed test modules when triggered in their applicable PR scope. Do **not** trigger Predeploy while admission and sidecar blockers remain.

## Architecture: legitimate PPI settlement binding still pending

The official PPI REST `SearchInstrument` search listing lacks settlement. `Current` and `Book` take ticker/type/settlement in the request but their payload does not automatically echo the full five-part instrument key. A client-supplied request selector cannot be promoted to provider-emitted settlement. One viable design proposal for review is a cryptographically bound **request-response receipt**: verified PPI account/session, exact request method/URL/selector, unique nonce/correlation, raw response digest, fetch and receipt timestamps, expiry, effective marketplace mapping and negative sibling/ambiguous-series checks. This is a *separate authority contract requiring proof and tests*, not a field-level `PPI_FIELD` attestation. Do not mark current 8,000 prelim catalog rows READY until authority provenance is established and PIT `Book1 → decision → Book2` can be audited.

## Follow-on and acceptance checklist

1. Independent OFFHOST cohort for up to 575 records, no runtime DB or PPI Watch writes, with custody/checkpoint/provenance proof and true catalog rejection reason for each five-key; merge only deterministic source code guards after tests.
2. Separate design/owner for PPI request-response-binding authority, sibling negative cases, full five-key provenance, Book timing and meaningful late/empty response tests. Do not counterfeit settlement authority.
3. Controlled admission simulation per family with real already captured evidence; PAPER/SHADOW zero real orders; deterministic replay with Book1/Book2 signed PIT receipt and risk+strategy acceptance.
4. Integrated candidate only after all guards, then Predeploy V2 and single Deploy V2 when explicit GO criteria are met. CI GREEN alone does not validate droplet runtime.

**Error → RCA → Fix → Guard → Test / CI / Preflight → Evidence**: test fixture regression has local proof; instrument causal attribution and live admission have evidence gaps and are **not closed**.
