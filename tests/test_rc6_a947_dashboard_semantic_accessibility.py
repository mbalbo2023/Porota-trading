"""A947: adversarial dashboard semantics and non-disruptive voice-access refresh.

Execute the production inline JavaScript in Node, not a Python reimplementation.
All DOM/network objects are synthetic OFFHOST fixtures, never real providers.
"""

import json
import re
import subprocess

import pytest

import bg_paper_dashboard as dashboard
import rc6_dashboard_responsive_ux as responsive


def _node(program, payload):
    proc = subprocess.run(
        ["node", "-e", program], input=json.dumps(payload), text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _semaphore_script():
    source = responsive.SCRIPT
    start = source.index("  function stateFor(card){")
    end = source.index("  function installQuickNav(){", start)
    return source[start:end]


@pytest.mark.parametrize(
    ("value", "description", "expected"),
    [
        ("NO_VERIFICADO", "Broker: NO_VERIFICADO", "yellow"),
        ("SIN DATOS", "Book: NO_VERIFICADO", "yellow"),
        ("BROKER: SIN DATOS", "Observación en papel", "gray"),
        ("Book: NO_VERIFICADO", "CLOSED", "gray"),
        ("35.4%", "Broker: GREEN, Book: OK", "gray"),
        (None, "Broker: GREEN, Book: OK", "gray"),
        ("CLOSED", "Contrato finalizado", "gray"),
        ("BLOCKED_STALE", "Esperando contrato", "red"),
        ("ERROR", "Fetch bloqueado", "red"),
        ("PENDING", "Sin evidencia suficiente", "yellow"),
        ("INSUFFICIENT_EVIDENCE", "Libro sin cotización", "yellow"),
        ("READY_SHADOW", "Broker: NO_VERIFICADO", "green"),
        ("GREEN", "Estado operativo probado del componente", "green"),
        ("OK", "Autorización verificada del componente", "green"),
    ],
)
def test_semaphore_uses_only_explicit_metric_status(value, description, expected):
    code = _semaphore_script() + r"""
const payload=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const card={
  innerText: 'Análisis anual\n'+(payload.value||'')+'\n'+payload.description,
  querySelector: selector => selector==='.analysis-value' && payload.value!==null
    ? {innerText:payload.value} : null,
};
process.stdout.write(JSON.stringify({state:stateFor(card)}));
"""
    assert _node(code, {"value": value, "description": description})["state"] == expected


def test_semaphore_exposes_status_in_spanish_without_certifying_paper_orders():
    script = responsive.SCRIPT.split(">", 1)[1].rsplit("</script>", 1)[0]
    program = r"""
const vm=require('node:vm');
const src=require('node:fs').readFileSync(0,'utf8');
let dot=null;
const card={
  innerText:'Book: NO_VERIFICADO',
  querySelector(selector){
    if(selector==='.analysis-value')return{innerText:'NO_VERIFICADO'};
    if(selector==='.porota-semaphore')return dot;
    return null;
  },
  appendChild(node){dot=node;},
};
const document={
  readyState:'complete',documentElement:{},body:{id:'top'},
  getElementById:id=>id==='porota-quick-nav'?{}:null,
  querySelectorAll(selector){return selector==='.analysis-grid>.paper-card'?[card]:[];},
  createElement:()=>({attrs:{},setAttribute(name,value){this.attrs[name]=value;}}),
};
vm.runInNewContext(src,{document,MutationObserver:class{observe(){}},window:{}});
process.stdout.write(JSON.stringify({className:dot.className,
  role:dot.attrs.role,label:dot.attrs['aria-label']}));
"""
    # The production script under test is the VM input; the runner is only DOM.
    proc = subprocess.run(["node", "-e", program], input=script,
                          text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    observed = json.loads(proc.stdout)
    assert observed["className"] == "porota-semaphore yellow"
    assert observed["role"] == "img"
    assert "amarillo" in observed["label"].lower()
    assert "PAPER" in observed["label"]


def _refresh_script():
    page = dashboard._document("A947", "<h1>Prueba sin red</h1>", refresh=30)
    match = re.search(r"<script>\(function\(\)\{\s*const seconds=.*?</script>",
                      page, flags=re.S)
    assert match, "Expected actual partial-refresh script on a timed page"
    return match.group(0).removeprefix("<script>").removesuffix("</script>")


def test_refresh_toggle_is_stable_outside_replaced_main_and_voice_accessible():
    page = dashboard._document("A947", "<h1>Contenido</h1>", refresh=30)
    assert page.index("id='porota-auto-refresh-toggle'") < page.index("<main")
    assert "aria-pressed='false'" in page
    assert "id='porota-auto-refresh-status'" in page
    assert "aria-live='polite'" in page
    assert "window.refreshPorota(true)" in page


_REFRESH_HARNESS = r"""
const vm=require('node:vm');
const payload=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
let requests=0, swaps=0, banners=0, bannerReplacements=0, bannerCreated=0, updates=0, interval=0;
let autoCallback;
let finishOld;
const listeners={};
const active={
  id:'reading-paragraph',
  matches:()=>false,
  closest:()=>null,
};
const main={
  contains: element => element===active && payload.focusInside===true,
  replaceWith:()=>{swaps++;},
};
function makeBanner(initial){return{
  _html:initial,
  get innerHTML(){return this._html;},
  set innerHTML(value){this._html=value;banners++;},
  replaceWith:()=>{banners++;bannerReplacements++;},
};}
let banner=payload.currentBannerMissing?null:makeBanner('GREEN — old mode may be stale');
const toggle={
  id:'porota-auto-refresh-toggle',
  textContent:'',
  attrs:{},
  setAttribute(name,value){this.attrs[name]=value;},
  addEventListener(name,cb){listeners['toggle-'+name]=cb;},
};
const status={
  _text:'',writes:0,
  get textContent(){return this._text;},
  set textContent(value){this._text=value;this.writes++;},
};
const selection={toString:()=>payload.selection||''};
const document={
  hidden:false,
  body:{prepend(node){banner=node;bannerCreated++;}},
  activeElement:payload.focusInside?active:{id:'outside',matches:()=>false,closest:()=>null},
  querySelector(selector){
    if(selector==='dialog[open]')return null;
    if(selector==='main.paper-page')return main;
    if(selector==='#porota-paper-mode')return banner;
    if(selector==='.paper-actions')return{before(node){banner=node;bannerCreated++;}};
    return null;
  },
  createElement:tag=>makeBanner(''),
  querySelectorAll:()=>[],
  getElementById(id){
    if(id==='porota-auto-refresh-toggle')return toggle;
    if(id==='porota-auto-refresh-status')return status;
    return null;
  },
  addEventListener(name,cb){listeners[name]=cb;},
  getSelection:()=>selection,
};
const window={
  scrollY:0,
  scrollTo:()=>{},
  setInterval(cb,ms){updates++;interval=ms;autoCallback=cb;},
  matchMedia:()=>({matches:!!payload.reducedMotion}),
  getSelection:()=>selection,
};
const sessionStorage={
  getItem:()=>payload.savedPause?'1':null,
  setItem:()=>{},
};
const context={
  document,window,sessionStorage,location:{href:'http://offline.invalid/qa'},
  fetch:async()=>{
    const kind=(payload.responses||[])[requests]||payload.responseKind||'valid';
    requests++;
    if(kind==='networkError')throw new Error('Synthetic network denied');
    const response={ok:kind!=='login401',text:async()=>kind};
    if(payload.trigger==='race'&&requests===1)return new Promise(resolve=>{
      finishOld=()=>resolve(response);
    });
    return response;
  },
  DOMParser:class { parseFromString(kind){
    if(kind==='malformed')throw new Error('Synthetic malformed HTML');
    return{
    querySelector: selector => selector==='main.paper-page'
      ? (kind==='missingMain'?null:{}) : selector==='#porota-paper-mode' && kind!=='missingBanner'
        ? {innerHTML:'new mode'} : null,
  };}},
  CSS:{escape: value=>value},
};
vm.runInNewContext(payload.script,context);
(async()=>{
  if(payload.trigger==='interaction')listeners.focusin?.({target:active});
  if(payload.trigger==='toggle')listeners['toggle-click']?.({preventDefault(){}});
  if(payload.trigger==='manual')await window.refreshPorota(true);
  else if(payload.trigger==='race'){
    const old=autoCallback();
    const newest=autoCallback();
    await newest;
    finishOld();
    await old;
  }
  else if(autoCallback){
    for(let n=0;n<(payload.repeat||1);n++)await autoCallback();
  }
  process.stdout.write(JSON.stringify({requests,swaps,banners,bannerReplacements,bannerCreated,updates,interval,
    toggleAria:toggle.attrs['aria-pressed'],toggleText:toggle.textContent,
    statusText:status.textContent,statusWrites:status.writes,bannerHTML:banner?.innerHTML,
  }));
})().catch(error=>{process.stderr.write(error.stack);process.exit(1)});
"""


@pytest.mark.parametrize("trigger,fixture", [
    ("auto", {"focusInside": True}),
    ("auto", {"selection": "Estoy leyendo este texto"}),
    ("interaction", {"focusInside": True}),
    ("auto", {"reducedMotion": True}),
    ("auto", {"savedPause": True}),
])
def test_refresh_preserves_voice_reading_but_updates_safety_banner(trigger, fixture):
    result = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                      "trigger": trigger, **fixture})
    assert result["updates"] == 1  # one preexisting interval; no new timer
    assert result["requests"] == 1  # still fetches current evidence
    assert result["banners"] == 1  # keeps outside-main safety message current
    assert result["swaps"] == 0  # no main / virtual-cursor disruption
    assert result["toggleAria"] == "true"
    assert "Reanudar" in result["toggleText"]


def test_user_can_resume_auto_refresh_by_voice_and_manual_refresh_is_available():
    resumed = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                         "savedPause": True, "trigger": "toggle"})
    assert resumed["swaps"] == 1
    assert resumed["toggleAria"] == "false"
    assert "Pausar" in resumed["toggleText"]

    manual = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                        "savedPause": True, "trigger": "manual"})
    assert manual["swaps"] == 1
    assert manual["banners"] == 1


def test_unpaused_refresh_updates_main_and_mode_banner_without_extra_timer():
    result = _node(_REFRESH_HARNESS, {"script": _refresh_script(), "trigger": "auto"})
    assert result["updates"] == 1
    assert result["requests"] == 1
    assert result["banners"] == 1
    assert result["swaps"] == 1
    assert result["toggleAria"] == "false"


def test_paused_refresh_keeps_banner_dom_identity_and_does_not_repeat_aria_status():
    result = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                      "trigger": "auto", "savedPause": True, "repeat": 2})
    assert result["requests"] == 2
    assert result["swaps"] == 0
    assert result["banners"] == 1  # changed once, not on unchanged second sample
    assert result["bannerReplacements"] == 0  # keep wrapper/ARIA/focus identity
    assert result["statusWrites"] == 1  # no repeated live-region announcements


@pytest.mark.parametrize("failure", ["missingBanner", "missingMain", "login401", "networkError", "malformed"])
def test_untrusted_refresh_response_revokes_old_green_without_destroying_main(failure):
    result = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                      "trigger": "auto", "responseKind": failure,
                                      "repeat": 2})
    assert result["requests"] == 2
    assert result["swaps"] == 0
    assert "NO_VERIFICADO" in result["bannerHTML"]
    assert "GREEN" not in result["bannerHTML"]
    assert "NO_VERIFICADO" in result["statusText"]
    assert result["bannerReplacements"] == 0
    assert result["banners"] == 1  # failed second fetch cannot reannounce same warning
    assert result["statusWrites"] == 2  # initial state + one changed failure state


@pytest.mark.parametrize("saved_pause,expected_swaps", [(False, 1), (True, 0)])
def test_valid_refresh_recovers_status_after_malformed_prior_receipt(saved_pause, expected_swaps):
    result = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                      "trigger": "auto", "savedPause": saved_pause,
                                      "responses": ["missingBanner", "valid"], "repeat": 2})
    assert result["requests"] == 2
    assert result["swaps"] == expected_swaps
    assert result["banners"] == 2
    assert result["bannerHTML"] == "new mode"
    assert "NO_VERIFICADO" not in result["statusText"]


@pytest.mark.parametrize("old,new,expected_swap,expected_banner", [
    ("valid", "login401", 0, "NO_VERIFICADO"),
    ("login401", "valid", 1, "new mode"),
])
def test_late_older_refresh_cannot_overwrite_newer_safety_receipt(
    old, new, expected_swap, expected_banner,
):
    result = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                      "trigger": "race", "responses": [old, new]})
    assert result["updates"] == 1
    assert result["requests"] == 2
    assert result["swaps"] == expected_swap
    assert expected_banner in result["bannerHTML"]


@pytest.mark.parametrize("paused", [False, True])
def test_missing_current_mode_banner_blocks_main_and_recovers_on_next_receipt(paused):
    result = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                      "trigger": "auto", "currentBannerMissing": True,
                                      "savedPause": paused, "repeat": 1})
    assert result["requests"] == 1
    assert result["swaps"] == 0
    assert result["bannerCreated"] == 1
    assert "NO_VERIFICADO" in result["bannerHTML"]
    assert result["statusWrites"] == 2

    recovered = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                         "trigger": "auto", "currentBannerMissing": True,
                                         "savedPause": paused, "repeat": 2})
    assert recovered["requests"] == 2
    assert recovered["bannerCreated"] == 1
    assert recovered["swaps"] == (0 if paused else 1)
    assert recovered["bannerHTML"] == "new mode"


def test_racing_receipts_when_current_banner_missing_stay_fail_closed():
    result = _node(_REFRESH_HARNESS, {"script": _refresh_script(),
                                      "trigger": "race", "currentBannerMissing": True,
                                      "responses": ["valid", "valid"]})
    assert result["requests"] == 2
    assert result["swaps"] == 0
    assert result["bannerCreated"] == 1
    assert "NO_VERIFICADO" in result["bannerHTML"]
