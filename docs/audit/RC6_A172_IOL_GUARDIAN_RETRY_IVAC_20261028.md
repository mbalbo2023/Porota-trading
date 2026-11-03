# RC6 A172 — OAuth guardian retry and provenance (28 Oct 2026)

## Observed failure

- Deploy V2 [#40771934853](https://github.com/mbalbo2023/Porota-trading/actions/runs/40771934853), candidate `4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6`, stopped at T_MINUS_45: the only RED check was `required_rc6_timers`. The preserved IOL OAuth timer was active/enabled, while its one-shot service was failed. PAPER/0 and NO_GO remained in force.
- Exact read-only [#40773641026](https://github.com/mbalbo2023/Porota-trading/actions/runs/40773641026) bound the failed service to `Result=exit-code`, `ExecMainStatus=2`, a 21:15 UTC invocation, and durable `REAUTH_REQUIRED` with `IOL_MCP_OAUTH_REFRESH_FAILED`. The old adapter discarded the HTTP status/body distinction. The original HTTP subtype is **not verified**; never infer `invalid_grant` from the former `REAUTH_REQUIRED` label.
- Controlled OAuth-only [#40774284582](https://github.com/mbalbo2023/Porota-trading/actions/runs/40774284582) authenticated, rotated access/refresh tokens and listed 36 tools at 21:43 UTC; before expiry was -2603.5 seconds, after was 899.9 seconds. Follow-up read-only [#40775048585](https://github.com/mbalbo2023/Porota-trading/actions/runs/40775048585) observed a successful guardian tick at 21:45 UTC. The failure was recoverable. None of these observations authorize PAPER GO or real orders.

## Causal guard

The guardian previously ran each 30 minutes while the observed access TTL was 15 minutes, so its next check occurred after access expiry. A single refresh failure returned exit 2 and mislabeled even transient HTTP/transport faults as `REAUTH_REQUIRED`. The new version keeps the preopen RED gate for a failed service. It records only safe failure class and numeric status, retries one bounded transient fault (408/425/429/5xx or transport), defers long Retry-After to the next timer, and uses `DEGRADED_RETRYABLE` with exit 2 and a distinct alert if the retry fails. HTTP 400/401/403 and missing authorization remain `REAUTH_REQUIRED`; unknown errors remain `DEGRADED_UNVERIFIED`, all exit 2. It emits an idempotent recovery alert only after a subsequent authenticated read-only MCP probe.

The timer interval is 10 minutes with a 420-second proactive skew. For the observed 900-second TTL, the next 600-second tick occurs with about 300 seconds left, inside the proactive window. Host policy retains exactly 16 RC6 timers and preserves only this guardian active during Deploy. The frozen host manifest records both modified systemd unit hashes; canonical `porota_apply_host_control_plane_v2.py` installs them and runs `daemon-reload` before preopen, without starting the other 15 timers or touching PPI Watch. Host application and a fresh full Predeploy remain mandatory for the next candidate.

## Regression evidence

Tests cover 503 recovery, persistent 503/429 and transport degradation, 400/401 rejection, unknown response, safe no-body diagnostics, deduplicated outbox alerts, previous incident recovery, exact systemd copy/order and the 16-timer carveout. The incident fixture uses the observed service state and TTL; the exact failed HTTP response remains unrecorded in historical code.
