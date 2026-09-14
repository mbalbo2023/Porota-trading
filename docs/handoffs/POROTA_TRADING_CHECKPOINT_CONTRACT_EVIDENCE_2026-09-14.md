# POROTA TRADING — CHECKPOINT CONTRACT EVIDENCE — 2026-09-14

## Canonical scope for this investigation

This checkpoint covers the PPI authenticated contractual-evidence work performed on 2026-09-14 and the mandatory transition toward a multi-source PPI + IOL architecture.

Canonical working branch:

`ops/rc6-contract-open-session-immediate-20260914`

Repository:

`mbalbo2023/Porota-trading`

## Safety invariants — MUST NOT be relaxed

- Trading mode observed during diagnostics: `PRODUCTION_PAPER`.
- Real broker orders sent: `0`.
- Contract evidence work is read-only with respect to trading and canonical DBs.
- No DB import is permitted until the contractual evidence is structurally validated.
- No service restart is needed for the diagnostic/capture steps.
- Browser/network probes allow `GET`, `HEAD`, `OPTIONS` only after authentication/session establishment.
- Order/trade/confirm/cancel-like mutations are forbidden.
- Credentials, cookies, tokens, authorization headers, query strings and account data must never be printed or persisted in diagnostic logs.
- The global browser lock `/run/lock/porota-ppi-web-browser.lock` is mandatory.
- Do not run while `porota-ppi-web-residual-rc6.service` is active.
- No family may be declared `READY_PPI` solely from external analytics. Broker contract evidence remains broker-specific.

## Authentication diagnosis completed

### Run 34863931835

Initial one-shot diagnostic was able to inspect the trusted profile, but a logic bug classified `/logOut` as an authenticated trading route. This was identified and fixed immediately. No real orders, DB import, service restart or credential exposure occurred.

### Run 34864075852

Corrected one-shot login diagnostic completed.

Observed facts:

- Local re-auth secret exists and was not printed.
- Exactly one login submission was permitted.
- `api.portfoliopersonal.com/api/Seguridad/Auth/Login` returned HTTP 200.
- `trading.portfoliopersonal.com/api/logInSSO` returned HTTP 200.
- Browser landed at `https://cuenta.portfoliopersonal.com/cuentas`.
- No 2FA/OTP challenge was detected.
- No generic credential error was detected.
- A Google analytics POST was blocked; this is not a PPI trading operation.
- No order path was visited.
- `REAL_ORDERS_SENT=0`.

Conclusion: authentication credentials/session establishment are not the contractual-evidence blocker.

## Authenticated contractual passive capture completed

### Run 34864255027

Authenticated trusted-device capture against:

- `/Cotizaciones/Bonos`
- `/Operar/Bonos`
- `/Operar/Ons`
- `/Cotizaciones/Cauciones`
- `/Operar/Cauciones`

Result:

- `AUTH_STATUS=AUTHENTICATED_TRUSTED_DEVICE`
- `ROUTES_REACHED=5/5`
- collector exit code `0`
- capture file generated on droplet
- `REAL_ORDERS_SENT=0`
- `DB_IMPORT_EXECUTED=NO`
- `SERVICE_RESTARTED=NO`

But contractual payload evidence was initially insufficient:

- `ENDPOINT_KIND_COUNTS={"SCHEMA_ONLY":1}`
- `AL30_EXPLICIT=NO`
- `CAUCIONES_OPERABLES_ROWS=0`
- Missing endpoint kinds: `InstrumentosOperables`, `CaucionesOperables`, `DatosTecnicos`
- `STRUCTURALLY_IMPORTABLE_CANDIDATE=NO`

Conclusion: the old passive collector was not observing the requests correctly enough to collect current evidence.

## Network inventory diagnostic — COMPLETED

Added:

`ops/rc6_ppi_contract_network_inventory_20260914.py`

Commit:

`202879bc16e3083d4ad53568e4e6c41e9db81f16`

Workflow execution commit:

`02db32a53e7142452b6e57db641e3dc857bd29bd`

Run:

`34865060794`

Final result:

- Workflow/job: SUCCESS.
- `AUTH_STATUS=AUTHENTICATED_TRUSTED_DEVICE`.
- `ROUTES_REACHED=6/6`.
- `FIRST_PARTY_GET_RECORDS=154`.
- `JSON_GET_RECORDS=28`.
- `BLOCKED_NONREAD_COUNT=13` — blocked by design.
- `QUERY_STRINGS_LOGGED=FALSE`.
- `HEADERS_LOGGED=FALSE`.
- `RESPONSE_BODIES_LOGGED=FALSE`.
- `DB_IMPORT_EXECUTED=FALSE`.
- `SERVICE_RESTARTED=FALSE`.
- `REAL_ORDERS_SENT=0`.

Crucial discovery: the current PPI frontend DID emit the contractual endpoints during the authenticated run. Relevant first-party GET host/path values observed:

- `api.portfoliopersonal.com/api/Ordenes/InstrumentosOperables`
- `api.portfoliopersonal.com/api/Ordenes/CaucionesOperables`
- `api.portfoliopersonal.com/api/Ordenes/ConfiguracionOperatoriaSimplificada`

`CaucionesOperables` was specifically observed from `/Operar/Cauciones` with provider fields including `cantidadDecimales` and `cantidadDecimalesPrecio`.

Updated RCA:

- Authentication works.
- The PPI frontend still uses `InstrumentosOperables`, `CaucionesOperables`, and `ConfiguracionOperatoriaSimplificada`.
- The previous passive collector failed because its observation/capture behavior was insufficient, NOT because those endpoints no longer exist.
- We now have current endpoint-path proof from the live authenticated frontend.

## Targeted contractual response capture — COMPLETED

Script:

`ops/rc6_ppi_contract_targeted_capture_20260914.py`

Workflow commit:

`173572b27bad423b6346f16c1289e4ad3ade9f98`

Run:

`34865452877`

Final result:

- Workflow/job: SUCCESS.
- `AUTH_STATUS=AUTHENTICATED_TRUSTED_DEVICE`.
- `ROUTES_REACHED=3/3`.
- Captured kinds:
  - `InstrumentosOperables`
  - `CaucionesOperables`
  - `ConfiguracionOperatoriaSimplificada`
- `AL30_EXPLICIT=YES`.
- `AL30_ROWS=1`.
- `CAUCIONES_ROWS=120`.
- `DB_IMPORT_EXECUTED=FALSE`.
- `SERVICE_RESTARTED=FALSE`.
- `REAL_ORDERS_SENT=0`.

AL30 normalized provider evidence included:

- `instrument_id`
- `ticker`
- `currency`
- `provider_name`
- commission fields
- market fee rate
- auction flag
- quantity decimal places
- price decimal places
- semantic guard
- `quantity_step=None`
- `price_tick=None`

**Important:** PPI decimal places are not interpreted as step or tick.

Cauciones evidence included 120 rows with fields including:

- `dias`
- `descripcion`
- `pildoraDescripcion`
- `cantidadDecimales`
- `cantidadDecimalesPrecio`

Conclusion: PPI contractual investigation is NOT a failure. The provider is returning current explicit contract-related data, but some semantic fields remain unresolved.

## AL30 technical-data probe — COMPLETED, partial negative result

Script:

`ops/rc6_ppi_al30_technical_probe_20260914.py`

Run:

`34865706726`

Result:

- Workflow/job: SUCCESS.
- authenticated session remained valid;
- `/Operar/Bonos` reached successfully;
- nine visible input candidates detected;
- no safe unique instrument selector was selected automatically;
- `AL30_TYPED=FALSE`;
- `AL30_OPTION_CLICKED=FALSE`;
- `QUANTITY_FILLED=FALSE`;
- `PRICE_FILLED=FALSE`;
- `ORDER_CONTROL_CLICKED=FALSE`;
- no `DatosTecnicos` response was triggered;
- only `ConfiguracionOperatoriaSimplificada` was observed among technical-shape responses;
- `REAL_ORDERS_SENT=0`.

This was a deliberate fail-safe outcome, not a failed safety control.

## AL30 input metadata inspection — COMPLETED

Run:

`34865900760`

Final state: SUCCESS.

Sanitized input metadata showed two React Select combobox candidates:

- `react-select-2-input`
- `react-select-158-input`

and separate fields for period, amount type, amount, price type, price, expiry period type and expiry period.

The probe correctly refused to guess which React Select represented the instrument selector.

No mutation was executed.

## Strategic decision — MULTI-SOURCE architecture is now mandatory pending work

Decision recorded on 2026-09-14:

**Do NOT abandon PPI. Do NOT permanently reduce Porota to Acciones + CEDEAR. Do NOT start mass IOL scraping.**

Porota must evolve toward:

> **multi-source market data + multi-source analytics + broker-specific execution contract + fail-closed execution gate**

Roles:

### PPI

PPI remains the authority for `READY_PPI` execution semantics:

- exact operable identity;
- availability;
- market/currency;
- settlement;
- broker restrictions;
- minimums/steps/ticks when required and explicitly supplied;
- execution configuration.

### IOL API

IOL is approved for investigation as a second structured provider for:

- instrument metadata;
- quotes/order book;
- daily history;
- intraday history;
- fixed-income analytics;
- caucion rates;
- options chain/Greeks;
- corporate events;
- cross-provider validation.

IOL analytics may support a decision for an instrument that will ultimately execute through PPI, but **IOL cannot by itself make that instrument `READY_PPI`.**

### IOL Web scraping

Deferred and blocked by design until the API coverage matrix proves a critical missing field exists only on the web.

## IOL investigation started — actual read-only findings

Read-only market-data tests performed on 2026-09-14:

### AL30

IOL asset metadata returned:

- family/type `TIT. PUBLICOS`;
- ARS currency;
- `units_per_lot=100`;
- settlement `T1`;
- related symbols `AL30`, `AL30D`, `AL30C`.

IOL market quote returned:

- last/open/previous close/high/low;
- variation;
- nominal and cash volume;
- multi-level bid/ask order book.

IOL fixed-income analytics returned:

- clean/dirty price;
- accrued interest;
- technical value;
- parity;
- TIR/TEM;
- Macaulay and modified duration;
- issue/maturity and settlement dates;
- cashflow schedule with interest/amortization/residual balance.

Conclusion: IOL can provide a substantial professional analytics layer for fixed income while PPI remains the execution-contract authority.

### GGAL / AAPL

IOL metadata correctly differentiated:

- `GGAL` as `ACCIONES`, T1, lot 1;
- `AAPL` BCBA as `CEDEARS`, T1, lot 1, with `AAPLD`/`AAPLC` related symbols.

### Cauciones

IOL read-only rates provided current term, rate, due date and minimum amount for 1/2/3-day cauciones.

### Opciones

IOL options-chain query for GGAL returned active expirations, strikes, call/put, bid/ask, IV, theoretical price and Greeks where market data was available.

This is research evidence only. Options remain outside immediate READY scope pending dedicated risk and broker-contract work.

## Official IOL API documentation findings

Official documentation reviewed on 2026-09-14 states:

- HTTPS + JSON API;
- bearer token + refresh token authentication;
- documented bearer lifetime of 15 minutes;
- `/token` used for initial authentication and refresh;
- IOL provides a sandbox environment separate from the real account/environment;
- production API actions can affect the REAL environment;
- public IOL information currently states API usage is bonified up to 25,000 calls/month, subject to current terms/tariffs.

Implication for Porota:

- initial integration must be read-only and default-deny;
- mutation methods must not be part of the first IOL adapter;
- call budget/cache/rate monitoring is required before mass ingestion.

## Mandatory linked documents

### Architecture research

`docs/research/POROTA_MULTI_SOURCE_MARKET_DATA_AND_CONTRACT_ARCHITECTURE_2026-09-14.md`

Commit creating it:

`cd9a2d80f1f983d8b592c5cf31c3edf774d9300f`

### Mandatory pending backlog

`docs/handoffs/POROTA_TRADING_PENDING_MULTI_SOURCE_PPI_IOL_2026-09-14.md`

Commit creating it:

`dbb16db4dc5d74a72d667da14ba3fa4dea8555b8`

The pending backlog is mandatory and items may only be resolved as `DONE`, `DEFERRED`, or `WONT_DO` with explicit evidence/reason. They must not silently disappear from future checkpoints.

## Current priority order

1. Keep Acciones + CEDEAR as the mature PAPER core; do not interpret this as permanent scope reduction.
2. Finish AL30 as the representative fixed-income case.
3. Complete Cauciones contract mapping.
4. Build PPI-vs-IOL coverage matrix by family/field.
5. Design `IOLReadOnlyProvider` with no mutation methods.
6. Validate IOL histories on representative Acción, CEDEAR, Bono, Letra and ON.
7. Add source provenance/freshness/divergence to canonical data model.
8. Only then decide whether IOL web scraping is necessary for any residual field.

## Required evidence before READY/import

Do NOT mark instruments READY based on assumptions or metadata inference.

For the currently blocked contractual layer, explicit provider evidence is still required. Priority proof targets are:

1. AL30 as a representative bond with explicit provider contract/operability fields.
2. Cauciones with explicit operable rows/terms.
3. Discovery/operability endpoint sufficient to map provider item/ticker identity and settlement context.
4. Technical endpoint(s) or equivalent current frontend API exposing maturity/coupon/amortization and other bond technical terms needed by the canonical contract model, OR documented substitution of those analytics from IOL while maintaining PPI execution-contract evidence separately.

Decimal precision must NOT be interpreted as quantity step or price tick unless a provider explicitly supplies that semantic.

## Stopper status

PPI contract evidence remains a blocker for declaring affected non-core instruments fully `READY_PPI`, but PPI itself is **not considered failed**.

The architectural response is now multi-source:

- PPI for execution contract;
- IOL for additional structured market data/analytics and cross-check;
- Porota for canonical normalization, provenance, reconciliation, decision and risk.

This blocker does not mean the PAPER observer is sending real orders; real orders remain zero. Evaluations/simulations must not be interpreted as contractual readiness for families whose provider evidence is incomplete.
