# Source capture boundaries

The original full source pin f889/157 reproduced three late success returns after closing or cleanup, and two blocked opens after an adversarial MAIN/WAL replacement with FIFO. The exact timeout reason was hidden by the existing exception taxonomy. All original diagnostic files remain byte exact here; their replay produced seven failures and five control passes with all 1,557 product bytes/modes/Git blobs unchanged.

The correction requires nonblocking source open before the existing metadata check and checks the same absolute deadline after both closing SQLite and completing private scratch cleanup. It preserves the original caller error and adds only the exact safe token `TIME_BUDGET_EXHAUSTED`. SQL, financial calculations, native clocks, all byte/identity checks, quota and deadline values remain unchanged. The permanent capability guard uses a real descriptor, strengthening the first diagnostic interceptor which returned an invalid descriptor.

The separate component timing probe completed two reduced-heap captures in 65 and 58 ms, without GC during either capture. It omits native planner holdings and does not reproduce the 579 MB full-tick heap. No marker/quota optimization, GC suppression, whole-tick cache, deadline increase or claim that the BIG failure is fixed follows from it.

The real-descriptor supplemental capability replay also failed on the original full source pin, with its source bytes/modes/Git blobs intact. It confirms the capability guard without the initial invalid-descriptor interception; the initial seven-failure/five-pass RAW remains unchanged.

The corrected whole OWN825 source passed 127/127 cases on the literal frozen311 interpreter with the exact 157-name/version lock set: 12 permanent capture-boundary cases, 89 existing native source/migration/family/lab/entry/wiring cases, 10 history-copy cases, 15 disk-scratch cases and one existing exception-secret guard. All 1,545 Git files and modes/blob ids remained intact; all 48 product imports came from that whole checkout and network attempts were zero. The native tests include actual WAL/SHM custody and byte protection. This execution belongs to OWN825, not its root cherry-pick or a final release.

No complete BIG pipeline, nine-hour retention, final artifact or runtime acceptance is claimed.
