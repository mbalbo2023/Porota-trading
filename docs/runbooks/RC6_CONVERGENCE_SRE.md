# RC6 convergence SRE contracts

Applies to the convergence of #466 and #470 with #468/#469 residuals. Changes
remain PAPER/SHADOW only, real orders are zero, real routes are NOT_CALLED,
and PPI Watch is outside the deployment scope. This document describes guards
and offline evidence; it does not authorize a merge or a production workflow.

## Approved artifact origin: U11

Predeploy V2 publishes one newly built image for the complete final candidate.
The artifact name contains exact candidate SHA, run ID, and attempt. Promotion
uses the two-parent merge's second parent and requires its tree to equal the
promotion tree. A future approved merge message must carry all five unique
trailers, populated from the reviewed GitHub execution and artifact metadata:

```text
Porota-Predeploy-Workflow-ID: <official numeric workflow ID>
Porota-Predeploy-Run-ID: <approved run ID>
Porota-Predeploy-Run-Attempt: <approved attempt>
Porota-Predeploy-Artifact-ID: <approved artifact ID>
Porota-Predeploy-Artifact-Digest: sha256:<approved outer ZIP digest>
```

`scripts/porota_predeploy_binding.py` checks the official workflow ID and path,
repository and head repository, `pull_request` event, exact head SHA, completed
SUCCESS, approved attempt, PR head/base/repository identity, unique same-run
artifact ID/name/digest, expiration, sizes, and artifact creation within the
approved attempt's server-recorded start/completion window. It checks current API state
again before transfer. A rerun invalidates an earlier approved attempt until
the new tuple is reviewed. Display names and an artifact's internal statements
cannot establish approval.

The outer ZIP's exact size and independent approved SHA256 are verified before
any extraction. Duplicate paths, duplicate required basenames, symlinks,
traversal, encrypted entries, bad CRCs, excessive entries, and excessive sizes
are rejected. Extracted source metadata is rederived from a clean worktree of
the exact candidate. Bundle bytes and modes, raw saved image config ID, layer
digests, and host lifecycle receipts are checked against that source. Native
host receipts use pretty JSON; their decoded content must equal Git-derived
receipts, while the approved ZIP digest still binds their serialized bytes.

Superseded artifacts `11315198085` (#466), `11317509383` (#470), and
`11293625514` (#463) and runs `37234866451`, `37243206476`, and `37175265248`
are explicitly denied as final promotion inputs. No partial predecessor can
replace the new convergence artifact.

## Published primary replay and small secondary receipts

Executor attachment download currently caps files at 32 MiB. The primary
frozen ZIP is larger. Predeploy therefore downloads its already uploaded
primary artifact once in the same runner, verifies upload output ID/digest
against GitHub metadata and the exact run/workflow/head/attempt, checks the
outer size/digest and entire ZIP CRC, and replays source, bundle, raw image
config and ordered layers. It reconstructs `/app` by applying layer deltas,
including opaque/deletion whiteouts and repeated/compressed blobs. Every
required source byte and full mode plus generated provenance metadata must
match Git; missing, excluded, extra, or aliased source fails closed.

Before the primary upload, the integrator's convergence provenance CLI verifies
the original source orders and actual governed JUnit executions and produces
`porota-final-input-provenance.json`. The primary must contain exactly one such
receipt. Published replay downloads that original receipt and preserves its
bytes and hash in the small secondary, without regenerating it. Missing or
homonymous receipts fail closed. The final reviewer independently validates
the input source anchors and expanded executed test nodes.

The runner loads that downloaded archive and checks the actual image ID,
then executes imports and installed-closure checks in an ephemeral container
with network disabled and no host volume mounts. This does not build a second
image or access a host/runtime/provider database. Native urllib removes
Authorization from every redirect, bounds response/download sizes and
redirect counts, checks expected bytes during streaming, and enforces a
600-second total wall deadline through both per-request clocks and a process
timer. Signed URLs, headers, tokens, and exception details stay out of logs.

Predeploy exports only JSON manifests and verification receipts as
`porota-predeploy-v2-evidence-only-<SHA>-run-<ID>-attempt-<N>`. The raw receipt
set is bounded at 24 MiB and the uploaded secondary artifact is checked by API
at <=32 MiB. Its index hashes every receipt; its primary tuple identifies exact
artifact ID/name, workflow ID/path, head, run/attempt, digest, bounds and source
tree. It records layer/rootfs and native CLI execution results and exact
loaded/frozen IDs. Images and deploy bundle bytes are absent from this secondary.

These receipts are evidence only and cannot grant promotion authority. The
run is still IN_PROGRESS while generating them; production promotion separately
requires completed SUCCESS and approved merge trailers. The final reviewer
must revalidate the external GitHub API, frozen source/workflow definition,
jobs/logs, and receipt manifests. The secondary never bootstraps its own trust.
The unchanged primary artifact remains the sole promotable artifact.

## Image identity and permission modes: U12/U25

Immediately after Docker load, before installing candidate source or creating
any tag, the loaded image ID must be a full `sha256:<64 hex>` and equal the
frozen expected ID. The transferred tar digest is rechecked. Docker's display
serialization of `.Config` is retained only as a diagnostic; it cannot replace
the exact raw exported config digest that defines the image ID. Observer and
dashboard must subsequently use that exact loaded ID.

Tracked regular modes map exactly `100644 -> 0644` and `100755 -> 0755`.
Source checkout, bundle entries, and image files must match the full mode,
including write permissions and special bits. Generated provenance metadata
must be 0644. Honest rebinding of an outer digest cannot authorize 0600, 0640,
0666, setuid, setgid, or sticky variants. Runtime health and private credential
env files have separate roles; private env files are atomically created 0600.
Promotion validates the complete install set and destination aliases before
its first copy, then atomically replaces each file with verified source bytes
and mode. Existing symlink/hardlink targets and symlink parents fail closed.

Dashboard launcher verifies source/image SHA, tree, and source manifest digest,
then every mounted hook/module/package byte and mode, before stopping or
removing a container. The complete `rc6_trader_dashboard` package is mounted
read-only with its hook. Unknown, missing, aliased, stale, or writable source
cannot create a mixed-provenance dashboard.

## Dynamic capacity launcher: U04

Both canonical observer and dashboard receive these six explicit variables:

| Variable | Contract |
| --- | --- |
| `POROTA_DYNAMIC_CAPACITY_MODE` | Explicit OFF, SHADOW, or APPROVED; default OFF |
| `POROTA_CAPACITY_POLICY_PATH` | Policy JSON in an allowed mounted input root |
| `POROTA_CAPACITY_REPORT_PATH` | Benchmark/report JSON; required for APPROVED |
| `POROTA_CAPACITY_RECOMMENDATION_PATH` | Recommendation JSON; required for APPROVED |
| `POROTA_CAPACITY_APPROVAL_PATH` | Independent approval JSON; required for APPROVED |
| `POROTA_CAPACITY_SHADOW_PATH` | Canonical committed SHADOW directory or its CURRENT/legacy selector |

Input roots are `/app/ops/policy` and `/app/data/rc6-capacity`. Paths must be
absolute, normalized, bounded regular JSON files with no symlink parent, hard
link alias, executable/special bits, or group/other write permissions. Control
characters and env/shell injection characters are rejected without logging
values. Both containers bind `/app/ops/policy` read-only; when data inputs are
configured, the nested `/app/data/rc6-capacity` input root is also mounted
read-only inside the broader writable data volume. The mounts provide exactly
the validated host inputs. APPROVED
requires all three evidence paths before any engine shutdown and retains the
controller's evidence/fingerprint/digest/expiry checks. Presence of a benchmark
alone never changes feature authority or enables real order routes.

SHADOW evidence is `artifact_root(database)/dynamic-shadow`. Explicit launcher
shadow selection can name that directory, `CURRENT.json`, or `latest.json.gz`;
all select the same committed generation. Derived `POROTA_DYNAMIC_SHADOW_ROOT`
and alias `POROTA_SHADOW_RUNTIME_ROOT` must agree. The launcher constrains this
runtime to the canonical mounted root. `POROTA_BUILD_SHA` and
`POROTA_CANDIDATE_TREE_SHA` are derived from verified staged provenance, never
inherited from an operator's env.

## Child health and current committed evidence: U13

The parent writes `artifact_root(database)/runtime-health.json` with schema
`rc6.runtime-child-health.v1`, recording source SHA, candidate tree,
`recorded_at`, and child state/PID/restarts/spawn failures/start and exit times.
States include RUNNING, STARTUP_WAIT, SPAWN_FAILED, CRASH_BACKOFF, and STOPPED.

The read-only `scripts/rc6_shadow_health_gate.py` independently requires a
current health record, exact source/tree, RUNNING child with a live PID,
observable integer counters, and a committed V2 generation read through the
external persistence reader. Report/status/manifest fingerprints must equal
the canonical read-only worker factory's current configuration. Evidence must
be at most 90 seconds old, never future-dated, and published after the child
started. Health must be at most 20 seconds old. A recovered child with nonzero
restarts must be stable for at least 60 seconds (two 30-second cadences) and
publish fresh evidence. A running scanner is insufficient when SHADOW is dead,
backing off, stale, or in publication recovery.

PREOPEN and CLOSED are nonoperational. Even fresh OPEN SHADOW evidence retains
`provider_capacity_open=NO_VERIFICADO`; synthetic readiness does not establish
real provider capacity or OOS edge. The gate runs after startup, at every
stability cycle, and throughout the final soak. It does not contact providers,
change source databases, or publish evidence. Persistence custody is
`LOCAL_DURABLE_CUSTODY_NOT_EXTERNAL_AUTHENTICATION`; this is an explicit local
trust boundary, not an external authentication or WORM claim.

## Dynamic disk admission and scoped cleanup: U28

`ops/policy/rc6-disk-housekeeping-v1.json` is the admission authority. Root YAML
is aligned. Required free bytes before first transfer are:

```text
compressed image tar + exact Docker image size + 2 * bundle size
+ (512 MiB - identified resident SQLite scratch)
+ max(0, 128 MiB - bounded verified resident live SHADOW)
+ max(0, 512 MiB - bounded verified resident private archive)
+ one shared 2 GiB residual reserve per filesystem
```

The launcher shares canonical archive root
`artifact_root(PRIMARY)/dynamic-shadow-archive` and cap 512 MiB between observer,
dashboard and the operational worker factory. It prepares only new directories
as bot1000, mode 0700, before the combined native admission and before stopping
either container. Existing aliases, wrong ownership/modes and unknown namespaces
are rejected without repair or deletion. Recognized archive/live interruptions
are counted and close admission until their producer recovers them. Missing
derived roots reserve their full future growth before transfer.

Candidate-native source is embedded verbatim for the remote read-only probe;
it requires no old host checkout and opens no source with SQLite. Scratch, live
and archive must share the canonical DATA filesystem. The separate DATA gate
checks all derived growth plus one 2 GiB reserve and the 10% inode floor; the
deployment filesystem gate additionally accounts for image and bundle bytes.
When DATA and deployment share a filesystem these are comparisons against the
same available bytes, never an addition of two reserve terms. A different DATA
filesystem independently retains its own reserve.

Archive/live inventory verifies bounded namespace and custody metadata only.
For disk admission, resident discount is the smaller of logical quota usage and
verified allocated block bytes, so sparse logical bytes cannot reduce physical
growth reserve. If allocation metadata is unavailable, discount is zero and the
full future quota is reserved. Logical usage and block allocation remain separate
fields in the receipt.
It does not prove CRC, receipt validity, logical semantics, recovery, external
authentication or the contracted nine-hour retention horizon. A canonical
configured destination alone cannot close that retention gate.

The 2 GiB term is residual reserve. There is no reinstated fixed 6 GiB start
floor. Admission also requires the policy inode floor (10%). On RED, existing
policy-guarded housekeeping and scoped candidate cleanup run before transfer,
then admission is measured again. The promote load check subtracts already
transferred compressed image and bundle bytes from the same candidate-specific
requirement. Final residual and inodes are remeasured after cleanup. Actual
host footprint and pressure remain pending until the authorized final artifact
and host are measured; offline tests make no assertion about host free space.

`scripts/rc6_deploy_scoped_cleanup.py` enumerates only exact RC6 candidate tags.
Stable/current image IDs, explicit pins, and all ancestor references, including
stopped containers, protect candidates. Removal is per tag and never forced.
This routine has no global image/builder/buildx prune, shared cache eviction,
volume/data mutation, or PPI Watch service/container inspection. Existing
SQLite, containerd, swap, retention, and checkpoint safeguards remain policy
requirements.

## Hash-locked distributions and Actions: U29

`requirements.lock.txt` retains the original 153 package versions and adds
the previously implicit `packaging==26.3` dependency. Its 154 runtime rows have
real reviewed PyPI SHA256 distribution hashes. `requirements.build.lock.txt`
hash-locks pip 25.2, setuptools 80.9.0, and wheel 0.45.1. Thus expected installed
closure is 157 distributions, not an assumed equality between a shared test
venv's freeze and the runtime lock.

`ops/policy/rc6-supply-chain-v1.json` records distribution filenames, sizes,
hashes, metadata source/digest, allowed platform, and Action commits. Supported
platform is Linux x86_64, CPython 3.11/3.12, glibc >=2.36. Compatible reviewed
wheels are enforced with `--require-hashes --only-binary=:all:`; only the four
hash-locked sdists `msgpack`, `ppi-client`, `signalrcoreppi`, and `ta` can compile.
Build isolation is disabled after installing the hash-locked build tools.
Mutable/unreviewed Action refs, distribution/hash/version drift, unpinned base
image, wrong platform, missing glibc, unbounded build isolation, and unexpected
installed transitive distributions fail the reproducibility gate. Predeploy
also checks the installed closure inside the image.

Canonical checkout, upload-artifact, and setup-python use verified full commit
SHAs. Regeneration is explicit through `scripts/porota_lock_distribution_hashes.py`
and public PyPI metadata; CI/deploy do not regenerate a lock from the network.

This is bounded reproducibility of Action commits and Python inputs. It is
**not a fully hermetic build**: the hosted runner/kernel, apt package repository
bytes, and native compilation remain explicit residual boundaries. The pinned
base image and hash-locked toolchain narrow these boundaries without claiming
identical source-built wheel bytes across arbitrary hosts.

## Verification evidence and limits

The SRE regression files exercise approved metadata mismatches, replaced ZIPs,
full frozen source/bundle/image/host receipts, actual extracted pre-tag shell
guards, full permission modes, real launcher/controller input propagation,
real V2 SHADOW producers/readers, dead child processes, recovered restarts,
scoped cleanup with active/stopped pins, hash/action/platform drift, and exact
installed closure. Native provenance/deploy/integration tests remain included.

An isolated Python 3.11 environment installed all 157 hash-locked distributions
using the real pip installer, with `pip check` GREEN. A changed requests hash
was independently rejected by pip. This does not represent a Docker build,
production deploy, provider OPEN probe, or final candidate artifact. The final
integrator must record immutable final SHA/tree, automatic discovery results,
new Predeploy V2 run/attempt/artifact/digest, exact frozen footprint, and review
approval before any production action.
