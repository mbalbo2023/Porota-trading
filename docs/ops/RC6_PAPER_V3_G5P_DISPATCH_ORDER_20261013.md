# RC6 PAPER V3: dispatch order for an exact Source

Incident: G5P-acquire run #574 failed admission before payload with
`G5P_LONG_RUN_INITIAL_DECISION_NOT_LIVE_AT_ACTUAL_START`. Cheap run #573
closed GREEN for Source `65622b017b3f3ffec3d57704483611b30643b66b`,
but its 63-minute duration exhausted the separately bounded G5P contract
acceptance. Owner renewal does not renew an immutable decision.

For a new Source, create and inspect the commit and tree before moving the
candidate ref. Keep the candidate PR draft. Arrange the first material
dispatch so that G5P-acquire starts with a newly posted, paired owner lease,
a fresh exact Source contract acceptance, and one immutable single-gate
launch. Its prerequisite manifest is the contracted empty acquisition
manifest. At admission the PR must be open, draft, owned by the canonical
repository, and based on `deploy/rc6-pr69-isolated-20260915`.

G5P-acquire is acquisition only. It does not qualify cheap, G2, G3, G4,
G5P-certify, full governance, Predeploy V2, or the final candidate.
Run cheap for the same exact Source after acquisition under its own fresh
acceptance and exactly one prepush launch; preserve its original tests,
JUnit accounting, controls, and RAW. Each later gate consumes the exact
ordered artifacts required by its own admission.

A failed first attempt consumes the gate/Source identity even if no payload
starts. Fix forward with a new evidenced commit and fresh receipts; do not
re-run the same gate for the same Source or repurpose old artifacts.
For a long producer, renew the paired owner lease before expiry throughout
the actual interval. Never extend or reinterpret an expired acceptance.
PAPER/SHADOW only, real orders sent zero, and PPI Watch untouched.
