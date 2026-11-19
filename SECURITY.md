# Security Policy

## Supported version

Only POROTA Trading RC6 in PAPER/SHADOW mode is supported. The required operating contract is `PRODUCTION_PAPER / SIMULATION`; real-order capability remains blocked. This policy is not evidence of the current runtime.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue. Use GitHub's private vulnerability-reporting or security-advisory channel for this repository. Include a minimal reproduction, affected commit or workflow, and impact. Do not include credentials, private endpoints, customer data, or production exports.

The maintainer will acknowledge a report, assess impact, and coordinate a fix-forward remediation. No rollback or change to PAPER/SHADOW safety controls is implied by this policy.

## Evidence handling during RC6 PPI recovery

Publish only sanitized HTTP status, request category, timestamps, identity
hashes and PAPER safety receipts. Do not publish PPI credentials, bearer tokens,
quote payloads, private URLs or raw runtime databases in issues, PRs or Actions
artifacts. A successful PPI login is not proof of Current/Book success. A market-closed
technical release still requires verifiable container/image/controller identity,
PAPER/zero-real-order safety and health; never waive those checks to conceal
missing broker evidence. Time-bound the next-session instrument freshness QA.
Before changing safety code, inspect and recover existing partial fixes and PRs;
retain evidence and ownership for every open PR. Refer to
[#1093 incident evidence](https://github.com/mbalbo2023/Porota-trading/issues/1093)
and [#980 ownership](https://github.com/mbalbo2023/Porota-trading/issues/980).

## Verified P0 release boundary — 18-Nov-2026

The post-incident Deploy V2 run
[#44442769638](https://github.com/mbalbo2023/Porota-trading/actions/runs/44442769638)
produced physical runtime artifact #14492700787. Its sanitized receipt states
`VALIDADO_RUNTIME`, a pinned source SHA/tree/image, `PRODUCTION_PAPER`,
`SIMULATION`, a running observer and `real_orders_sent=0` with real routes
`BLOCKED`. It also reports `ppi_watch_touched=false`. This attests only the
deployment's measured scope; it does not prove future order state, quote
freshness or historical database completeness.

Do not purge CI fixtures, historical incident logs or signed receipts to make
an error disappear. Tests remained in isolated offhost workspaces; **no test
quotes or synthetic history were written into the live PAPER database** by
this release. If an actual contaminating row is found, establish its exact
identity, backup/WAL custody, RCA and removal authorization in GitHub Actions
before touching it. PPI Current/Book results and financial payloads must stay
private; publish only bounded sanitised outcome per family/instrument.
