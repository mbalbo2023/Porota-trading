# RC6 R309 bounded transport and witness plan

> **For agentic workers:** Use superpowers:executing-plans with TDD, task by task.

**Goal:** Add a bounded R305 Actions SSH transport and independent offhost root-manifest witness, without host execution.

**Architecture:** Trusted-main-only R309 adapter consumes R305's literal reviewed command set, runs one multiplexed SSH master with no fallback and bounded streaming. A separate owner-dispatched offhost workflow validates an R304 five-role manifest and emits only digest provenance; review, owner GO, R280 install and Deploy V2 remain separate.

**Tech Stack:** Python 3.11+ stdlib, unittest, OpenSSH, GitHub Actions.

**Spec:** #1093 R313/R310, R305 execute_transition and _actions_artifact_witness contracts.

## Global constraints

- PAPER/SHADOW only; PPI Watch/Boca untouched; no real orders, host writes, dispatch or Deploy.
- Do not modify R305/R308/R271 owner-reserved files or product/main/#1087.
- Reject unreviewed commands, SSH fallback, missing source guard, malformed output, expired budgets, forged identities.
- R309 call: stdin <=8192 bytes, output <=256 bytes across pipes, per-call <=15s, total <=540s; no retry after lock.
- Witness does not approve its own manifest or establish HOST GO.

## Review focus

- Loss of master socket never causes new direct TCP connection.
- Output flood, timeouts and unapproved sudo fail closed before any retry.
- Noncanonical D manifest, incorrect candidate SHA/tree or wrong actor/ref yield no artifact.
- Witness SHA/digest is tied to exact source and run/attempt.

### Task 1: R309 SSH adapter

**Files:** Create scripts/rc6_r309_bounded_mux_host.py and tests/test_rc6_r309_bounded_mux_host.py.

**Interface:** R309BoundedMuxHost(env) context manager with .call(command, stdin=None, max_output_bytes=256, timeout_s=15) -> (returncode, stdout_bytes).

- [ ] Write failing tests for command allowlist, caps, secret file modes, one master/no fallback, cleanup, timeout and no retry.
- [ ] Run RED. Implement minimal adapter reusing R295 streaming runner, not R295 command list.
- [ ] Run GREEN, R295/R305 relevant tests, compilation and diff check, then review and commit.

### Task 2: R305 digest witness

**Files:** Add scripts/rc6_r305_manifest_witness_offhost.py, tests/test_rc6_r305_manifest_witness_offhost.py and .github/workflows/rc6-r305-root-manifest-witness.yml after updating #980 ownership.

- [ ] Write failing tests for strict R304 canonical bytes, five roles, valid candidate GitHub tree and immutable run metadata.
- [ ] Run RED, add witness generator and trusted-main owner-only offhost workflow, then GREEN/review/commit.
- [ ] Never dispatch host, create a host-GO, merge main or build Docker during this task.

### Task 3: Integration handoff

- [ ] Obtain R305/R308 owner transfer before publishing their reserved files.
- [ ] Preserve R304+R305 as a trusted-main code PR and R308 at #1087, by exact per-file blobs; never merge divergent branch histories.
- [ ] Fresh witness/owner-GO, host staging and one Deploy V2 remain gated outside this code plan.

**Ruling:** The user already requested autonomous execution of R313; this plan is not a substitute for owner-transfer or host approval gates.
