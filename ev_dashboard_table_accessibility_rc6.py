"""RC6 classic semantic table accessibility layer.

Presentation only. Keeps native HTML table semantics on every viewport and
contains horizontal overflow locally when a table is wider than its card.
No database, broker, network, strategy or trading behavior.
"""
from __future__ import annotations

TABLE_PAGE_SIZE = 20

TABLE_A11Y_CSS = r"""
<style id='porota-rc6-table-a11y-classic'>
.paper-page{min-width:0;max-width:100%;overflow-x:hidden}
.paper-card{max-width:100%;min-width:0}
.porota-table-scroll{width:100%;max-width:100%;overflow-x:auto;overflow-y:visible;-webkit-overflow-scrolling:touch;overscroll-behavior-x:contain;border-radius:8px}
.porota-table-scroll:focus{outline:3px solid color-mix(in srgb,var(--blue) 45%,transparent);outline-offset:2px}
table.paper-table{display:table!important;width:100%;max-width:none;table-layout:auto!important;border-collapse:collapse!important}
table.paper-table thead{display:table-header-group!important}
table.paper-table tbody{display:table-row-group!important}
table.paper-table tr{display:table-row!important}
table.paper-table tr[hidden]{display:none!important}
table.paper-table th,table.paper-table td{display:table-cell!important;vertical-align:top;overflow-wrap:anywhere;word-break:normal;white-space:normal}
table.paper-table th{white-space:nowrap}
.porota-progressive-controls{display:flex;gap:9px;align-items:center;justify-content:flex-start;flex-wrap:wrap;margin:12px 0 2px}
.porota-progressive-count{color:var(--muted);font-size:.88rem;font-weight:650}
.porota-table-more,.porota-table-less{background:var(--blue);color:#fff;border:0;border-radius:8px;padding:9px 13px;font:700 .9rem system-ui;cursor:pointer}
.porota-table-less{background:var(--gray)}
@media(max-width:980px){
  table.paper-table{font-size:.82rem}
  table.paper-table th,table.paper-table td{padding:7px!important}
}
@media(max-width:700px){
  table.paper-table{font-size:.78rem}
  table.paper-table th,table.paper-table td{padding:6px!important}
}
</style>
"""

TABLE_A11Y_SCRIPT = r"""
<script id='porota-rc6-table-a11y-classic-script'>
(function(){
  const PAGE_SIZE=20;
  let scheduled=false;
  let observer=null;

  function safeKey(text){
    return String(text||'').replace(/\s+/g,' ').trim().slice(0,100);
  }

  function tableRows(table){
    return Array.from(table.rows||[]).filter(row=>Array.from(row.cells||[]).some(cell=>cell.tagName==='TD'));
  }

  function headerCells(table){
    const headerRow=Array.from(table.rows||[]).find(row=>Array.from(row.cells||[]).some(cell=>cell.tagName==='TH'));
    if(!headerRow) return [];
    return Array.from(headerRow.cells||[]).filter(cell=>cell.tagName==='TH');
  }

  function accessibleName(table,index){
    const card=table.closest('.paper-card');
    const heading=card?.querySelector('h1,h2,h3')?.textContent || table.getAttribute('aria-label') || ('Tabla '+(index+1));
    return safeKey(heading) || ('Tabla '+(index+1));
  }

  function ensureSemantics(table,index){
    table.classList.add('paper-table');
    const name=accessibleName(table,index);
    if(!table.getAttribute('aria-label')) table.setAttribute('aria-label',name);
    const headers=headerCells(table);
    headers.forEach(cell=>{
      if(!cell.hasAttribute('scope')) cell.setAttribute('scope','col');
    });
    const columns=headers.length;
    if(columns>=6){
      const width=Math.min(1320,Math.max(700,columns*116));
      table.style.minWidth=width+'px';
    }else{
      table.style.minWidth='100%';
    }
    return {name,columns};
  }

  function ensureScrollRegion(table,index,name){
    let wrapper=table.parentElement?.classList.contains('porota-table-scroll') ? table.parentElement : null;
    if(!wrapper){
      wrapper=document.createElement('div');
      wrapper.className='porota-table-scroll';
      table.parentNode.insertBefore(wrapper,table);
      wrapper.appendChild(table);
    }
    wrapper.setAttribute('role','region');
    wrapper.setAttribute('aria-label','Tabla: '+name);
    wrapper.setAttribute('tabindex','0');
    wrapper.dataset.porotaTableIndex=String(index);
    return wrapper;
  }

  function storageKey(table,index){
    return 'porota-table-rc6:'+location.pathname+location.search+':'+index+':'+safeKey(table.getAttribute('aria-label'));
  }

  function progressive(table,index,anchor){
    const rows=tableRows(table);
    const total=rows.length;
    const old=anchor.nextElementSibling?.classList.contains('porota-progressive-controls') ? anchor.nextElementSibling : null;
    if(total<=PAGE_SIZE){
      rows.forEach(row=>row.hidden=false);
      if(old) old.remove();
      return;
    }

    const key=storageKey(table,index);
    let shown=Number.parseInt(sessionStorage.getItem(key)||String(PAGE_SIZE),10);
    if(!Number.isFinite(shown)) shown=PAGE_SIZE;
    shown=Math.max(PAGE_SIZE,Math.min(total,shown));

    let controls=old;
    if(!controls){
      controls=document.createElement('div');
      controls.className='porota-progressive-controls';
      controls.setAttribute('role','group');
      controls.setAttribute('aria-label','Controles de registros');
      anchor.insertAdjacentElement('afterend',controls);
    }

    const tableId=table.id||('porota-table-'+index);
    table.id=tableId;
    controls.innerHTML='';

    const count=document.createElement('span');
    count.className='porota-progressive-count';
    controls.appendChild(count);

    const more=document.createElement('button');
    more.type='button';
    more.className='porota-table-more';
    more.id=tableId+'-mostrar-mas';
    more.setAttribute('aria-controls',tableId);
    controls.appendChild(more);

    const less=document.createElement('button');
    less.type='button';
    less.className='porota-table-less';
    less.id=tableId+'-mostrar-menos';
    less.setAttribute('aria-controls',tableId);
    less.textContent='Mostrar menos';
    controls.appendChild(less);

    function paint(focusTarget){
      rows.forEach((row,rowIndex)=>row.hidden=rowIndex>=shown);
      count.textContent='Mostrando '+shown+' de '+total;
      const remaining=Math.max(0,total-shown);
      more.hidden=remaining===0;
      more.textContent=remaining ? 'Mostrar '+Math.min(PAGE_SIZE,remaining)+' más' : 'Todos los registros visibles';
      less.hidden=shown<=PAGE_SIZE;
      sessionStorage.setItem(key,String(shown));
      if(focusTarget==='more' && !more.hidden) more.focus({preventScroll:true});
      if(focusTarget==='less' && !less.hidden) less.focus({preventScroll:true});
    }

    more.onclick=function(){shown=Math.min(total,shown+PAGE_SIZE);paint(shown<total?'more':'less');};
    less.onclick=function(){shown=PAGE_SIZE;paint('more');};
    paint(null);
  }

  function startObserver(){
    if(observer) observer.observe(document.documentElement,{childList:true,subtree:true});
  }

  function init(){
    if(observer) observer.disconnect();
    try{
      const tables=Array.from(document.querySelectorAll('main.paper-page table, #porota-legacy-shell table'));
      tables.forEach((table,index)=>{
        const meta=ensureSemantics(table,index);
        const wrapper=ensureScrollRegion(table,index,meta.name);
        progressive(table,index,wrapper);
      });
    } finally {
      startObserver();
    }
  }

  function schedule(){
    if(scheduled) return;
    scheduled=true;
    requestAnimationFrame(()=>{scheduled=false;init();});
  }

  observer=new MutationObserver(schedule);
  if(document.readyState==='loading'){
    startObserver();
    document.addEventListener('DOMContentLoaded',init,{once:true});
  }else{
    init();
  }
  window.porotaInitClassicTables=init;
})();
</script>
"""


def assert_table_accessibility_contract() -> None:
    assert TABLE_PAGE_SIZE == 20
    assert "porota-table-scroll" in TABLE_A11Y_CSS
    assert "display:table!important" in TABLE_A11Y_CSS
    assert "display:table-row!important" in TABLE_A11Y_CSS
    assert "display:table-cell!important" in TABLE_A11Y_CSS
    assert "data-porota-compact" not in TABLE_A11Y_CSS
    assert "porota-cell-label" not in TABLE_A11Y_CSS
    assert "scope','col'" in TABLE_A11Y_SCRIPT
    assert "role','region'" in TABLE_A11Y_SCRIPT
    assert "Mostrar '+Math.min(PAGE_SIZE,remaining)+' más" in TABLE_A11Y_SCRIPT
    assert "Mostrar menos" in TABLE_A11Y_SCRIPT
    assert "Mostrando '+shown+' de '+total" in TABLE_A11Y_SCRIPT
    assert "sessionStorage" in TABLE_A11Y_SCRIPT
    assert "MutationObserver" in TABLE_A11Y_SCRIPT
    assert "porotaInitClassicTables" in TABLE_A11Y_SCRIPT
