# Source capture boundaries

The original full source pin f889/157 reproduced three late success returns after closing or cleanup, and two blocked opens after an adversarial MAIN/WAL replacement with FIFO. The exact timeout reason was hidden by the existing exception taxonomy. All original diagnostic files remain byte exact here; their replay produced seven failures and five control passes with all 1,557 product bytes/modes/Git blobs unchanged.

The correction requires nonblocking source open before the existing metadata check and checks the same absolute deadline after both closing SQLite and completing private scratch cleanup. It preserves the original caller error and adds only the exact safe token `TIME_BUDGET_EXHAUSTED`. SQL, financial calculations, native clocks, all byte/identity checks, quota and deadline values remain unchanged. The permanent capability guard uses a real descriptor, strengthening the first diagnostic interceptor which returned an invalid descriptor.

The separate component timing probe completed two reduced-heap captures in 65 and 58 ms, without GC during either capture. It omits native planner holdings and does not reproduce the 579 MB full-tick heap. No marker/quota optimization, GC suppression, whole-tick cache, deadline increase or claim that the BIG failure is fixed follows from it.

The native fix replay is pending at this checkpoint. No complete pipeline, nine-hour retention, final artifact or runtime acceptance is claimed.
