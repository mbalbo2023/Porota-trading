# Exact volatile binding compatibility evidence

The five-module Native focal passed 271/271 cases on whole Git source
`d51b4577d2a41443c8fb453b768d5e2c88448e8d`, tree
`ceaa55e1852308fc46c4178928eae18d36aea962`. The 74 new cases compare
captured chunks, every literal slot, complete wire bytes, hashes and full
decode against an independent append/capture oracle from baseline blob
`f2d2c238c64b93a3a414d3427dbe9464e7812ffb`. The remaining 197 cases are the
existing small-container, allocation, packed-storage and funnel-codec guards.
There are no failures, errors, skips or duplicate JUnit nodes.

PID 220202 ran the literal frozen Python 3.11.16 interpreter after checking
157 exact lock names and versions before fixtures. All 1,940 Git file SHA256s,
modes and blob IDs remained unchanged; the source had zero overlays.
The receipt records 97 own-source imports, zero alien imports and zero network
or protected source-SQLite attempts. DATA400 was not an input. The 11.271-second
elapsed time includes focal setup and does not measure publisher performance.

[DOSSIER.json](DOSSIER.json) lists every executed JUnit node, the original
source/TAR/index bindings and SHA256s for 15 originals. Each stored `.gz` has
`mtime=0`; decompressing it recovers the exact original bytes. The whole source
TAR and large DATA are not included. The original raw directory is
`/tmp/rc6-packed-volatile-d51b4577-guards-raw/` and the indexed whole archive is
`/workspace/rc6-packed-volatile-d51b4577-source`.

Finance caught an unqualified `PACK_TARGET` in the initial, unexecuted oracle
WIP. Its original bytes and classification are preserved. The source was
corrected before d51b and before Native execution; this is a static harness
preparation defect, with no product RED or failed Native run attributed to it.

This evidence verifies functional compatibility of a private binding-byte map
within one capture call. The entry and canonical-byte bounds only trigger the
original conversion as fallback; the 1-MiB bound is not total Python heap or
RSS. No cache survives the capture, including failure and nested calls. The
map keeps every literal occurrence and is separate from caller-supplied caches.

No saving, branch frequency, complete BIG90, retention horizon, UX/browser,
image or runtime acceptance is claimed. BIG9d32 remains RED and its original
RAW is preserved by the coordinator. Full PREOPEN plus OPEN must still meet
the original 90-second deadline, 2-GiB RSS and all original quotas. The earlier
TIMED auxiliary SIGSEGV remains unattributed.
