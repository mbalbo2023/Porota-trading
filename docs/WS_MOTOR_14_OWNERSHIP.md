# WS-MOTOR-14 ownership

- WORKSTREAM_ID: `WS-MOTOR-14-IOL-CACHE-RELAX-PAPER-MASS-PROMOTION-20260929`
- MODE: `WRITE_OWNER`
- BRANCH: `work/ws-motor-14-iol-cache-relax-paper-20260929`
- BASE_SHA: `3e859767cbfcde963ca26944f4847d0e2a3f9d9c`
- PRODUCTIVE_BASE_SHA: `3e859767cbfcde963ca26944f4847d0e2a3f9d9c`
- STARTED_READ_ONLY: `YES`
- DEPLOY_OWNER: `NO`
- STATUS: `ACTIVE`
- ACQUIRED_UTC: `2026-09-29T15:31:00Z`

Scope: IOL MCP/API read-only cache resilience and family-reference population;
explicit broker-term versus PAPER-policy schema; Evidence v2 mass reevaluation;
fixed-income nominal policies, FCI amount lifecycle, long-option contracts,
caucion conservative fill and futures PAPER margin policies; bridge, catalog,
candidate projections, permanent regression coverage, single Predeploy V2,
single Deploy V2 and read-only postdeploy audit.

Reserved paths/components: `iol_mcp_readonly_adapter_rc6.py`,
`iol_shadow_collector_rc6.py`, `scripts/rc6_iol_shadow_collect.py`,
`cr_contract_evidence_v2_mass_hf6.py`, `cp_contract_evidence_v2_hf6.py`,
`rc6_broker_parity_evidence.py`, `rc6_contract_bridge.py`,
`cq_family_contract_rules_hf6.py`, `bu_instrument_catalog.py`,
`bf_production_paper_observer.py`, `rc6_paper_family_lifecycle.py`, deployment
workflows only where required, and focused tests.

Historical draft PRs #357/#358 and PPI Watch are excluded. No runtime, DB,
systemd or Docker mutation occurs until global DEPLOY_OWNER is acquired and the
Actions mutex is revalidated with zero queued/in-progress deploys.

Invariants: PAPER/SHADOW only; `PRODUCTION_PAPER / SIMULATION`;
`real_orders_sent=0`; real routes not called; PPI Watch untouched;
FIX-FORWARD only; no rollback.
