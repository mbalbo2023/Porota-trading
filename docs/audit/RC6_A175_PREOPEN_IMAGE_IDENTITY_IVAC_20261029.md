# A175 — Preopen image identity, NO_GO continuity (IVAC)

## Incident and causal boundary

- Deploy V2 `40796450488` reached T_MINUS_45, then `observer_container` and
  `dashboard_container` became RED. Host inspection reported both running,
  restart count zero, OOM false, and `Config.Image` equal to the frozen
  `porota-trading-bot:17.0.0-rc6-candidate-<SHA>` tag. The workflow had already
  verified their immutable `.Image` IDs and in-image SHA/tree. The preopen
  implementation required the stable literal tag, which Docker retagging does
  not rewrite in an existing container's `Config.Image`.
- Read-only image inventory Action `40798107395` confirmed the A175 loaded
  image is the current stable alias; it did not authorize PAPER admission.
  The exact full image ID, final runtime status, and post-fix host result must
  be obtained from the next frozen candidate's Deploy receipts.

## Fix and verification contract

- Candidate tag is admitted only when its full SHA matches the deploy argument
  or a daily `VALIDATED_RUNTIME` marker; frozen receipt SHA/tree/PAPER/0 and
  bundle manifest's installed preopen/version file hashes match; Docker's
  candidate and stable aliases both resolve to the exact running immutable
  image ID; image environment proves the same source SHA/tree. Original tar
  ID remains evidence, since `docker load` can normalize image configuration.
- Stable-tag containers retain an exact immutable ID check. Daily use also
  requires a `VALIDATED_RUNTIME`, PAPER/0, routes-not-called marker; a stable
  tag cannot bypass that marker. Deploy with an explicit new candidate cannot
  mistake an old stable-tag container for its new runtime.
- A176's exact 29-Oct administrative NO_GO sidecar is required before accepting
  a held Deploy preopen. Its digest, operation, historical A151 origin, A175
  receipts, and `paper_execution_authorized=false` are reflected in the
  preopen output and rechecked by the Deploy result gate. The independent
  PAPER admission interlock continues to deny GO. Daily preopen checks the
  same continuation sidecar, permits the preopen timer to remain disabled,
  and never overwrites the historical A125 receipt. A missing or altered
  sidecar remains RED; no date transition confers GO.

## Regression and scope

- Offline Docker-command replay covers candidate Deploy and daily admission,
  stable-only legacy, forged tag/alias/immutable ID, source SHA/tree, frozen
  receipt, installed bundle source, PENDING/tampered marker, OOM/restart, and
  observer read-only violation.
- A176 tests cover the 28→29 date boundary, missing/tampered continuation,
  held and restored timer modes, forged GREEN, and manual daily NO_GO without
  writing A125. These are local/predeploy proofs only. The final frozen SHA,
  complete Predeploy V2, exact host ID/receipt, runtime validation, and
  administrative ownership release remain to be established by canonical
  GitHub Actions. No real order operation was performed.
