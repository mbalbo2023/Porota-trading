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

## Authenticated contractual capture completed

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

Conclusion: simply visiting the known pages no longer causes the frontend to emit the legacy contractual GETs expected by the passive collector.

## Current RCA

The remaining blocker is NOT authentication.

The current passive collector waits for frontend requests whose endpoint names are known from earlier evidence. During the authenticated 2026-09-14 session those requests were not emitted just by navigating the pages.

Therefore the next task is to discover the first-party GET endpoints currently used by the PPI frontend and then reproduce the required read-only calls explicitly while preserving the authenticated browser context.

## Network inventory diagnostic

Added:

`ops/rc6_ppi_contract_network_inventory_20260914.py`

Commit:

`202879bc16e3083d4ad53568e4e6c41e9db81f16`

Purpose:

- visit account/Bonos/ON/Cauciones pages under the trusted authenticated profile;
- record only first-party GET host/path, status and sanitized JSON schema shape;
- never persist query strings, headers, cookies, bodies, credentials or account data;
- abort every non-read request;
- no DB or service mutation capability.

Workflow updated to execute that inventory safely:

`.github/workflows/rc6-contract-open-session-immediate-20260914.yml`

Workflow commit:

`02db32a53e7142452b6e57db641e3dc857bd29bd`

The workflow now has push path filters so documentation/checkpoint commits do not retrigger the probe.

Current run at checkpoint creation:

- Run ID: `34865060794`
- Job: `contractual-network-inventory-readonly`
- State at last observation: `in_progress`
- Checkout/SSH/staging completed successfully.
- Step currently running: `Run GET-only network inventory`.

## Required evidence before READY/import

Do NOT mark instruments READY based on assumptions or metadata inference.

For the currently blocked contractual layer, explicit provider evidence is still required. Priority proof targets are:

1. AL30 as a representative bond with explicit provider contract/technical fields.
2. Cauciones with explicit operable rows/terms.
3. Discovery/operability endpoint sufficient to map provider item/ticker identity and settlement context.
4. Technical endpoint(s) or equivalent current frontend API exposing the contractual fields needed by the canonical contract model.

Decimal precision must NOT be interpreted as quantity step or price tick unless PPI explicitly supplies that semantic.

## Planned continuation

1. Finish run `34865060794`.
2. Read the sanitized GET inventory and identify first-party JSON host/path candidates used on Bonos/ON/Cauciones pages.
3. Build a minimal authenticated GET-only direct probe for the candidate endpoints, initially AL30 + Cauciones.
4. Capture explicit provider fields and schema only; no canonical import.
5. Validate structural mapping and semantic guards.
6. Only if evidence is sufficient, perform a separate guarded import/recompute proposal. Import must remain a separate action and must not be silently bundled with discovery.

## Stopper status

Contract evidence is still a blocker for declaring the affected instruments fully `READY`.

This blocker does not mean the PAPER observer is sending real orders; real orders remain zero. It means evaluations/simulations must not be interpreted as contractual readiness for families whose required provider evidence is incomplete.
