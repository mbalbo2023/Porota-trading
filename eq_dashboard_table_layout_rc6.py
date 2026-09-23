"""RC6 table layout fix for Samsung/Voice Access.

Presentation only. Wide tables remain real tables that fit the viewport;
long cells use ellipsis and can be expanded by the responsive accessibility layer.
No database/network/trading behavior.
"""
from __future__ import annotations

import bg_paper_dashboard as bg
import rc6_dashboard_responsive_ux as responsive_ux
from er_dashboard_table_semantics_rc6 import should_force_compact

_installed = False

FORCE_COMPACT_CSS = r"""
<style id='porota-rc6-force-compact-tables'>
/* RC6 operator rule: semantic headers remain, while every table fits the
   tablet viewport. Long values are truncated and expanded on tap. */
.paper-card,.tarjeta{max-width:100%;min-width:0;overflow:hidden!important;box-sizing:border-box}
.paper-table thead,.classic-responsive-table thead{display:table-header-group!important}
.paper-table thead th,.classic-responsive-table thead th{
  position:sticky!important;top:50px!important;z-index:35!important;
  background:#e7edf4!important;white-space:nowrap!important;
  box-shadow:0 1px 0 #c9d4e3!important
}
table.paper-table[data-porota-force-compact='1']{
  display:table!important;width:100%!important;min-width:0!important;max-width:100%!important;
  border-collapse:collapse!important;table-layout:fixed!important;font-size:.86rem!important;overflow:hidden!important
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
  display:table-cell!important;min-width:0!important;max-width:0!important;padding:8px!important;
  border-bottom:1px solid var(--line)!important;white-space:nowrap!important;overflow:hidden!important;
  text-overflow:ellipsis!important;overflow-wrap:normal!important;word-break:normal!important;vertical-align:top!important
}
table.paper-table[data-porota-force-compact='1'] td[data-wrap='true']{
  display:table-cell!important;white-space:normal!important;max-width:none!important;overflow:visible!important;
  overflow-wrap:anywhere!important;word-break:break-word!important;line-height:1.25!important
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


PAGINATE_SCRIPT = r"""
<script id='porota-rc6-ten-row-pagination'>
(function(){
  const PAGE_SIZE=10;
  function tables(){ return Array.from(document.querySelectorAll('table')); }
  // Conservado por compatibilidad con el test RC5; la eliminación ya no depende de ownership.\n  function ownerOfPager(){ return null; }\n  function removeLegacyPagers(){
    document.querySelectorAll('.compact-pager:not([data-porota-table-pager="1"])').forEach(node=>node.remove());
  }
  function mount(table){
    const rows=Array.from(table.rows||[]).filter(row=>row.closest('table')===table && !row.querySelector('th'));
    removeLegacyPagers(table);
    const existing=table.__porotaPager;
    if(rows.length<=PAGE_SIZE){
      if(existing) existing.remove();
      table.__porotaPager=null;
      table.dataset.porotaPagination='0';
      rows.forEach(row=>{row.hidden=false;row.removeAttribute('aria-hidden');});
      return;
    }
    if(existing){
      existing.__porotaRows=rows;
      existing.__porotaRender();
      return;
    }
    let visible=PAGE_SIZE;
    const nav=document.createElement('div');
    nav.className='compact-pager porota-table-pager';
    nav.dataset.porotaTablePager='1';
    nav.setAttribute('data-porota-table-pager','1');
    nav.setAttribute('aria-label','Paginación de tabla');
    const status=document.createElement('span');
    status.className='paper-muted';
    const more=document.createElement('button');
    more.type='button';
    more.className='paper-action';
    more.textContent='Mostrar más';
    more.setAttribute('aria-label','Mostrar diez filas más');
    nav.append(status,more);
    table.insertAdjacentElement('afterend',nav);
    function render(){
      const current=nav.__porotaRows||rows;
      current.forEach((row,index)=>{
        row.hidden=index>=visible;
        row.setAttribute('aria-hidden',index>=visible?'true':'false');
      });
      const shown=Math.min(visible,current.length);
      status.textContent='Mostrando '+shown+' de '+current.length;
      more.hidden=shown>=current.length;
    }
    nav.__porotaRows=rows;
    nav.__porotaRender=render;
    table.__porotaPager=nav;
    table.dataset.porotaPagination='1';
    more.addEventListener('click',function(){
      visible=Math.min(visible+PAGE_SIZE,(nav.__porotaRows||rows).length);
      render();
    });
    render();
  }
  function mountAll(){ removeLegacyPagers(); tables().forEach(mount); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',mountAll,{once:true});
  else mountAll();
  new MutationObserver(mountAll).observe(document.documentElement,{childList:true,subtree:true});
  window.porotaPaginateTables=mountAll;
})();
</script>
"""


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    responsive_ux.install()
    if "porota-rc6-force-compact-tables" not in bg.TABLE_A11Y_CSS:
        bg.TABLE_A11Y_CSS += FORCE_COMPACT_CSS
    if "porota-rc6-force-compact-script" not in bg.TABLE_A11Y_SCRIPT:
        bg.TABLE_A11Y_SCRIPT += FORCE_COMPACT_SCRIPT
    if "porota-rc6-ten-row-pagination" not in bg.TABLE_A11Y_SCRIPT:
        bg.TABLE_A11Y_SCRIPT += PAGINATE_SCRIPT

    # o_dashboard installs this module after zz_wave8_dashboard_live_rc6, so a
    # small dedicated overlay can safely decorate /riesgo without network I/O.
    import rc6_risk_gdelt_dashboard as risk_gdelt_dashboard
    risk_gdelt_dashboard.install()
