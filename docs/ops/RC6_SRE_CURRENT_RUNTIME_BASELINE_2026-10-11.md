# RC6 SRE current runtime baseline — 2026-10-11

Workstream `WS-RC6-SRE-REPAIR-DISK-DEEP-20261011`. Evidence from original GitHub Actions artifacts, not the runner filesystem. Production Source base `da697c6e6c2274579f9e4a112fabc4327475dd35`; isolated DRAFT PR #494. No host mutation or candidate deployment in this baseline.

## Custody

| Run / attempt | Artifact | ZIP SHA256 | Capture UTC | Meaning |
|---|---:|---|---|---|
| [38161192855 / 1](https://github.com/mbalbo2023/Porota-trading/actions/runs/38161192855) | 11710905733 | `044b4e4b0c919dbfc11a00064fe702778c48075421509aecfc1808ad98e3fa20` | 17:48:49–17:49:58 | Initial host inventory; Docker stderr diagnostic defect disclosed |
| [38161419212 / 1](https://github.com/mbalbo2023/Porota-trading/actions/runs/38161419212) | 11710133061 | `6f311b63dc1596b5c3f3cb0f3e70e12122be6267d0e613332e2c1938f43d4d48` | 17:52:06–17:55:25 | Corrected Docker log capture and disk attribution |
| [38161901175 / 1](https://github.com/mbalbo2023/Porota-trading/actions/runs/38161901175) | 11710582018 | `63a22041cb8a353dd106df76126ed9594cb72a7587bd4032bd78149648d6006f` | 17:59:19–18:01:02 | Focused logs, units, physical log inventory, bounded DB check |

Run 38161807486 failed at workflow validation before contacting the host: duplicated malformed YAML tail. Commit 4bd0b222 repaired it and local PyYAML parsing and both embedded Python compilation passed. Every published raw JSON is redacted; sensitive logs are represented by hashes and categories.

## Runtime state at 18:01 UTC

- Root filesystem: 24,883,167,232 bytes, available 9,419,915,264; free inodes 2,856,144 of 3,145,728. No low disk/inode threshold currently.
- `porota_production_observer`: running, accumulated restart count 46; no restart delta across these snapshots. The SQLite `observer_state` row is `PRODUCTION_PAPER`, `real_orders_sent=0`, WAL. DB physical blocks 1,486,831,616 bytes; WAL 14,368,768. No DB mutation.
- `porota_production_dashboard`: Docker healthy, restart count 0. This does not independently establish authenticated user flow.
- `porota_critical_approval_rc6`: running, Docker unhealthy, restarts 0, memory cap 192 MiB, PID cap 64, cgroup current 60; pids.events max rose 1,048,224 → 1,050,304. Recent health probes return ExitCode -1, classified `FORK_RESOURCE`, OOMKilled false. The host unit is active, which does not establish functionality. Source launcher `ops/rc6_deploy_critical_control_plane.sh` sets `--pids-limit 64` and does not override the dashboard image's HTTP healthcheck. Exact live command and nested task ancestry are pending a separate probe.
- Observer logs over 24 hours have SQLite lock lines every sampled hour, with 44 exception frames ending at `cf_intraday_scalping._heartbeat`, six Telegram status, four candle status, three candle tick, three exit heartbeat, three caucion persist, two settle. This attributes victims, not the long writer. Incident count cannot be inferred from matching lines. Continued lock errors mean `NO_VERIFICADO`.
- A read-only `PRAGMA quick_check(1)` on this 1.49 GB active DB exceeded the bounded 85 second window and returned 124. Integrity is `NO_VERIFICADO`; it was not repeated under the same condition. State read succeeded.
- Host backup service succeeded 11/oct 07:18–07:26; timer remains configured. No external DigitalOcean snapshot/backups were established by prior evidence, so recovery remains protected.
- Failed systemd records: preopen 9/oct exit 2 with RED journal categories (not `NOT_DUE`); GDELT not-found after 2/oct exit 2; scheduler-export 12/sep signal 15; weekend backfill 12/sep exit 4. These are historical unit results requiring owner/consumer analysis, not blanket current outage claims.
- PPI Watch was untouched; the diagnostic unit-name filter did not find a matching service. No configuration or runtime write occurred.

## RCA and validation ledger

| Component | Error and causal evidence | Status / next discriminant |
|---|---|---|
| Critical approval health | Health process cannot fork at cap 64; source launch inherits dashboard HTTP healthcheck although gateway uses a Unix Issues broker | `RCA_PARTIAL`: process ancestry, exact live health test, broker functionality and safe bound pending; no restart |
| Observer SQLite | Active recurring lock exceptions in multiple PAPER children, dominated by scalping heartbeat; 20 s busy timeout already present | `NO_VERIFICADO`: identify lock holder/transaction before code change or deploy |
| Preopen | 9/oct exit 2 and RED journal, not merely closed-market `NOT_DUE` | `NO_VERIFICADO`: exact red fields and next due window |
| Other failed units | Old dates and tombstone source for GDELT | `NO_VERIFICADO`: consumers and timer relevance |
| Disk | Free 9.42 GB; object inventory below | `EN_GITHUB`: no deletion without sealed plan and global DEPLOY_OWNER |
| PAPER / PPI | DB `PRODUCTION_PAPER/0`; PPI Watch untouched | Observed baseline, recheck after any authorized intervention |

No `GLOBAL_GREEN` or functional closure is claimed.
