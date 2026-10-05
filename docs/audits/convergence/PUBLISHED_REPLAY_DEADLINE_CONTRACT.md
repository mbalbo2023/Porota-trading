# Published primary replay: total deadline and transport timeout

Status: SOURCE_IMPLEMENTED / NATIVE_FOCAL_PENDING. This finding is inferred from committed source, not an observed Docker failure, native acceleration or final artifact acceptance.

At source base `d9333065a306dc59bc5a58d7b5604d52daf9ff9f` / tree `316ad539f2690842e2fb1df4637811826897d6a0`, the published-image helper used `porota_artifact_http.remaining(deadline)` as the timeout of Docker load, inspect and import commands. That HTTP helper returns `min(30, deadline-clock())`. A canonical replay with600s left therefore received only30s for load/import. No Docker command or historical primary ZIP was run to diagnose this coupling.

The published-image helper now computes the actual remaining total budget with a dedicated `replay_remaining(deadline, clock=...)`. It checks the same deadline before and after commands and after codec report validation; it never renews the deadline. Docker load, inspect and imports receive the remaining total budget. The codec receives `min(remaining_total, codec_smoke.MAX_SECONDS)` and retains its30s/64KiB limits. The HTTP implementation and its30s request cap are byte-identical to the baseline.

The canonical workflow still passes `--deadline-seconds600`, and the SIGALRM total guard remains. This patch changes neither that600s budget nor the existing CLI range60..900. A command finishing at or after the total deadline is rejected even if its controlled subprocess result says exit0. An exhausted/nonfinite total budget cannot start Docker.

Both import and codec containers receive private random names. A failed/timed-out run removes only its own name, with the existing bounded5s failure teardown. That teardown can complete after exhaustion; it never grants additional time for GREEN. There is no foreign inspect/prune, rebuild, promotion or host action. Load/inspect create no replay container and expiry there prevents later image execution. The actual loaded ImageID must still equal the frozen expected ID before imports/codec; all primary source, manifest and tiny-report bindings remain in force.

The permanent guards invoke the real published-image protocol over existing explicit synthetic Git/Docker-save metadata fixtures, with an injected monotonic clock and subprocess adapter. They execute no Docker daemon, remote API,600s sleep, installed-package mutation, trading data or runtime. A successful unit guard is not an actual ImageID observation or inside-image smoke receipt.

The six new native test declarations cover load/import remaining budgets plus unchanged HTTP/codec30s, prelaunch expiry/nonfinite time, a completed load after total exhaustion, codec min(total,30) and exact owned cleanup, import timeout cleanup without starting codec, and failed/boolean cleanup responses that cannot certify container termination. Parametrized controls share their function; they do not add independent original requirement/market attacks.

Proposed focal, pending ROOT CPU slot:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 /workspace/venv_rc6_frozen311/bin/python -I -B -m pytest tests/test_rc6_convergence_sre_published_evidence.py -p no:cacheprovider -o pythonpath=. -o junit_family=legacy --junitxml=/tmp/rc6-sre-replay-total-deadline-focal.xml --basetemp=/workspace/rc6-sre-replay-total-deadline-pytest
```

Run with umask022 in the isolated owner worktree and fresh external output paths. Preserve raw XML/log/command and the actual source SHA/tree; no guard is reported executed until that run succeeds. The prior source-preparation packet and historical denied artifacts remain evidence of their own scope and are not rewritten by this fix.
