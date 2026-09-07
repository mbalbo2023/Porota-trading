"""Dedicated Docker health probe for the RC6 critical approval gateway.

The gateway is not an HTTP server, so it must not inherit the generic bot
/dashboard healthcheck.  This probe verifies the actual control-plane
capability path: the container can authenticate to and reach the local Unix
GitHub Issues broker, and that broker reports its intentionally limited
issues-only capability.
"""
from __future__ import annotations

from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient


def main() -> int:
    try:
        health = UnixGithubIssuesClient().health()
    except Exception:
        return 1
    if health.get("status") != "ok":
        return 1
    if health.get("capability") != "issues_only":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
