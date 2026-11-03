# CHECKPOINT — WS-DISK-07B PRETRANSFER GUARD FIX-FORWARD — 2026-10-01

## Entry
- Product SHA: c78497bba49e1ea20e242df1936c37bbb29fe901
- Runtime remained on previous validated image because Deploy V2 run 36888773485 failed before Transfer.
- Failed run:
  - exact candidate resolved;
  - frozen artifact verified;
  - Pre-transfer Droplet disk guard executed;
  - Transfer frozen artifact: SKIPPED;
  - Promote: SKIPPED;
  - runner cleanup: GREEN.

## RCA
The first guard implementation embedded awk positional variables ($4/$2) inside a runner double-quoted SSH command.
With set -u, the runner attempted to expand $4 locally and aborted with:
- line 19: $4: unbound variable.

This was a guard implementation error, not a disk-capacity failure.

## Fix
- remote disk metrics now use remote Python stdlib:
  - shutil.disk_usage("/")
  - os.statvfs("/")
- no awk positional parameters;
- no heredoc/YAML indentation ambiguity;
- regression forbids the old awk pattern.

## Read-only runtime proof
Temporary read-only smoke run: 36889109236 — GREEN.
- available_bytes=8,094,048,256
- required_bytes=7,424,709,120
- inode_free_percent=90.569592
- min_inode_free_percent=10
- RC6_DISK_PRETRANSFER_PREFLIGHT=GREEN

Temporary smoke workflow was removed before candidate PR.

## Safety
- PAPER/SHADOW ONLY.
- real_orders_sent=0.
- no transfer/promote occurred in the failed Deploy V2.
- PPI Watch untouched.
- FIX-FORWARD ONLY.
