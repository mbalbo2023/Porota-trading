# Predeploy V2 scoped runner cleanup — source review

This checkpoint develops runner cleanup only. It does not execute Docker, Actions,
governed tests, a build, production runtime or deployment. The original finding
is a source-inferred contract failure, not an observed Docker failure.

The baseline is Git commit `4b8f6104380ef1a2338a4f246045e0a19b62831e`, tree
`b020d609ae5d63440fa74d7967bb250f817f235a`. The adjacent JSON preserves the old
cleanup step verbatim, the whole workflow blob/SHA256, source hashes and the
planned controlled focal. The old step ignores removal errors and always emits
GREEN; the preceding summary also emits overall GREEN before cleanup. It uses an
ancestor container query and shared fixed temporary paths without run ownership.

The fix retains `porota-predeploy-v2:<candidate_sha>` and one build. A private
run/attempt/UUID root and immutable marker bind the repository, source candidate,
tree, euid, device/inode and mount identity. Build labels add run ownership; the
actual built ImageID is captured after complete label/tag verification. An
explicit build-start phase permits a failed capture to be handled honestly, using
complete own-label identity rather than treating missing ImageID as no build.

Only UUID-filtered containers with matching labels and ImageID may be removed.
Bind mounts, volumes, foreign tags/digests, aliases and unknown custody fail
closed. A foreign container reference makes non-forced image removal fail; its
container metadata is never inspected or deleted. Parent images and global
Docker caches are not pruned. The local Docker Unix socket is explicit.

Temporary outputs use the private root with their original basenames. Governed
pytest alone receives that root as RUNNER_TEMP, so the actual issue465 stress
producer, upload and cleanup agree. Global RUNNER_TEMP keeps the ownership
control and measured cleanup receipt outside the root to be removed. No test
exclusion, threshold or product policy is changed.

Before any mutation, the helper validates the current Git/context and external
root/UUID binding and exclusively publishes a PENDING cleanup receipt to a safe
output. It validates every private directory/file with NOFOLLOW and mount ID,
including same-device bind mounts, before fd-relative deletion. The generated
source manifest is removed only with its previously claimed identity and hash.
A failure or remaining owned resource yields RED and a nonzero exit. Partial
removals and retained evidence remain visible in the receipt.

Filesystem measurements cover the checkout, private output and Docker data
filesystem, deduplicated by device. The receipt preserves signed free-byte and
free-inode deltas; positive delta is an observed free-space increase, not proof
that every byte was attributable to this job. Files and directories are counted
separately. The authorized 2 GiB and 10% inode requirements apply after cleanup;
there is no new fixed start gate or larger runtime quota. The cleanup's total
budget is 180 seconds, each Docker call at most 10 seconds. The alarm remains
active through receipt fsync and log flush.

Primary upload and bounded secondary JSON/XML upload precede cleanup. The final
summary now follows cleanup and requires its successful exit and a GREEN
candidate/run/UUID-bound receipt. A preserved primary artifact cannot approve a
failed run. The receipt is logged after upload; it neither replaces nor mutates
the sole promotable primary ZIP. Final independent replay must still require
completed-success GitHub execution and the exact artifact tuple.

Fifteen Python heredocs and shell run blocks have been parsed without product
imports or collection. Controlled guards cover failed Docker removal, remaining
images, partial build, every origin label, shared references, aliases/mount
identity, source/control substitution, safe output ordering, capacity, deadline
and workflow producer/upload/cleanup ordering. Their execution is pending the
Root CPU slot on a fresh whole source checkout with the exact frozen dependency
closure. They do not constitute an image or runtime validation.

A further source review of checkpoint `edc2b57d` found that a FIFO substituted
for a control/marker/receipt/source-manifest could block the initial read opener
before its regular-file check. The forward fix adds O_NONBLOCK without relaxing
regular-file custody; a FIFO with no writer must be rejected immediately. Its
controlled guard has a one-second watchdog so a regression fails visibly rather
than hanging the focal. This finding remains source-inferred, not a Native RED.
