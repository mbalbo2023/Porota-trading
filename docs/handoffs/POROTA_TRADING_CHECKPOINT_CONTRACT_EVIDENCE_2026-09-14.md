# POROTA TRADING — CHECKPOINT CONTRACT EVIDENCE — 2026-09-14

## Canonical scope for this investigation

This checkpoint covers the PPI authenticated contractual-evidence work performed on 2026-09-14. It is intentionally isolated from the canonical trading/history databases until explicit provider evidence is captured and validated.

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

But contractual payload evidence was insufficient:

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

Other account-related endpoints were deliberately NOT treated as contract evidence.

Updated RCA:

- Authentication works.
- The PPI frontend still uses `InstrumentosOperables`, `CaucionesOperables`, and `ConfiguracionOperatoriaSimplificada`.
- The previous passive collector failed because its observation/capture behavior was insufficient, NOT because those endpoints no longer exist.
- We now have current endpoint-path proof from the live authenticated frontend.

## Targeted contractual response capture — STARTED

Added:

`ops/rc6_ppi_contract_targeted_capture_20260914.py`

Commit:

`8ccded6033676bfb7e3f953d00421eb67f83189f`

Workflow updated to capture only sanitized provider responses from the three proven current endpoints.

Workflow commit:

`173572b27bad423b6346f16c1289e4ad3ade9f98`

Run:

`34865452877`

State at this checkpoint update: queued/starting.

Targeted capture design:

- Trusted authenticated PPI browser profile.
- Routes: `/Operar/Bonos`, `/Operar/Ons`, `/Operar/Cauciones`.
- GET/HEAD/OPTIONS only.
- Capture only the three proven contractual endpoints.
- Raw response bodies are NOT logged.
- InstrumentosOperables is normalized with the existing RC6 normalizer and only explicit AL30 rows are retained for the AL30 proof summary.
- Cauciones rows are sanitized to contractual safe fields only.
- ConfiguracionOperatoriaSimplificada is sanitized/schema captured.
- No canonical import.
- No service restart.
- Real orders remain forbidden/zero.

## Required evidence before READY/import

Do NOT mark instruments READY based on assumptions or metadata inference.

For the currently blocked contractual layer, explicit provider evidence is still required. Priority proof targets are:

1. AL30 as a representative bond with explicit provider contract/operability fields.
2. Cauciones with explicit operable rows/terms.
3. Discovery/operability endpoint sufficient to map provider item/ticker identity and settlement context.
4. Technical endpoint(s) or equivalent current frontend API exposing maturity/coupon/amortization and other bond technical terms needed by the canonical contract model.

Decimal precision must NOT be interpreted as quantity step or price tick unless PPI explicitly supplies that semantic.

## Planned continuation

1. Finish run `34865452877`.
2. Validate AL30 explicit row and Cauciones rows/fieldsets from targeted sanitized capture.
3. If AL30 technical terms are still missing, perform a separate read-only interaction/probe that selects AL30 without filling quantity/price and captures `DatosTecnicos` or the current equivalent GET endpoint.
4. Validate structural/semantic mapping.
5. Only after evidence is sufficient, prepare a separate guarded import/recompute proposal. Discovery and import remain separate operations.

## Stopper status

Contract evidence remains a blocker for declaring the affected instruments fully `READY` until the provider contract fields are captured and validated.

This blocker does not mean the PAPER observer is sending real orders; real orders remain zero. Evaluations/simulations must not be interpreted as contractual readiness for families whose provider evidence is incomplete.
