#!/usr/bin/env python3
"""Wire RC6 validation dashboard into o_dashboard.py deterministically.

This helper is build-time/repository tooling only.  It fails closed if the
expected RC5/RC6 extension anchor is missing or duplicated.
"""
from pathlib import Path

PATH = Path("o_dashboard.py")
ANCHOR = (
    "import bh_universe_dashboard_hf6\n"
    "bh_universe_dashboard_hf6.install(app, _check_auth)\n"
)
INSERT = (
    ANCHOR
    + "\n# RC6: project-management path-to-production view. Read-only HTTP surface.\n"
    + "import en_validation_project_dashboard_rc6\n"
    + "en_validation_project_dashboard_rc6.install(app, _check_auth)\n"
)
MARKER = "en_validation_project_dashboard_rc6.install(app, _check_auth)"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if text.count(MARKER) == 1:
        print("RC6_VALIDATION_WIRING=ALREADY_PRESENT")
        return 0
    if text.count(MARKER) > 1:
        raise SystemExit("RC6_VALIDATION_WIRING_DUPLICATED")
    count = text.count(ANCHOR)
    if count != 1:
        raise SystemExit(f"RC6_VALIDATION_WIRING_ANCHOR_COUNT={count}")
    PATH.write_text(text.replace(ANCHOR, INSERT, 1), encoding="utf-8")
    print("RC6_VALIDATION_WIRING=APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
