"""RC6 read-only GDELT DOC 2.0 SHADOW feed."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib, json
from typing import Iterable
from urllib.parse import urlsplit
import requests
from fi_event_risk_shadow_rc6 import EventEvidence, EVENT_TYPES
GDELT_HOST="api.gdeltproject.org"; GDELT_DOC_URL="https://api.gdeltproject.org/api/v2/doc/doc"; MAX_RECORDS_LIMIT=250
QUERY_PACKS={
"WAR_ESCALATION":'(war escalation OR military escalation OR missile attack OR invasion)',"CEASEFIRE":'(ceasefire OR cease-fire OR truce)',"CEASEFIRE_BREAKDOWN":'(ceasefire breakdown OR truce collapse OR cease-fire violation)',"SANCTIONS":'(sanctions OR economic sanctions OR trade restrictions)',"OIL_SUPPLY_SHOCK":'(oil supply disruption OR crude supply shock OR oil production cut)',"SHIPPING_DISRUPTION":'(shipping disruption OR tanker attack OR shipping route closure)',"ENERGY_INFRA_ATTACK":'(energy infrastructure attack OR refinery attack OR pipeline attack)',"CENTRAL_BANK":'(central bank rate decision OR emergency rate decision OR monetary intervention)',"FX_INTERVENTION":'(currency intervention OR foreign exchange intervention OR FX intervention)',"REGULATORY":'(market regulation OR securities regulation OR regulatory action)',"DEFAULT_RESTRUCTURING":'(sovereign default OR debt restructuring OR bond restructuring)',"NATURAL_DISASTER":'(earthquake OR hurricane OR flood OR wildfire)',"CYBER_INCIDENT":'(cyberattack OR ransomware OR major cyber incident)',"MARKET_HALT":'(market halt OR trading halt OR exchange suspension)',"POLITICAL_SHOCK":'(state of emergency OR government collapse OR coup OR political crisis)'}
for _event_type in QUERY_PACKS:
    if _event_type not in EVENT_TYPES: raise RuntimeError("GDELT_QUERY_PACK_EVENT_TYPE_INVALID")
class GDELTShadowError(RuntimeError): pass
def _utc_now(): return datetime.now(timezone.utc)
def _iso(dt):
    if dt.tzinfo is None: raise GDELTShadowError("NAIVE_TIMESTAMP")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def _parse_gdelt_date(value, *, fallback):
    raw=str(value or "").strip()
    if not raw: return _iso(fallback)
    for fmt in ("%Y%m%dT%H%M%SZ","%Y%m%d%H%M%S","%Y-%m-%dT%H:%M:%SZ"):
        try: return _iso(datetime.strptime(raw,fmt).replace(tzinfo=timezone.utc))
        except ValueError: pass
    raise GDELTShadowError("GDELT_TIMESTAMP_UNRECOGNIZED")
def build_params(*,event_type,timespan="24h",maxrecords=75):
    if event_type not in QUERY_PACKS: raise GDELTShadowError("GDELT_EVENT_TYPE_NOT_CONFIGURED")
    maxrecords=int(maxrecords)
    if not 1<=maxrecords<=MAX_RECORDS_LIMIT: raise GDELTShadowError("GDELT_MAXRECORDS_OUT_OF_RANGE")
    timespan=str(timespan or "").strip()
    if not timespan: raise GDELTShadowError("GDELT_TIMESPAN_REQUIRED")
    return {"query":QUERY_PACKS[event_type],"mode":"artlist","format":"json","sort":"datedesc","timespan":timespan,"maxrecords":str(maxrecords)}
def _assert_response_url(url):
    p=urlsplit(str(url))
    if p.scheme.lower()!="https" or (p.hostname or "").lower()!=GDELT_HOST: raise GDELTShadowError("GDELT_REDIRECT_OR_HOST_INVALID")
    if p.path.rstrip("/")!="/api/v2/doc/doc": raise GDELTShadowError("GDELT_PATH_INVALID")
def fetch_articles(*,event_type,timespan="24h",maxrecords=75,session=None,timeout=(8,20)):
    params=build_params(event_type=event_type,timespan=timespan,maxrecords=maxrecords); own=session is None; client=session or requests.Session()
    try:
        r=client.get(GDELT_DOC_URL,params=params,headers={"Accept":"application/json","User-Agent":"porota-rc6-gdelt-shadow/1.0"},timeout=timeout,allow_redirects=False)
        _assert_response_url(r.url)
        if r.status_code!=200: raise GDELTShadowError(f"GDELT_HTTP_{r.status_code}")
        try: payload=r.json()
        except (ValueError,json.JSONDecodeError) as exc: raise GDELTShadowError("GDELT_NON_JSON") from exc
        articles=payload.get("articles") if isinstance(payload,dict) else None
        if not isinstance(articles,list): raise GDELTShadowError("GDELT_ARTICLES_INVALID_SHAPE")
        return [a for a in articles if isinstance(a,dict)],_iso(_utc_now())
    finally:
        if own: client.close()
def normalize_article(article,*,event_type,retrieved_at):
    if event_type not in QUERY_PACKS: raise GDELTShadowError("GDELT_EVENT_TYPE_NOT_CONFIGURED")
    url=str(article.get("url") or "").strip(); title=str(article.get("title") or "").strip(); domain=str(article.get("domain") or "").strip()
    if not url or not title: raise GDELTShadowError("GDELT_ARTICLE_IDENTITY_INCOMPLETE")
    p=urlsplit(url)
    if p.scheme.lower() not in {"http","https"} or not p.hostname: raise GDELTShadowError("GDELT_ARTICLE_URL_INVALID")
    retrieved_dt=datetime.fromisoformat(retrieved_at.replace("Z","+00:00"))
    if retrieved_dt.tzinfo is None: raise GDELTShadowError("GDELT_RETRIEVED_AT_NAIVE")
    seen=_parse_gdelt_date(article.get("seendate"),fallback=retrieved_dt)
    canonical={"event_type":event_type,"url":url,"title":title,"domain":domain,"seendate":seen,"language":str(article.get("language") or ""),"sourcecountry":str(article.get("sourcecountry") or "")}
    raw=json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(",",":")); digest=hashlib.sha256(raw.encode()).hexdigest()
    e=EventEvidence(event_id="gdelt:"+digest[:24],event_type=event_type,first_seen_at=retrieved_at,published_at=seen,available_to_engine_at=retrieved_at,source=("GDELT_DOC:"+domain) if domain else "GDELT_DOC",source_tier="TIER_C_SINGLE_SOURCE",provenance_url=url,payload_hash=digest,region=str(article.get("sourcecountry") or "GLOBAL") or "GLOBAL")
    e.validate(); return e
def normalize_articles(articles:Iterable[dict],*,event_type,retrieved_at):
    out=[]; seen=set()
    for article in articles:
        try: e=normalize_article(dict(article),event_type=event_type,retrieved_at=retrieved_at)
        except GDELTShadowError: continue
        if e.event_id in seen: continue
        seen.add(e.event_id); out.append(e)
    return out
def collect_shadow(*,event_type,timespan="24h",maxrecords=75,session=None):
    articles,retrieved_at=fetch_articles(event_type=event_type,timespan=timespan,maxrecords=maxrecords,session=session)
    return [asdict(e) for e in normalize_articles(articles,event_type=event_type,retrieved_at=retrieved_at)]
def assert_shadow_only():
    assert "BUY" not in QUERY_PACKS and "SELL" not in QUERY_PACKS
