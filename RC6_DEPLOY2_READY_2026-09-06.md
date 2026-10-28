# RC6 Deploy2 Ready

This branch is the fail-closed transactional deployment candidate for POROTA 17.0.0-rc6.

Safety invariants:
- PRODUCTION_PAPER
- SIMULATED execution
- REAL_ORDER_CAPABILITY=BLOCKED
- no network order test
- immediate rollback uses the local RC5 image with `--pull never`
- daily RC6 preopen has no RC4 runtime dependency
- legacy unit files may remain inert for rollback evidence, but RC4 timers/services must not be active after RC6 activation
- host-only executable bits for the two exporter scripts are incorporated into the candidate tree
