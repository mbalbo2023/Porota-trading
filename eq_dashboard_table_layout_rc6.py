"""RC6 table layout fix for Samsung/Voice Access.

Presentation only.  Wide tables remain real tables with horizontal scrolling;
column headers stay visible instead of being hidden by the former compact-card
fallback.  No database/network/trading behavior.
"""
from __future__ import annotations

import bg_paper_dashboard as bg
from er_dashboard_table_semantics_rc6 import should_force_compact

_installed = False

FORCE_COMPACT_CSS = r"""
<style id='porota-rc6-force-compact-tables'>
/* RC6 operator rule: a table must never lose its semantic column headers.
   Overflow is handled horizontally; it is not converted into anonymous cards. */
.paper-card,.tarjeta{max-width:100%;overflow-x:auto!important;overflow-y:visible!important;-webkit-overflow-scrolling:touch}
.paper-table thead,.classic-responsive-table thead{display:table-header-group!important}
.paper-table thead th,.classic-responsive-table thead th{
  position:sticky!important;top:50px!important;z-index:35!important;
  background:#e7edf4!important;white-space:nowrap!important;
  box-shadow:0 1px 0 #c9d4e3!important
}
table.paper-table[data-porota-force-compact='1']{
  display:table!important;width:max-content!important;min-width:100%!important;max-width:none!important;
  border-collapse:collapse!important;table-layout:auto!important;font-size:.86rem!important;overflow:visible!important
}
table.paper-table[data-porota-force-compact='1'] thead{display:table-header-group!important}
table.paper-table[data-porota-force-compact='1'] tbody{display:table-row-group!important}
table.paper-table[data-porota-force-compact='1'] tr.porota-table-header{
  position:static!important;width:auto!important;height:auto!important;padding:0!important;margin:0!important;
  overflow:visible!important;clip:auto!important;clip-path:none!important;white-space:normal!important;
  border:0!important;display:table-row!important
}
table.paper-table[data-porota-force-compact='1'] tr.porota-table-header th{
  display:table-cell!important;visibility:visible!important;white-space:nowrap!important
}
table.paper-table[data-porota-force-compact='1'] tr.porota-record-row{
  display:table-row!important;width:auto!important;max-width:none!important;margin:0!important;border:0!important;
  border-radius:0!important;background:transparent!important;padding:0!important;overflow:visible!important
}
table.paper-table[data-porota-force-compact='1'] tr.porota-record-row[hidden]{display:none!important}
table.paper-table[data-porota-force-compact='1'] td{
  display:table-cell!important;width:auto!important;max-width:none!important;padding:8px!important;
  border-bottom:1px solid var(--line)!important;white-space:nowrap!important;overflow-wrap:normal!important;
  word-break:keep-all!important;vertical-align:top!important
}
table.paper-table[data-porota-force-compact='1'] td[data-wrap='true']{
  white-space:normal!important;min-width:16rem!important;max-width:32rem!important;
  overflow-wrap:break-word!important;word-break:normal!important
}
table.paper-table[data-porota-force-compact='1'] .porota-cell-label{display:none!important}
@media(max-width:700px){
  .paper-table thead th,.classic-responsive-table thead th{top:0!important}
  table.paper-table[data-porota-force-compact='1']{font-size:.78rem!important}
  table.paper-table[data-porota-force-compact='1'] td{padding:6px!important}
}
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
      /* Attribute now marks a wide/overflow table; CSS preserves table semantics. */
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

    # o_dashboard installs this module after zz_wave8_dashboard_live_rc6, so a
    # small dedicated overlay can safely decorate /riesgo without network I/O.
    import rc6_risk_gdelt_dashboard as risk_gdelt_dashboard
    risk_gdelt_dashboard.install()
