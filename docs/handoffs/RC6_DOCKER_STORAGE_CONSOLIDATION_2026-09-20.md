# RC6 Docker storage consolidation — handoff

**Cut:** 2026-09-20 12:33 UTC  
**Repository:** `mbalbo2023/Porota-trading`  
**Canonical deploy branch:** `deploy/rc6-pr69-isolated-20260915`  
**Canonical head:** `787fba625c90a8fd53b5dfb3072ea0ef00fb8c9a`  
**Live RC6 image:** `porota-trading-bot:17.0.0-rc6`  
**Live image ID:** `sha256:3d032ddcc2e6767920fb5bf3fd551fe2d8bfd31febdeb98ad7561d7019e9b618`  
**Safety:** `PRODUCTION_PAPER` / `SIMULATED`; `real_orders_sent=0`.

## Outcome

- All live RC6 services (observer, dashboard, critical approval) use the same image ID above.
- Docker has exactly one Porota image reference, `porota-trading-bot:17.0.0-rc6`; no RC6 candidate tag remains.
- Chroma remains separately pinned to `chromadb/chroma:0.5.23`. Its container was `Exited (0)` for four days at the latest capture. Keep its image and persistent index until its operational dependency is established.
- Root filesystem: 24,883,167,232 bytes total; 9,138,925,568 bytes free (64% used) at 2026-09-20 12:32:28 UTC.
- The significant recovery was removal of two stopped/created containers for old image `sha256:6606a339…`, which retained an obsolete snapshot chain. Their mounts were bind mounts only; removal used `docker rm` without `-v`. Persistent data was not removed.
- Free bytes rose from 8,035,471,360 immediately before exact cleanup to 9,139,933,184 after it, a net gain of about 1.03 GiB. Tag removal itself does not duplicate or free image layers.
- A second unused, untagged old RC6 image record (`sha256:1f5678c1…`, metadata commit `4b34b831…`) had no container references and was removed by its exact image ID. Its layers were shared with the current build, so it did not materially change free space.
- Latest post-cleanup Docker accounting: two images (RC6 + Chroma), four containers (three running RC6 + stopped Chroma), 2.942 GB BuildKit cache; 67.69 MB reclaimable in the post-deploy Docker accounting. Build cache, volumes, Chroma data, and trading data were left intact.
- No `docker system prune`, `docker image prune`, `docker builder prune`, volume deletion, data deletion, or real trading calls.

## Durable change and evidence

- PR #120 merged: https://github.com/mbalbo2023/Porota-trading/pull/120
  - Changes the canonical deploy cleanup to remove the just-promoted candidate alias when stable and candidate resolve to the same immutable ID.
  - Keeps a candidate only when stable differs and a container still references that candidate image ID.
  - Only one deploy workflow file changed; PR `preflight` and `tests` succeeded.
- Canonical deploy run #118, ID `35510375770`: https://github.com/mbalbo2023/Porota-trading/actions/runs/35510375770
  - Tests and RC6 invariants passed.
  - `RC6_IMAGE_TAG_REMOVED=…|STABLE_DIGEST_VERIFIED`.
  - `POST_STATE=ok|PRODUCTION_PAPER|0`, `REAL_ORDERS_SENT=0`, `RC6_UNIFIED_PAPER_DEPLOY=GREEN`.
  - Approval service reported issues-only health and passed its PAPER secret guard.
- Exact container/tag consolidation Action: run `35510088986`, succeeded. It removed only the two verified stopped containers and candidate tag; temporary branch self-deleted.
- Read-only postcheck: run `35510211349`, succeeded; confirmed one RC6 tag, all 3 services running on one digest, and `POST_STATE=ok|PRODUCTION_PAPER|0`.
- Dangling image inventory/removal: run `35510853738`, succeeded; exact image `1f5678c1…` removed only after verifying no container reference and older RC6 commit. Postcheck again confirmed PAPER state and one RC6 tag.
- All temporary audit branches self-deleted after capturing evidence.

## State board

- 🟢 **Image/tag consolidation:** done and verified live.
- 🟢 **Stale old container snapshot references:** exact stopped containers removed; persistent bind-mounted data retained.
- 🟢 **Untagged obsolete RC6 image record:** exact unreferenced image ID removed.
- 🟢 **Stable future deploy behavior:** PR #120 merged and canonical run passed.
- 🟡 **Chroma dependency:** determine whether SRE/vector tooling still needs the stopped service and `/opt/porota-trading/sre_vector_db`; inspect dependency wiring before any deletion.
- 🟡 **Remaining Docker space:** preserve BuildKit cache for now. Its large recent/shared records are in use; the latest summary reports only 67.69 MB reclaimable.
- ⚪ **Non-Docker disk owners:** previously inventoried `/home/.../.cache`, journals, apt cache, and Porota data. Reclassify contents from current live evidence before any cleanup.

## Constraints and next action

All Droplet operations go through GitHub Actions. Keep RC6 in PAPER/SIMULATED, do not touch PPI Watch, do not use global prune, do not remove Chroma or persistent data without a dependency proof, and do not add documentation to the canonical deploy branch.

Next: perform a read-only Actions-based dependency trace for `porota_sre_chromadb` and its bind-mounted index. Confirm systemd, Compose, and application consumers, then decide whether only the stopped container is obsolete; retain the persistent index until proven unused. If further disk recovery is requested after that, inspect Docker cache records and other owners before selecting exact targets.
