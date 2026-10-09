# Real-cut capture and restart diagnostic

State: DESARROLLADO. Original driver `15b36374` has 18/18 native custody/failure
guards on frozen311157. Its restore diagnostic completed; its report decode
terminated with SIGSEGV before preparation. The explicit sampling-selector
driver `dc817b73` has 21/21 native guards. Its single NONE control decoded the
sealed report and entered preparation but was stopped by its 60-second
watchdog. Receipt/default-NONE driver `8d57319c` has 34/34 native guards on the
same frozen311157 environment. None of these guards replays a large decode.
This diagnostic does not
modify product code and does not establish acceptance of the 90-second runtime
pipeline.

WRITE_OWNER: `/root/persistence_review`; workstream
`rc6-capture-restore-diagnostic-20261005`; branch
`work/rc6-capture-restore-diagnostic-20261005`; base
`400a677c7a2d94e52fcc7e1c598f94a66862253c`, tree
`4694f053baf294c324e4cea7f66500b22b4c3004`. Scope is the diagnostic script, its
custody/failure guards and this plan. Runtime writers, mathematical functions,
GC policy, reader guards, deadlines and quotas are outside this change.

The original full run published a real PRE cut and entered its subsequent
checkpoint restart read before the original 90-second deadline. It did not
complete the two-tick pipeline. Aggregate `storage_prepare` time includes the
earlier funnel preparation; role entry intervals include entry overhead and
are not independent per-role native timers. The original RAW remains under
`/tmp/rc6-canonical-400a677c-big-raw`.

The two phases run independently, each in a new process with a 60-second
diagnostic watchdog, a 2-GiB RSS bound, stack samples every ten seconds, logs
bounded to 1 MiB and samples bounded to 64 KiB. The wrapper uses literal
`/workspace/venv_rc6_frozen311/bin/python`, Python 3.11.16 and exactly the 157
locked distribution names/versions before any copy or product import. This
version check is not a distribution-byte proof.

Product imports come exclusively from the already frozen whole Source400:
`/workspace/rc6-whole-source-20261005-packed400`. The index is
`/tmp/rc6-whole-source-20261005-packed400-raw/source.index.json`. Every Git file,
mode and blob ID is checked before and after; the wrapper records the source
index, TAR, profiler and four relevant product-module hashes. There are no
product overlays.

The original data is `/workspace/rc6-canonical-400a677c-big`. It is inventoried
through NoAtime directory descriptors and file reads. Full SHA and eleven
selected fields are compared before and after: device, inode, UID, GID, mode,
link count, size, allocated blocks, access, modification and change times.
This is the selected eleven-field contract, not every possible OS stat field.
Both `dynamic-shadow` and its separate authority directory are copied exactly
into a fresh private tree. No control bytes are rewritten. Files and ancestor
identities are checked against the original inventory during copying.

The private copy is limited to 512 MiB using the larger of logical and allocated
sizes, including directories. Before copying and each write, the diagnostic
requires at least 2 GiB filesystem residue and ten percent free inodes.
Its private tree is separate from operational SQLite-read scratch; it does not
claim an operational scratch reservation or measured runtime scratch peak.
Use a new output directory on `/workspace`, not tmpfs. The copy is retained
as diagnostic evidence. Filesystem admission, failures and process cleanup are
recorded; source custody checks still run after child or copy failures.

All outbound socket connection, send and DNS entrypoints are blocked. Both
SQLite connection aliases reject original data paths. The child calls the
native read-only `EvidenceFiles.read_writer_generation`; it never enters the
writer context, creates a source lock, calls a worker tick or sends an order.
Private-path custody rejection is recorded as a rejection, without changing
authority or manifest bytes.

`restore` uses `read_writer_generation(checkpoint=True)` on the private copy and
records the native four-role wire verification and checkpoint restart parsing.
`capture-report` first validates the same cut, then uses native logical report
decoding and semantic/header checks. It prepares and measures that real logical
report. Its digest and logical length must equal the sealed original report.
Decoding can reconstruct a different object-alias layout from the publisher;
this phase is explicitly a diagnostic on the decoded graph and makes no
publisher CPU or original alias-layout claim.

Timers wrap whole reads, payload parsing, unpacking, shape validation and
builder/counting initialization, with capture timings per root field and
aggregate named-subtree capture. They do not trace every JSON token. Nested
timers are inclusive and must not be summed as independent CPU demand. GC
thresholds are recorded without callbacks or changing policy. The original
source-capture 0.25-second budget and runtime 90-second deadline remain intact;
the independent 60-second watchdog is labelled diagnostic-only.

The original capture-report diagnostic died at 10.237 seconds with signal 11,
while its first timed stack sample stopped at the serialization filename. The
last recorded stage is report logical decoding. Native report preparation
was not reached and no final native receipt was written. This is an unknown
classification: neither a product defect nor a sampling cause has been proved.
The original driver does not register GC callbacks, trace each token, arm
multiple watchers or close its stack stream before cancellation. Its restore
diagnostic completed before the first sample.

The auxiliary profiler now defaults to NONE; this avoids its sampler and does
not demonstrate or repair the unexplained SIGSEGV. Explicit
`--stack-sampling timed` preserves the original ten-second periodic watcher.
The explicit `--stack-sampling none` diagnostic control installs no timed,
registered or fatal faulthandler callback. It retains the same native reader,
timers, private-copy custody, 60-second watchdog and 2-GiB bound. Native PID,
UID, interpreter, actual core/stack/address-space resource limits, initial
faulthandler state and fatal return signal are recorded. A single coordinated
NONE control is authorized after its guards pass; even a favorable result is
only a discriminator for the sampling hypothesis, not acceptance or permission
to repeat controls or change product `_loads`.

Completion also requires a final native receipt. A zero child exit without
that file is rejected. PID, phase, sampling mode, original cut, manifest,
four-role proofs, source imports, zero network/source-SQLite attempts, native
completion and memory/GC checks must match before the wrapper can report
`diagnostic_completed=true`. This is a separately identified static driver
gap; it neither caused nor changes the preserved SIGSEGV/timeout outcomes.

After the SOURCE checkpoint and coordinated CPU authorization, run the two
commands sequentially, each using a new RAW directory:

```sh
/workspace/venv_rc6_frozen311/bin/python -B -u <checkpoint>/scripts/rc6_capture_restore_diagnostic.py --phase restore --source /workspace/rc6-whole-source-20261005-packed400 --source-index /tmp/rc6-whole-source-20261005-packed400-raw/source.index.json --data /workspace/rc6-canonical-400a677c-big --raw /workspace/rc6-capture-restore-400-restore-raw
/workspace/venv_rc6_frozen311/bin/python -B -u <checkpoint>/scripts/rc6_capture_restore_diagnostic.py --phase capture-report --source /workspace/rc6-whole-source-20261005-packed400 --source-index /tmp/rc6-whole-source-20261005-packed400-raw/source.index.json --data /workspace/rc6-canonical-400a677c-big --raw /workspace/rc6-capture-restore-400-capture-raw
```

Child PID, actual argv, watchdog outcome, partial progress, typed exception
frames, actual RSS, source/private custody and all timing data remain in RAW.
Timeouts are retained as failures. `acceptance_complete` is always false.
No favorable retry, deadline/quota change, whole-tick cache, GC suppression or
lossy input reduction is authorized by this diagnostic.
