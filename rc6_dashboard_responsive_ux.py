"""RC6 responsive dashboard overlay for tablet/Voice Access.

Presentation-only: keeps real HTML tables, fits them to the viewport, truncates
long cells with an accessible tap/click expansion, and presents analysis metrics
as compact semaphore cards. It never changes data, decisions, or routes.
"""
from __future__ import annotations

import bg_paper_dashboard as bg

_installed = False

CSS = r"""
<style id="porota-rc6-responsive-tablet">
html,body,main,.paper-page,.paper-card,.paper-grid,.paper-table-wrap,.system-content,.legacy-shell{min-width:0!important;max-width:100vw!important;box-sizing:border-box}
html,body{overflow-x:hidden!important}
.paper-card,.paper-table-wrap,.analysis-grid{max-width:100%;box-sizing:border-box}
.paper-table-wrap{width:100%;overflow:visible!important}
.paper-table-wrap table,.paper-card table,.classic-responsive-table{display:table!important;width:100%!important;min-width:0!important;max-width:100%!important;table-layout:fixed!important;border-collapse:collapse!important}
.paper-table-wrap th,.paper-table-wrap td,.paper-card table th,.paper-card table td,
.classic-responsive-table th,.classic-responsive-table td{display:table-cell;min-width:0!important;max-width:0!important;overflow:hidden!important;text-overflow:ellipsis;white-space:nowrap;vertical-align:top}
.paper-table-wrap th,.paper-table-wrap td,.paper-card table th,.paper-card table td{padding:7px 6px}
.paper-table-wrap td[data-wrap="true"],.paper-card table td[data-wrap="true"],
.classic-responsive-table td[data-wrap="true"]{max-width:none!important;overflow:visible!important;white-space:normal!important;overflow-wrap:anywhere;word-break:break-word}
.paper-table-wrap td[data-porota-expanded="1"],.paper-card table td[data-porota-expanded="1"],
.classic-responsive-table td[data-porota-expanded="1"]{max-width:none!important;overflow:visible!important;white-space:normal!important;overflow-wrap:anywhere;word-break:break-word;position:relative;z-index:4;background:#fff}
.porota-expandable{cursor:pointer;text-decoration:underline dotted;text-decoration-color:#8797aa}
.porota-expandable::after{content:" …";color:#62748a;font-weight:700}
.analysis-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:10px;align-items:stretch}
.analysis-grid>.paper-card{margin:0;min-width:0;position:relative;padding:12px}
.analysis-grid>.paper-card h2{font-size:.92rem;line-height:1.2;margin:0 0 7px;padding-right:18px}
.analysis-grid>.paper-card .analysis-value{font-size:1.02rem;font-weight:700;line-height:1.25;margin:0;overflow-wrap:anywhere}
.porota-semaphore{position:absolute;right:12px;top:14px;width:11px;height:11px;border-radius:50%;box-shadow:0 0 0 2px #fff}
.porota-semaphore.green{background:#168544}.porota-semaphore.yellow{background:#b47a00}.porota-semaphore.red{background:#ba2937}.porota-semaphore.gray{background:#7b8794}
#porota-scroll-rail{position:fixed;right:3px;top:112px;bottom:12px;width:9px;background:#dbe4ef;border:1px solid #aebdd0;border-radius:99px;z-index:9999;box-shadow:0 1px 4px #14213d33}
#porota-scroll-thumb{position:absolute;left:1px;right:1px;top:0;min-height:28px;background:#1769aa;border-radius:99px;cursor:pointer}
@media(max-width:700px){
  .paper-table-wrap th,.paper-table-wrap td,.paper-card table th,.paper-card table td,.classic-responsive-table th,.classic-responsive-table td{padding:6px 5px;font-size:.78rem}
  .paper-card table th,.paper-card table td{line-height:1.2}
  .analysis-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
  .analysis-grid>.paper-card{padding:10px}
  .analysis-grid>.paper-card h2{font-size:.78rem}
  .analysis-grid>.paper-card .analysis-value{font-size:.88rem}
}
@media(max-width:390px){.analysis-grid{grid-template-columns:1fr}}
</style>
"""

SCRIPT = r"""
<script id="porota-rc6-responsive-tablet-script">
(function(){
  function stateFor(card){
    const text=(card.innerText||"").toUpperCase();
    if(/BLOCKED|ERROR|NO DISPONIBLE|SIN CIERRES|NO SE PUDO/.test(text)) return "red";
    if(/PENDING|PENDIENTE|INSUFFICIENT|FALTA|NO EMITIDA|MIXTA|DEBIL|DEGRAD/.test(text)) return "yellow";
    if(/GREEN|READY|VERIFIED|FAVORABLE|COMPLETA|OK/.test(text)) return "green";
    return "gray";
  }
  function installScrollRail(){
    if(document.getElementById("porota-scroll-rail")) return;
    const rail=document.createElement("div");
    rail.id="porota-scroll-rail";
    rail.setAttribute("aria-label","Progreso de la página; tocar para desplazarse");
    const thumb=document.createElement("span");
    thumb.id="porota-scroll-thumb";
    rail.appendChild(thumb);
    document.body.appendChild(rail);
    function sync(){
      const max=Math.max(1,document.documentElement.scrollHeight-window.innerHeight);
      const ratio=Math.min(1,Math.max(0,window.scrollY/max));
      const visible=Math.min(1,window.innerHeight/Math.max(window.innerHeight,document.documentElement.scrollHeight));
      thumb.style.height=Math.max(28,Math.round(rail.clientHeight*visible))+"px";
      thumb.style.top=Math.round((rail.clientHeight-thumb.offsetHeight)*ratio)+"px";
    }
    rail.addEventListener("click",function(event){
      if(event.target===thumb) return;
      const rect=rail.getBoundingClientRect();
      const ratio=Math.min(1,Math.max(0,(event.clientY-rect.top)/rect.height));
      window.scrollTo({top:ratio*Math.max(0,document.documentElement.scrollHeight-window.innerHeight),behavior:"smooth"});
    });
    window.addEventListener("scroll",sync,{passive:true});
    window.addEventListener("resize",sync);
    sync();
  }
  function decorate(){
    installScrollRail();
    document.querySelectorAll("table").forEach(function(table){
      table.querySelectorAll("td").forEach(function(cell){
        const raw=(cell.innerText||"").replace(/\s+/g," ").trim();
        if(raw.length<32 || cell.querySelector("a,button,details,input,select,textarea")) return;
        cell.classList.add("porota-expandable");
        cell.title="Tocar para expandir: "+raw;
        cell.setAttribute("aria-label",raw);
        if(!cell.dataset.porotaBound){
          cell.dataset.porotaBound="1";
          cell.addEventListener("click",function(){
            const expanded=cell.dataset.porotaExpanded==="1";
            cell.dataset.porotaExpanded=expanded?"0":"1";
          });
        }
      });
    });
    document.querySelectorAll(".analysis-grid>.paper-card").forEach(function(card){
      if(card.querySelector(".porota-semaphore")) return;
      const dot=document.createElement("span");
      dot.className="porota-semaphore "+stateFor(card);
      dot.setAttribute("aria-label","Estado "+dot.className.split(" ").pop());
      dot.title="Estado "+dot.className.split(" ").pop();
      card.appendChild(dot);
    });
  }
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",decorate,{once:true});
  else decorate();
  new MutationObserver(decorate).observe(document.documentElement,{childList:true,subtree:true});
})();
</script>
"""

def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    if "porota-rc6-responsive-tablet" not in bg.TABLE_A11Y_CSS:
        bg.TABLE_A11Y_CSS += CSS
    if "porota-rc6-responsive-tablet-script" not in bg.TABLE_A11Y_SCRIPT:
        bg.TABLE_A11Y_SCRIPT += SCRIPT
