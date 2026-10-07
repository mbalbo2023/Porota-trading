# RC6 runtime evidence channel

## Contract

`ops_runtime_evidence_rc6.py` is an independent producer. It does not import
the introspector, the observer, broker clients, order routers or DB writers.
Its only database connection is SQLite URI `mode=ro`; it sets and verifies
`PRAGMA query_only=ON` before reading an explicit table/column allowlist.

The producer writes one sanitized bundle to staging. The publisher copies that
bundle to the `runtime-observability` branch at:

- `runtime/evidence/latest.json`
- `runtime/evidence/daily/YYYY-MM-DD.json`

The publisher has no Docker command, no retention, no service restart and no
runtime mutation. Its Git write is limited to those two evidence paths. It
shares one host lock with the existing introspection publisher so both writers
cannot mutate the same observability clone concurrently.

The publisher service uses `ProtectHome=read-only`: Git may read the
`porotaadmin` credential configuration, but neither Git nor the publisher can
write anywhere under the home tree. `ProtectSystem=strict`,
`NoNewPrivileges=true` and the three explicit runtime/state write paths remain
in force.

## Fail-closed states

- `COMPLETE`: required schema and safety contract are evidenced.
- `INCOMPLETE`: the bundle is valid, but a schema/ledger/provenance fact is
  missing or inconsistent.
- `BLOCKED`: mode is not PAPER/SIMULATED, `real_orders_sent != 0`, the real
  route is not blocked, or the route-call state is not evidenced as
  `NOT_CALLED`/`BLOCKED`.

PPI Watch remains a separate owner and is never mutated. Its evidence contract
is versioned in `ops/policy/host-control-plane-reconciliation-v2.json`. The
repository currently has no authoritative PPI Watch systemd unit name and does
not assert that such a unit must be absent. Consequently:

- one exact unit can be `VERIFIED_READ_ONLY` only when a future versioned
  contract names it and read-only checks prove it enabled, active and hashed;
- zero matching units is `NOT_PRESENT`, not GREEN;
- multiple matches are `AMBIGUOUS`;
- enumeration, permissions or metadata failures are `NO_VERIFICADO`;
- absence can be `VERIFIED_ABSENT` only if a versioned contract explicitly
  changes the presence expectation to `ABSENT`.

An instrument can be `READY_PAPER` only with a PPI-primary identity, fresh PPI
evidence, an AVAILABLE catalog row, an explicit `READY_PAPER_*` capability,
no ambiguity and exactly one coherent candidate-ledger row. Unknown freshness
never becomes READY. IOL/BYMA/A3/ROFEX are complementary only and cannot
replace the PPI identity.

`candidate_universe` remains a legacy ticker/family/market summary so existing
read-only consumers keep their schema. Reconciliation rebuilds it inside the
caller's transaction: identities removed or reclassified in the catalog cannot
survive as ghosts. `can_simulate=1` is allowed only when the summary maps to one
complete, fresh PPI-primary identity with no retry ambiguity. Catalog status and
capability remain visible for historical consumers even when this gate is zero.

The bundle reports `catalog_rows` and `candidate_rows` separately. The legacy
`ledger_rows` field is retained as a compatibility alias for `candidate_rows`;
it no longer counts catalog projections. If a future ghost is detected, its
sanitized settlement, gate, status, detail and check timestamp are included for
RCA without exposing provider payloads.

## Pinned consumption

Never read the moving branch name as evidence. Resolve the
`runtime-observability` branch once, retain that 40-character commit SHA, read
`runtime/evidence/latest.json` at that commit and retain the returned Git blob
SHA. Consumers must verify both pins.

For a local read-only clone, run:

```bash
python scripts/porota_consume_runtime_evidence_rc6.py \
  --repo /path/to/read-only/clone \
  --commit-sha <fixed-40-character-commit> \
  --blob-sha <fixed-40-character-blob>
```

The consumer uses `git show <commit>:runtime/evidence/latest.json`; it never
checks out a branch and never triggers the producer.

## Operational boundary

Installing/enabling the dedicated service and timer requires the normal
reconciled Deploy V2 process. Merely merging this code does not prove that a
current runtime bundle exists. Runtime/DB/systemd/Docker and PPI Watch must be
checked again after the eventual consolidated deployment.
