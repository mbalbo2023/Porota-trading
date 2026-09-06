"""RC6 table layout fix for Samsung/Voice Access.

RC5 switched tables to cards only from viewport width <=980px.  That is not
sufficient when a wide Android/desktop-mode viewport contains a narrower content
column (for example Sistema has a side navigation).  This extension also
compacts any table whose own usable width per column is too small or whose
content overflows its card.

Presentation only.  No database/network/trading behavior.
"""
from __future__ import annotations

import bg_paper_dashboard as bg
from er_dashboard_table_semantics_rc6 import should_force_compact

_installed = False

FORCE_COMPACT_CSS = r"""
<style id='porota-rc6-force-compact-tables'>
/* Content-width driven fallback, independent from viewport media queries. */
table.paper-table[data-porota-force-compact='1']{display:block;width:100%;max-width:100%;border-collapse:separate;table-layout:auto;font-size:.9rem;overflow:visible!important}
table.paper-table[data-porota-force-compact='1'] tbody{display:block;width:100%}
table.paper-table[data-porota-force-compact='1'] tr.porota-table-header{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
table.paper-table[data-porota-force-compact='1'] tr.porota-record-row{display:block;width:100%;max-width:100%;margin:0 0 10px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:6px 10px;overflow:hidden}
table.paper-table[data-porota-force-compact='1'] tr.porota-record-row[hidden]{display:none!important}
table.paper-table[data-porota-force-compact='1'] td{display:grid;grid-template-columns:minmax(145px,34%) minmax(0,66%);gap:10px;width:100%;max-width:100%;padding:8px 2px;border-bottom:1px solid var(--line);overflow-wrap:anywhere;word-break:break-word;white-space:normal}
table.paper-table[data-porota-force-compact='1'] td:last-child{border-bottom:0}
table.paper-table[data-porota-force-compact='1'] td[colspan]{display:block}
table.paper-table[data-porota-force-compact='1'] .porota-cell-label{display:block;color:var(--muted);font-weight:700;min-width:0}
table.paper-table[data-porota-force-compact='1'] td[colspan]>.porota-cell-label{display:none}
@media(max-width:560px){table.paper-table[data-porota-force-compact='1'] td{display:block}.porota-cell-label{margin-bottom:3px}}
</style>
"""

FORCE_COMPACT_SCRIPT = r"""
<script id='porota-rc6-force-compact-script'>
(function(){
  let scheduled=false;
  function measure(){
    document.querySelectorAll('main.paper-page table.paper-table, #porota-legacy-shell table.paper-table').forEach(table=>{
      const header=Array.from(table.rows||[]).find(row=>Array.from(row.cells||[]).some(cell=>cell.tagName==='TH'));
      const columns=header ? Array.from(header.cells||[]).length : 0;
      if(columns<=1){table.dataset.porotaForceCompact='0';return;}
      const card=table.closest('.paper-card,.system-content,main.paper-page') || table.parentElement;
      const available=Math.max(1, card?.clientWidth || table.clientWidth || 1);
      const perColumn=available/columns;
      /* Keep this JS predicate synchronized with er_dashboard_table_semantics_rc6.py. */
      const force=(columns>=7 || perColumn<128 || table.scrollWidth>available+4);
      table.dataset.porotaForceCompact=force?'1':'0';
    });
  }
  function schedule(){
    if(scheduled)return;
    scheduled=true;
    requestAnimationFrame(()=>{scheduled=false;measure();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',schedule,{once:true});
  else schedule();
  window.addEventListener('resize',schedule,{passive:true});
  new MutationObserver(schedule).observe(document.documentElement,{childList:true,subtree:true});
  window.porotaMeasureCompactTables=measure;
})();
</script>
"""


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    if "porota-rc6-force-compact-tables" not in bg.TABLE_A11Y_CSS:
        bg.TABLE_A11Y_CSS += FORCE_COMPACT_CSS
    if "porota-rc6-force-compact-script" not in bg.TABLE_A11Y_SCRIPT:
        bg.TABLE_A11Y_SCRIPT += FORCE_COMPACT_SCRIPT
