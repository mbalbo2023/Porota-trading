# RC6 deep disk forensics and cleanup — 2026-10-11

Workstream `WS-RC6-SRE-REPAIR-DISK-DEEP-20261011`, PR DRAFT #494. This is the measured **audit/plan** boundary. No deletion is claimed. Baseline and original JSON artifacts: [run 38161419212](https://github.com/mbalbo2023/Porota-trading/actions/runs/38161419212) artifact 11710133061 ZIP SHA256 `6f311b63dc1596b5c3f3cb0f3e70e12122be6267d0e613332e2c1938f43d4d48`; [run 38161901175](https://github.com/mbalbo2023/Porota-trading/actions/runs/38161901175) artifact 11710582018 ZIP SHA256 `63a22041cb8a353dd106df76126ed9594cb72a7587bd4032bd78149648d6006f`; [run 38162173708](https://github.com/mbalbo2023/Porota-trading/actions/runs/38162173708) artifact 11710837212 ZIP SHA256 `2de53e0b4caead49f675602c60086a2aa8b972e6aa0d15ebcfe0418f5fe0068e`.

## Physical map

At 18:01 UTC, root filesystem total 24,883,167,232 bytes, available 9,419,915,264 bytes; free inodes 2,856,144/3,145,728. At 18:05 UTC, available 9,419,874,304. Changes within the normal runtime drift are not cleanup savings. `/var/lib/docker` is the configured DockerRootDir; physical `du` approximately 3,749,466,112 bytes. Earlier ~9.8 MB readings were not a valid Docker occupancy claim. One image ID `ec7fe885...` is shared by all three active containers (2.46 GB reported image size); BuildKit cache ~1.402 GB is reported active/non-reclaimable. No orphaned image or volume has been proved eligible. Docker's virtual/shared sizes must not be added to `du`.

| Root | Physical bytes (du -x, approximate) | Classification |
|---|---:|---|
| `/var` | 6,029,787,136 | Mixed; `/var/lib` 5,158,514,688, `/var/log` 821,870,592 |
| `/opt` | 3,741,220,864 | Mixed; `/opt/porota-trading` 3,094,474,752 protected until inode attribution |
| `/usr` | 3,135,778,816 | System/runtime protected |
| `/home` | 346,963,968 | Ambiguous, no path/reference proof |
| `/root`, `/tmp` | small | Ambiguous, no safe-delete set |

The PAPER DB alone occupies 1,486,831,616 physical bytes, WAL 14,368,768, SHM 32,768 at 18:01. All three are `PROTECTED/ACTIVE`. No `VACUUM`, checkpoint, WAL deletion, schema mutation or forced recovery: no independently verified external backup. The daily host backup service succeeded 11/oct and its timer remains configured; future growth is a separate policy risk.

`/var/log` occupies 821,927,936 bytes at 18:01, including journal 632.7 MiB. Largest examples by physical blocks: active `system.journal` 67,112,960; archived system/user journal segments 61,464,576, 64,442,368, 57,487,360, 56,020,992 and others; `syslog.1` 43,499,520 (10/oct incident window), `btmp.1` 29,716,480 (security audit), `kern.log.1` 8,425,472. The inventory script's simple extension classifier marked `*.journal` as nonrotated; journald `system@...journal` segments are archived, but their deletion eligibility still needs an explicit retention/evidence decision. `/proc/*/fd` found zero open unlinked regular files. There is no hidden `df` recovery from restarting processes on that evidence.

## Object classes and decisions

| Object/class | Bytes expected | Class | Reason/next guard |
|---|---:|---|---|
| Active Docker image and three containers | Not additive; Docker image 2.46 GB shared | `ACTIVE/PROTECTED` | Same image ID in all three; never prune |
| Docker BuildKit cache | ~1.402 GB reported | `AMBIGUOUS` | Docker reports active/non-reclaimable; no reference or safe rebuild proof |
| SQLite DB/WAL/SHM and `/opt/porota-trading/data` | DB+WAL ~1.50 GB alone | `ACTIVE/PROTECTED` | Unique PAPER state/history, no independent recovery |
| Journal archived segments | ~566 MB subset of 632.7 MiB, not exact deletion sum | `AMBIGUOUS` | Some contain RC6 incident history and may be unique; do not vacuum blindly |
| Rotated syslog/kern/auth/btmp | Measured per inode in run 38161901175 | `AMBIGUOUS` | 9/oct and later incident/audit interval and security provenance; archive/retention proof required |
| Active logs | Measured per inode | `ACTIVE` | Do not truncate |
| `/opt/google` 454,725,632 and contract evidence venv 159,760,384 | Physical du | `AMBIGUOUS` | Dependency and rebuild cost unverified |
| `/opt/porota-builds` 16,498,688, staging 421,888 | Physical du | `AMBIGUOUS` | Need manifest/SHA, owner, FD and current deploy references |
| Previously identified 26 general backups | 5,585,100,800 actually freed in PR #490 | `ALREADY_DELETED` | Do not count again; historical result |
| Current general backups | Host backup directory ~4,096 at scan | `PROTECTED` | No new safe backup deletion set |
| Open unlinked regular files | 0 | `OPEN_FD` none | No restart for disk recovery |
| Exact new `SAFE_DELETE` objects | 0 verified | `SAFE_DELETE` none | No apply manifest can be sealed yet |

A further object-level inventory must expose exact inode/dev/physical blocks, owner, mtime, hardlink/symlink/mount/FD and references before any finite deletion plan. The rollback-free apply path must be separate from audit/plan, require a live paired global `DEPLOY_OWNER`, the `rc6-unified-paper-deploy` mutex, original plan JSON digest and pre/post PAPER/0, critical/sibling identity, PPI and statvfs checks. Current expected and recovered cleanup bytes are **zero**. This is a safety boundary, not a claim that the disk cannot be cleaned further.

## Further causal work

The backup timer could regenerate unbounded copies. Source `scripts/v17_host_general_backup.py` has no retention step despite policy keep-newest=3 in `ops/policy/porota-policy.yaml`. An automatic purge cannot be accepted while independent recovery remains unverified. Inventory next backup output and recovery dependencies before a retention change.

The active observer SQLite lock problem continues in the same host interval; log/journal deletion would lose RCA evidence. Preserve incident window until a source fix is certified and observed in runtime.
