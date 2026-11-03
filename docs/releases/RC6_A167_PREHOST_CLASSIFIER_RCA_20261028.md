# RC6 A167: prehost classifier on the installed #751 runtime

Status: **NO_GO**, PAPER/SHADOW, zero real orders. This is a fix-forward
candidate; it is not a runtime validation or permission to reactivate trading.

## RCA and observed impact

Deploy V2 run `40750131063`, job `123034340337`, merge SHA
`aef17ca4bf8401ec85635541615500346ff33981`, passed the frozen artifact,
owner literal authorizations, Droplet identity, SSH argument bound and host
barrier reconciliation. Its prehost classifier then raised
`AttributeError: module 'scripts.rc6_deploy_quiescence' has no attribute
'A143_PENDING_IMAGE_ID'`. The installed #751 helper predates that symbol.
The classifier read it in an `elif` guard before decompressing and executing
the staged candidate, so the A145 evidence path could never run on that host.

The attempt stopped before scheduler suspension, transfer, Docker load or
runtime restart. Its pre-suspend accounting reported
`NO_RUNTIME_RECOVERY_NO_TIMER_WRITES` and 7,137,853,440 bytes free on `/`.
No state from that attempt is counted as VALIDATED_RUNTIME. The prehost
barrier and disk are rechecked by the next exact read-only A160 admission.

## IVAC: isolation, verification, action, control

- Isolate the installed module to its old stable functions. Use the previously
  verified exact A143 Docker image ID in the prehost guard. The staged
  candidate must independently expose the same ID before its proof is used.
- Verify staged bytes against the candidate checkout's SHA-256; cap gzip
  input and output and reject incomplete or trailing compressed data.
  The existing A143 proof still checks the marker, frozen manifest, prior
  receipt, two container identities, flat PAPER book, heartbeat and operator
  hold. Unknown identities or sources fail closed.
- Execute a new Predeploy V2 for this exact SHA/tree, then the identity/disk
  and A143 admission preflight, literal #624 authorization bound to that
  candidate, and one canonical Deploy V2. No prior artifact can be reused.
- Retain the regression that runs the actual inline classifier with an old
  host module lacking the new attribute; it also rejects source hash and
  candidate image drift. The A143 proof tests independently cover receipts,
  Docker mismatch, flatness and stopped continuation. All workflow shell
  sections receive syntax checks and the SSH argument budget is measured.

This correction does not touch PPI Watch, real-order routes, host stress or
the PAPER NO_GO operator hold. IOL token renewal is a separate read-only
source-health result; its cache stays AMBER until a verified collector sample.
