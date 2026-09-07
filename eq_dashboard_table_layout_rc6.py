"""RC6 classic table layout installer for Samsung/Voice Access.

Presentation only. The dashboard must preserve native row/column table semantics
on every viewport. Wide tables may scroll inside their own local container, but
must never be transformed into cards/stacked records.

No database, network, broker, strategy, gate or trading behavior.
"""
from __future__ import annotations

import bg_paper_dashboard as bg
import ev_dashboard_table_accessibility_rc6 as classic_tables

_installed = False

CLASSIC_TABLE_INVARIANT_CSS = r"""
<style id='porota-rc6-classic-table-invariant'>
/* Hard presentation invariant: a table remains a table at every viewport. */
table.paper-table{display:table!important;table-layout:auto!important}
table.paper-table thead{display:table-header-group!important}
table.paper-table tbody{display:table-row-group!important}
table.paper-table tr{display:table-row!important}
table.paper-table tr[hidden]{display:none!important}
table.paper-table th,table.paper-table td{display:table-cell!important}
/* Legacy per-cell labels are deliberately hidden if stale markup is present. */
.porota-cell-label{display:none!important}
</style>
"""


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    # Replace the previously imported responsive/card layer at runtime. This
    # avoids touching dashboard backend code while guaranteeing classic native
    # table semantics before any operator-facing page is rendered.
    bg.TABLE_A11Y_CSS = classic_tables.TABLE_A11Y_CSS + CLASSIC_TABLE_INVARIANT_CSS
    bg.TABLE_A11Y_SCRIPT = classic_tables.TABLE_A11Y_SCRIPT

    # Operator-facing layer for the 07-Sep PAPER go-live.
    import es_dashboard_go_live_ux_rc6 as go_live_ux
    go_live_ux.install()

    # Preserve the real component colour/state; classify PAPER blocking
    # independently so an informative YELLOW is not repainted as GRAY.
    import eu_dashboard_blocking_semantics_rc6 as blocking_semantics
    blocking_semantics.install()

    # Final observability truth correction: prefer current RC6 snapshots over
    # older/stale dashboard snapshots without touching producers, strategy or DBs.
    import et_dashboard_runtime_truth_fixes_rc6 as runtime_truth
    runtime_truth.install()

    # Latest operator-requested presentation/read-only changes. Install last so
    # they wrap the final truth layers rather than bypassing them.
    import ez_dashboard_operator_improvements_rc6 as operator_ux
    operator_ux.install()
