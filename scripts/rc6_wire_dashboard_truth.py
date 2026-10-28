#!/usr/bin/env python3
"""Wire RC6 dashboard truth layer after HF6 universe extension."""
from pathlib import Path

PATH = Path("o_dashboard.py")
ANCHOR = (
    "import bh_universe_dashboard_hf6\n"
    "bh_universe_dashboard_hf6.install(app, _check_auth)\n"
)
INSERT = (
    ANCHOR
    + "\n# RC6 P0: one runtime truth layer for mode/session/policies across all dashboard pages.\n"
    + "import ep_dashboard_truth_layer_rc6\n"
    + "ep_dashboard_truth_layer_rc6.install(app, _check_auth)\n"
)
MARKER = "ep_dashboard_truth_layer_rc6.install(app, _check_auth)"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if text.count(MARKER) == 1:
        print("RC6_DASHBOARD_TRUTH_WIRING=ALREADY_PRESENT")
        return 0
    if text.count(MARKER) > 1:
        raise SystemExit("RC6_DASHBOARD_TRUTH_WIRING_DUPLICATED")
    if text.count(ANCHOR) != 1:
        raise SystemExit(f"RC6_DASHBOARD_TRUTH_ANCHOR_COUNT={text.count(ANCHOR)}")
    PATH.write_text(text.replace(ANCHOR, INSERT, 1), encoding="utf-8")
    print("RC6_DASHBOARD_TRUTH_WIRING=APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
