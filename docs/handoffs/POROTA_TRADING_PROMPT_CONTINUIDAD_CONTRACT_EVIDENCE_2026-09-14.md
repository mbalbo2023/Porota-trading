# POROTA TRADING — PROMPT DE CONTINUIDAD — CONTRACT EVIDENCE — 2026-09-14

Use this file together with:

`docs/handoffs/POROTA_TRADING_CHECKPOINT_CONTRACT_EVIDENCE_2026-09-14.md`

Canonical working branch:

`ops/rc6-contract-open-session-immediate-20260914`

## Mandatory context

We are completing explicit contractual evidence for PPI-supported instruments before declaring affected instruments fully READY. The work is not allowed to infer missing contract semantics merely to increase READY counts.

The PPI Web session is proven able to authenticate. The blocker has moved from authentication to endpoint discovery/current frontend behavior.

A corrected one-shot authentication diagnostic proved HTTP 200 login + SSO and account landing without 2FA. A later capture proved `AUTHENTICATED_TRUSTED_DEVICE` and reached all Bonos/ON/Cauciones routes, but the passive collector saw only one `SCHEMA_ONLY` endpoint. It did NOT receive `InstrumentosOperables`, `CaucionesOperables`, or `DatosTecnicos` and did NOT capture AL30 explicitly.

Therefore do not waste time reopening the old hypothesis that username/password are the main blocker unless new evidence explicitly shows auth failure.

## Safety requirements

Always preserve:

- PAPER only; real orders must remain 0.
- Read-only network methods after auth: GET/HEAD/OPTIONS.
- Abort POST/PUT/PATCH/DELETE and order-like navigation.
- Do not expose or persist credentials, cookies, tokens, auth headers, query strings, account/portfolio data or response bodies in logs.
- Use the global PPI browser lock.
- Do not collide with `porota-ppi-web-residual-rc6.service`.
- Do not import to canonical Contract Evidence while discovery is incomplete.
- Do not restart trading services for discovery.

## Exact continuation point

A sanitized GET-only network inventory was added at:

`ops/rc6_ppi_contract_network_inventory_20260914.py`

Commit:

`202879bc16e3083d4ad53568e4e6c41e9db81f16`

The workflow was changed to execute this inventory on the droplet:

`.github/workflows/rc6-contract-open-session-immediate-20260914.yml`

Commit:

`02db32a53e7142452b6e57db641e3dc857bd29bd`

Current workflow run started from that commit:

`34865060794`

At handoff creation, job `contractual-network-inventory-readonly` was still running the GET-only network inventory. Earlier setup, checkout, SSH and staging steps had succeeded.

## What the next chat must do first

Check run `34865060794` and fetch job logs after completion.

Read only the sanitized summary fields:

- authentication state
- routes reached
- first-party GET count
- JSON GET host/path list
- sanitized interesting schema keys
- blocked non-read count
- real orders sent
- DB import/service restart flags

Then classify candidate first-party endpoints by likely purpose: instrument discovery, item detail, settlement/plazos, technical data, cauciones availability/terms.

## Immediate technical objective

Use the network inventory to identify the current first-party GETs the PPI frontend actually emits. Then build a minimal explicit authenticated GET-only probe for:

- AL30 representative bond evidence.
- Cauciones operable/term evidence.

The direct probe should use the existing trusted browser context, as the already working history collector does, but must not log auth headers or query strings and must not issue any non-read method.

The first direct probe is evidence-only. It must NOT import to DB.

## READY criteria for this workstream

Do not declare the affected contract layer READY until current provider evidence has been captured and semantically mapped. In particular, do not convert decimal-place fields into quantity step/price tick. Those remain unknown unless explicitly provided by PPI.

Only after explicit evidence exists may a separate guarded import/recompute be prepared.

## Key previous run IDs

- `34863931835`: first diagnostic; exposed false positive `/logOut` classification bug.
- `34864075852`: corrected one-shot auth diagnostic; login+SSO HTTP 200 and account landing.
- `34864255027`: authenticated contractual passive capture; 5/5 routes but insufficient endpoint evidence.
- `34865060794`: sanitized GET network inventory; in progress at handoff creation.

## User operating preference

Operational work should be executed directly against the droplet through audited GitHub automation when appropriate; do not ask the user to run long manual Termius commands when the connected workflow can safely execute it. Keep every meaningful result and continuation checkpoint in GitHub so a new chat can resume without reconstructing history.
