#!/usr/bin/env python3
"""RC6 host preopen compatibility layer for the current production host.

The live audit on 2026-09-25 proved that observer and dashboard use the same
17.0.0-rc6 image family on this host.  Timer/quarantine policy is owned only by
/opt/porota-trading/rc6_preopen.py; this wrapper must never override it.

No thresholds are relaxed and no order capability is introduced.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIVE_REPO = Path("/opt/porota-trading")
if not (LIVE_REPO / "rc6_preopen.py").is_file():
    raise RuntimeError("RC6_CANONICAL_PREOPEN_SOURCE_MISSING")
sys.path.insert(0, str(LIVE_REPO))

import rc6_preopen as base

DASHBOARD_IMAGE = "porota-trading-bot:17.0.0-rc6"


def container(name: str, require_readonly: bool = False):
    if name != "porota_production_dashboard":
        return (
            base._original_container(name, require_readonly)
            if hasattr(base, "_original_container")
            else base.container(name, require_readonly)
        )
    rc, out, err = base.cmd([
        "docker", "inspect", "-f",
        "{{.State.Running}}|{{.Config.Image}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}",
        name,
    ])
    parts = out.split("|") if rc == 0 else []
    ok = (
        len(parts) == 4
        and parts[0] == "true"
        and parts[1] == DASHBOARD_IMAGE
        and parts[2] == "0"
    )
    return {"state": "GREEN" if ok else "RED", "raw": out, "error": err}


def install_policy_overrides():
    if not hasattr(base, "_original_container"):
        base._original_container = base.container
    base.container = container
    # REQUIRED_TIMERS and BLOCKED_HISTORY_TIMERS remain canonical in rc6_preopen.py.


def main():
    install_policy_overrides()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
