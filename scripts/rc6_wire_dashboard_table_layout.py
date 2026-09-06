#!/usr/bin/env python3
from pathlib import Path

PATH = Path("o_dashboard.py")
ANCHOR = (
    "import ep_dashboard_truth_layer_rc6\n"
    "ep_dashboard_truth_layer_rc6.install(app, _check_auth)\n"
)
INSERT = (
    ANCHOR
    + "\n# RC6 P0: content-width aware tables for tablet/Voice Access.\n"
    + "import eq_dashboard_table_layout_rc6\n"
    + "eq_dashboard_table_layout_rc6.install()\n"
)
MARKER = "eq_dashboard_table_layout_rc6.install()"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if text.count(MARKER) == 1:
        print("RC6_TABLE_LAYOUT_WIRING=ALREADY_PRESENT")
        return 0
    if text.count(MARKER) > 1:
        raise SystemExit("RC6_TABLE_LAYOUT_WIRING_DUPLICATED")
    count = text.count(ANCHOR)
    if count != 1:
        raise SystemExit(f"RC6_TABLE_LAYOUT_ANCHOR_COUNT={count}")
    PATH.write_text(text.replace(ANCHOR, INSERT, 1), encoding="utf-8")
    print("RC6_TABLE_LAYOUT_WIRING=APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
