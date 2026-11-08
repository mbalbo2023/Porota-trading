# Porota Private Rootless Metadata Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, authenticated, bounded host READ_ONLY receipt for Porota's dedicated rootless daemon without inspecting the shared rootful Docker daemon or changing the Droplet.

**Architecture:** Extend the existing `rc6-r1-docker-capacity-readonly.yml` workflow with one opt-in branch inside its existing `host_readonly` job, preserving its mutex and SSH keys. Stream a standalone stdlib probe over SSH; query only an explicitly pinned `/run/user/<porota uid>/docker.sock` and return a sanitized allowlisted receipt. A missing private account/daemon returns `NO_VERIFICADO`; metadata observation is never a deploy certificate.

**Tech Stack:** GitHub Actions YAML, Python 3 standard library and `unittest` contract tests; no new CI dependencies.

**Spec:** #732 A997, read-only limited infrastructure scope from the Porota RC6 workstream. Code remains an isolated proposal until reviewed and merged by the authorized owner.

## Global Constraints

- PAPER/SHADOW ONLY, `real_orders_sent=0`; do not touch PPI Watch.
- No host mutation, restart, Docker global inventory, Docker cleanup, or foreign-app access.
- `github.ref == refs/heads/main`, pinned known_hosts SSH, `rc6-unified-paper-deploy` with `cancel-in-progress: false`.
- Do not invent a physical OS account; `porota-rc6` is the proposed dedicated identity and absent identities fail closed.
- No use of main workflow success as proof of runtime/deploy certification.

## Review Focus

1. A globally configured `DOCKER_HOST` redirects a query: test the command pins the private socket and clears Docker environment selectors.
2. A forged symlink or shared runtime dir points at another app: test `lstat`, dedicated UID, socket type and private mode checks.
3. A daemon is reachable but rootful: test the `SecurityOptions` rootless flag is mandatory.
4. Docker data root is outside the dedicated private home or accessible by others: test path containment, ownership and ACL checks.
5. Private JSON contains a hostname, image labels or secrets: test only fixed allowlisted fields are emitted.
6. Docker CLI sends unlimited or stalled output: test bounded bytes, short timeout, child termination and suppressed stderr.

---

### Task 1: Local fail-closed metadata probe

**Files:** Create `scripts/rc6_rootless_metadata_readonly.py`; test `tests/test_rc6_rootless_metadata_readonly.py`.

**Interfaces:** `collect()` returns only a v1 allowlisted receipt or raises `ProbeError` with a constant diagnostic; `main()` emits bounded sanitized JSON for both paths.

- [x] Write failing tests covering a private `docker --host unix:///run/user/UID/docker.sock info` command and valid receipt; missing account/socket, wrong UID, unsafe ACL/symlink, rootful engine, escaped data root, unbounded response and secret redaction.
- [x] Execute `python -m unittest tests.test_rc6_rootless_metadata_readonly -v` and observe expected failures from absent implementation.
- [x] Implement only the validated private metadata probe with timeout, response bound, no dynamic Docker host and no mutations.
- [x] Repeat focused tests until all pass.

### Task 2: Existing Action opt-in mode

**Files:** Modify `.github/workflows/rc6-r1-docker-capacity-readonly.yml`; extend `tests/test_rc6_rootless_metadata_readonly.py`.

**Interfaces:** `workflow_dispatch` boolean `rootless_metadata_only=false`; default capacity/timer mode stays unchanged; true path executes only the private metadata probe via SSH stdin and parses its v1 receipt.

- [x] Write failing workflow routing test confirming default versus opt-in steps, unchanged mutex/SSH host key guard and no Docker-global execution in opt-in.
- [x] Execute tests and observe failure from absent mode.
- [x] Add conditional path and runner receipt validator, preserving existing contracts and runner-only credential cleanup.
- [x] Run focused tests, YAML parse, `git diff --check`, and a representative baseline test set. Report full-suite limitations explicitly.

### Task 3: Certification path and handoff

**Files:** No additional runtime mutation or GitHub writes in this workstream.

- [ ] Deliver local diff and exact test results to integration owner.
- [ ] Recommend first obtaining actual OS-account/service/socket/data-root evidence and then independent SQLite writer proof, not new signer/VFS mandates.
- [ ] Keep infrastructure certification `NO_VERIFICADO` until direct host and runtime evidence exists.
