"""RC5: comportamiento transversal de tablas para tablet y Voice Access.

Presentación únicamente. No consulta PPI, no modifica SQLite y no participa de
ningún portón de trading. Todas las tablas del dashboard se convierten a tarjetas
verticales en anchos de tablet y limitan visualmente registros en bloques de 20.
"""

TABLE_PAGE_SIZE = 20

TABLE_A11Y_CSS = r"""
<style id='porota-rc5-table-a11y'>
.paper-page{min-width:0;max-width:100%;overflow-x:hidden}
.paper-card{max-width:100%;min-width:0}
.porota-progressive-controls{display:flex;gap:9px;align-items:center;justify-content:flex-start;flex-wrap:wrap;margin:12px 0 2px}
.porota-progressive-count{color:var(--muted);font-size:.88rem;font-weight:650}
.porota-table-more,.porota-table-less{background:var(--blue);color:#fff;border:0;border-radius:8px;padding:9px 13px;font:700 .9rem system-ui;cursor:pointer}
.porota-table-less{background:var(--gray)}
.porota-cell-label{display:none}

@media(max-width:980px){
  table.paper-table[data-porota-compact='1']{display:block;width:100%;max-width:100%;border-collapse:separate;table-layout:auto;font-size:.9rem}
  table.paper-table[data-porota-compact='1'] tbody{display:block;width:100%}
  table.paper-table[data-porota-compact='1'] tr.porota-table-header{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
  table.paper-table[data-porota-compact='1'] tr.porota-record-row{display:block;width:100%;max-width:100%;margin:0 0 10px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:6px 10px;overflow:hidden}
  table.paper-table[data-porota-compact='1'] tr.porota-record-row[hidden]{display:none!important}
  table.paper-table[data-porota-compact='1'] td{display:grid;grid-template-columns:minmax(110px,38%) minmax(0,62%);gap:10px;width:100%;max-width:100%;padding:8px 2px;border-bottom:1px solid var(--line);overflow-wrap:anywhere;word-break:break-word;white-space:normal}
  table.paper-table[data-porota-compact='1'] td:last-child{border-bottom:0}
  table.paper-table[data-porota-compact='1'] td[colspan]{display:block}
  .porota-cell-label{display:block;color:var(--muted);font-weight:700;min-width:0}
  table.paper-table[data-porota-compact='1'] td[colspan]>.porota-cell-label{display:none}
}

@media(max-width:560px){
  table.paper-table[data-porota-compact='1'] td{display:block}
  .porota-cell-label{margin-bottom:3px}
}
</style>
"""

TABLE_A11Y_SCRIPT = r"""
<script id='porota-rc5-table-a11y-script'>
(function(){
  const PAGE_SIZE=20;
  let scheduled=false;

  function safeKey(text){
    return String(text||'').replace(/\s+/g,' ').trim().slice(0,80);
  }

  function storageKey(table,index){
    const card=table.closest('.paper-card');
    const heading=card?.querySelector('h2,h3')?.textContent || table.getAttribute('aria-label') || '';
    return 'porota-table-v1:'+location.pathname+location.search+':'+index+':'+safeKey(heading);
  }

  function tableRows(table){
    return Array.from(table.rows||[]).filter(row=>Array.from(row.cells||[]).some(cell=>cell.tagName==='TD'));
  }

  function headersFor(table){
    const headerRow=Array.from(table.rows||[]).find(row=>Array.from(row.cells||[]).some(cell=>cell.tagName==='TH'));
    if(!headerRow) return [];
    headerRow.classList.add('porota-table-header');
    return Array.from(headerRow.cells).map(cell=>safeKey(cell.textContent)||'Campo');
  }

  function labelCells(table,headers){
    tableRows(table).forEach(row=>{
      row.classList.add('porota-record-row');
      Array.from(row.cells||[]).forEach((cell,index)=>{
        if(cell.colSpan>1) return;
        const label=headers[index]||('Campo '+(index+1));
        cell.dataset.label=label;
        if(!cell.querySelector(':scope > .porota-cell-label')){
          const node=document.createElement('span');
          node.className='porota-cell-label';
          node.textContent=label;
          cell.prepend(node);
        }
      });
    });
  }

  function progressive(table,index){
    const rows=tableRows(table);
    const total=rows.length;
    const old=table.nextElementSibling?.classList.contains('porota-progressive-controls') ? table.nextElementSibling : null;
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
      table.insertAdjacentElement('afterend',controls);
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

  function init(){
    const tables=Array.from(document.querySelectorAll('main.paper-page table, #porota-legacy-shell table'));
    tables.forEach((table,index)=>{
      table.classList.add('paper-table');
      table.dataset.porotaCompact='1';
      const headers=headersFor(table);
      labelCells(table,headers);
      progressive(table,index);
    });
  }

  function schedule(){
    if(scheduled) return;
    scheduled=true;
    requestAnimationFrame(()=>{scheduled=false;init();});
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
  new MutationObserver(schedule).observe(document.documentElement,{childList:true,subtree:true});
  window.porotaInitCompactTables=init;
})();
</script>
"""


def assert_table_accessibility_contract():
    assert TABLE_PAGE_SIZE == 20
    assert "max-width:980px" in TABLE_A11Y_CSS
    assert "porota-cell-label" in TABLE_A11Y_CSS
    assert "Mostrar '+Math.min(PAGE_SIZE,remaining)+' más" in TABLE_A11Y_SCRIPT
    assert "Mostrar menos" in TABLE_A11Y_SCRIPT
    assert "Mostrando '+shown+' de '+total" in TABLE_A11Y_SCRIPT
    assert "sessionStorage" in TABLE_A11Y_SCRIPT
    assert "MutationObserver" in TABLE_A11Y_SCRIPT
