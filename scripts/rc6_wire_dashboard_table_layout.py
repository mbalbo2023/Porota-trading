#!/usr/bin/env python3
from pathlib import Path

PATH = Path("o_dashboard.py")
TRUTH_ANCHOR = (
    "import ep_dashboard_truth_layer_rc6\n"
    "ep_dashboard_truth_layer_rc6.install(app, _check_auth)\n"
)
UNIVERSE_ANCHOR = (
    "import bh_universe_dashboard_hf6\n"
    "bh_universe_dashboard_hf6.install(app, _check_auth)\n"
)
BLOCK = (
    "\n# RC6 P0: content-width aware tables for tablet/Voice Access.\n"
    "import eq_dashboard_table_layout_rc6\n"
    "eq_dashboard_table_layout_rc6.install()\n"
)
MARKER = "eq_dashboard_table_layout_rc6.install()"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if text.count(MARKER) == 1:
        print("RC6_TABLE_LAYOUT_WIRING=ALREADY_PRESENT")
        return 0
    if text.count(MARKER) > 1:
        raise SystemExit("RC6_TABLE_LAYOUT_WIRING_DUPLICATED")

    if text.count(TRUTH_ANCHOR) == 1:
        anchor = TRUTH_ANCHOR
        source = "TRUTH_LAYER"
    elif text.count(UNIVERSE_ANCHOR) == 1:
        anchor = UNIVERSE_ANCHOR
        source = "UNIVERSE_FALLBACK"
    else:
        raise SystemExit(
            "RC6_TABLE_LAYOUT_ANCHOR_NOT_UNIQUE:"
            f"truth={text.count(TRUTH_ANCHOR)}:universe={text.count(UNIVERSE_ANCHOR)}"
        )

    PATH.write_text(text.replace(anchor, anchor + BLOCK, 1), encoding="utf-8")
    print(f"RC6_TABLE_LAYOUT_WIRING=APPLIED_AFTER_{source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
