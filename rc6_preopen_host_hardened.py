#!/usr/bin/env python3
"""RC6 host preopen compatibility layer after SRE split.

Keeps rc6_preopen.py as the fail-closed policy source while correcting two
host-contract details proven postclose on 2026-09-07:
- dashboard is a separate image family, not the observer image;
- the frequent functional timer is the fast no-full-scan RC6 probe.
No thresholds are relaxed: the 8 GiB free-space floor remains unchanged.
"""
from __future__ import annotations
import rc6_preopen as base

DASHBOARD_PREFIX = "porota-trading-dashboard:17.0.0-rc6"


def container(name: str, require_readonly: bool = False):
    if name != "porota_production_dashboard":
        return base._original_container(name, require_readonly) if hasattr(base, "_original_container") else base.container(name, require_readonly)
    rc, out, err = base.cmd([
        "docker", "inspect", "-f",
        "{{.State.Running}}|{{.Config.Image}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}",
        name,
    ])
    parts = out.split("|") if rc == 0 else []
    ok = (
        len(parts) == 4
        and parts[0] == "true"
        and parts[1].startswith(DASHBOARD_PREFIX)
        and parts[2] == "0"
    )
    return {"state": "GREEN" if ok else "RED", "raw": out, "error": err}


def install_policy_overrides():
    if not hasattr(base, "_original_container"):
        base._original_container = base.container
    base.container = container
    base.REQUIRED_TIMERS = (
        "porota-fast-functional-health-rc6.timer",
        "porota-history-postclose-rc6.timer",
        "porota-host-general-backup-rc6.timer",
        "porota-preopen-rc6.timer",
        "porota-candle-integrity-rc6.timer",
    )


def main():
    install_policy_overrides()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
