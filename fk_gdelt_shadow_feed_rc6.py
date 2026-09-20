"""RC6 read-only GDELT DOC 2.0 SHADOW feed."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib, json, re, unicodedata
from typing import Iterable
from urllib.parse import urlsplit
import requests
from fi_event_risk_shadow_rc6 import EventEvidence, EVENT_TYPES
GDELT_HOST="api.gdeltproject.org"; GDELT_DOC_URL="https://api.gdeltproject.org/api/v2/doc/doc"; MAX_RECORDS_LIMIT=250
QUERY_PACKS={
"WAR_ESCALATION":'("war escalation" OR "military escalation" OR "missile attack" OR invasion OR "armed conflict" OR airstrike)',
"CEASEFIRE":'(ceasefire OR "cease-fire" OR truce OR armistice)',
"CEASEFIRE_BREAKDOWN":'("ceasefire breakdown" OR "truce collapse" OR "cease-fire violation")',
"SANCTIONS":'(sanctions OR "economic sanctions" OR "trade restrictions" OR "export controls")',
"OIL_SUPPLY_SHOCK":'("oil supply disruption" OR "crude supply shock" OR "oil production cut" OR "gas supply disruption")',
"SHIPPING_DISRUPTION":'("shipping disruption" OR "tanker attack" OR "shipping route closure" OR "strait closure")',
"ENERGY_INFRA_ATTACK":'("energy infrastructure attack" OR "refinery attack" OR "pipeline attack" OR "power grid attack")',
"CENTRAL_BANK":'("central bank rate decision" OR "emergency rate decision" OR "monetary policy decision" OR "interest rate decision")',
"FX_INTERVENTION":'("currency intervention" OR "foreign exchange intervention" OR "FX intervention")',
"REGULATORY":'("financial market regulation" OR "securities regulation" OR "banking regulation" OR "stock exchange regulation")',
"DEFAULT_RESTRUCTURING":'("sovereign default" OR "debt restructuring" OR "bond restructuring" OR "sovereign debt default")',
"NATURAL_DISASTER":'(earthquake OR hurricane OR flood OR wildfire)',
"CYBER_INCIDENT":'(cyberattack OR ransomware OR "major cyber incident")',
"MARKET_HALT":'("market halt" OR "trading halt" OR "exchange suspension" OR "stock trading suspension")',
"POLITICAL_SHOCK":'("state of emergency" OR "government collapse" OR coup OR "political crisis")'
}

# API queries search article text, not only headlines. Fail closed on unrelated
# titles so a body-text match cannot become a market event by itself.
_EVENT_PATTERNS = {
    "WAR_ESCALATION": r"\b(war|military|missile|invasion|attack|airstrike|strike|escalat\w*|conflict|troop|guerra|ataque|misil\w*|invasion|escalad\w*|conflicto|guerre|attaque|krieg|angriff)\b",
    "CEASEFIRE": r"\b(ceasefire|cease-fire|truce|armistice|peace deal|alto el fuego|tregua|armisticio|cessez le feu|waffenruhe)\b",
    "CEASEFIRE_BREAKDOWN": r"\b(ceasefire|cease-fire|truce|armistice|alto el fuego|tregua|armisticio|cessez le feu|waffenruhe)\b",
    "SANCTIONS": r"\b(sanction\w*|trade restriction\w*|export control\w*|sancion\w*|sancoes|restriccion\w* comercial\w*|sanktion\w*|sanzion\w*)\b",
    "OIL_SUPPLY_SHOCK": r"\b(oil|crude|petroleum|natural gas|lng|opec|petrole\w*|crudo|gas naturel|erdol|petrolio)\b",
    "SHIPPING_DISRUPTION": r"\b(shipping|tanker|strait|canal|port|maritime|ruta maritima|estrecho|puerto|transporte maritimo|detroit|hafen|porto)\b",
    "ENERGY_INFRA_ATTACK": r"\b(energy|refinery|pipeline|power grid|electric grid|energy infrastructure|energia|refineria|oleoducto|gasoducto|red electrica|energie|raffinerie|stromnetz)\b",
    "CENTRAL_BANK": r"\b(central bank|federal reserve|fed\b|ecb\b|european central bank|bank of japan|boj\b|interest rate|rate cut|rate hike|monetary policy|inflation|banco central|reserva federal|tasas? de interes|tipo de interes|politica monetaria|inflacion|banque centrale|taux d.interet|politique monetaire|zentralbank|leitzins|banca centrale|tassi di interesse|politica monetaria)\b",
    "FX_INTERVENTION": r"\b(currency|foreign exchange|forex|fx\b|exchange rate|divisa|moneda|tipo de cambio|cambio de divisas|devise|taux de change|wahrung|wechselkurs|valuta|tasso di cambio)\b",
    "REGULATORY": r"\b(financial market|securities|stock exchange|banking regulation|financial regulator|capital market|mercado financiero|valores negociables|bolsa de valores|regulacion bancaria|marches financiers|regulation bancaire|wertpapier|finanzmarkt|mercato finanziario|borsa valori)\b",
    "DEFAULT_RESTRUCTURING": r"\b(sovereign debt|sovereign default|debt default|bond default|debt restructuring|bond restructuring|default on (the )?debt|reestructuracion de deuda|impago soberano|incumplimiento de deuda|reestruturacao da divida|calote soberano|defaut souverain|restructuration de la dette|staatsschulden|umschuldung|default sovrano|ristrutturazione del debito)\b",
    "NATURAL_DISASTER": r"\b(earthquake|hurricane|flood|wildfire|huracan|inundacion|incendio forestal|seisme|inondation|waldbrand|terremoto|alluvione)\b",
    "CYBER_INCIDENT": r"\b(cyberattack|cyber attack|ransomware|cyber incident|ciberataque|ataque informatico|ataque cibernetico|cyberattaque|cyberangriff|attacco informatico)\b",
    "MARKET_HALT": r"\b(market halt|trading halt|exchange suspension|stock trading suspension|halted trading|suspension de cotation|handelsaussetzung|sospensione delle contrattazioni)\b",
    "POLITICAL_SHOCK": r"\b(state of emergency|government collaps(?:e|es|ed)|coup|political crisis|early election|snap election|estado de emergencia|caida del gobierno|golpe de estado|crisis politica|etat d.urgence|chute du gouvernement|staatskrise|regierungskrise|colpo di stato|crisi politica)\b",
}
_MARKET_IMPACT_PATTERN = re.compile(
    r"\b(financial|finance|market|markets|stock|stocks|shares|bond|bonds|yield|yields|investor\w*|currency|currencies|forex|exchange rate|inflation|interest rate\w*|central bank|econom\w*|trade|tariff\w*|sanction\w*|oil|crude|petroleum|gas|energy|commodit\w*|supply|shipping|tanker|port|pipeline|refinery|export\w*|import\w*|debt|credit|bank\w*|securit\w*|mercado\w*|financier\w*|econom\w*|comercio|arancel\w*|sancion\w*|petrole\w*|energia|materias primas|suministro|puerto|oleoducto|gasoducto|exportacion\w*|importacion\w*|deuda|bono\w*|inflacion|interes\w*|finance\w*|economie\w*|commerce|sanction\w*|approvisionnement|dette|obligation\w*|zins\w*|anleihe\w*|schuld\w*|wirtschaft\w*|handel|sanktion\w*|ol\b|versorgung|markt\w*|finanz\w*|commercio|dazi|petrolio|debito|obbligazion\w*|inflazion\w*|tassi)\b",
    re.IGNORECASE,
)
_SYSTEMIC_GEO_PATTERN = re.compile(
    r"\b(united states|u\.s\.|usa|china|russia|ukraine|iran|israel|taiwan|north korea|south korea|european union|eu\b|nato|opec|strait of hormuz|red sea|south china sea|black sea|estados unidos|rusia|ucrania|union europea|mar rojo|mar negro|etats unis|chine|russie|ukraine|union europeenne|vereinigte staaten|russland|stati uniti|ucraina|unione europea)\b",
    re.IGNORECASE,
)
_SANCTIONS_IMPACT_PATTERN = re.compile(
    r"\b(financial|finance|market|stock|bond|yield|currency|forex|exchange rate|econom\w*|trade|tariff\w*|oil|crude|petroleum|gas|energy|commodit\w*|export\w*|import\w*|company|bank\w*|debt|credit|supply|mercado\w*|financier\w*|econom\w*|comercio|arancel\w*|petrole\w*|energia|exportacion\w*|importacion\w*|deuda|bono\w*|finance\w*|economie\w*|commerce|approvisionnement|dette|wirtschaft\w*|handel|debito|mercato finanziario)\b",
    re.IGNORECASE,
)
_OIL_SUPPLY_IMPACT_PATTERN = re.compile(
    r"\b(price\w*|market\w*|supply|disrupt\w*|shock|production|output|export\w*|import\w*|shortage|opec|precio\w*|mercado\w*|suministro|disrupcion|produccion|exportacion\w*|prix|marche\w*|approvisionnement|produktion|markt\w*)\b",
    re.IGNORECASE,
)
_SHIPPING_IMPACT_PATTERN = re.compile(
    r"\b(trade|cargo|freight|supply|oil|crude|gas|energy|export\w*|import\w*|price\w*|market\w*|comercio|carga|flete|suministro|petrole\w*|energia|exportacion\w*|importacion\w*|prix|commerce|approvisionnement|fracht|handel)\b",
    re.IGNORECASE,
)
_DIRECT_FINANCIAL_TYPES = frozenset({
    "CENTRAL_BANK", "FX_INTERVENTION", "REGULATORY",
    "DEFAULT_RESTRUCTURING", "MARKET_HALT",
})

def _fold_title(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))

def is_market_relevant_title(event_type: str, title: str) -> bool:
    """Fail closed unless the headline itself proves event and market relevance."""
    text = _fold_title(title)
    event_pattern = _EVENT_PATTERNS.get(event_type)
    if not text or event_pattern is None or not re.search(event_pattern, text, re.IGNORECASE):
        return False
    if event_type in _DIRECT_FINANCIAL_TYPES:
        return True
    if event_type == "SANCTIONS":
        return bool(_SANCTIONS_IMPACT_PATTERN.search(text) or _SYSTEMIC_GEO_PATTERN.search(text))
    if event_type == "OIL_SUPPLY_SHOCK":
        return bool(_OIL_SUPPLY_IMPACT_PATTERN.search(text))
    if event_type == "SHIPPING_DISRUPTION":
        return bool(
            _SHIPPING_IMPACT_PATTERN.search(text)
            or _SYSTEMIC_GEO_PATTERN.search(text)
        )
    if _MARKET_IMPACT_PATTERN.search(text):
        return True
    return event_type in {
        "WAR_ESCALATION", "CEASEFIRE", "CEASEFIRE_BREAKDOWN", "POLITICAL_SHOCK"
    } and bool(_SYSTEMIC_GEO_PATTERN.search(text))


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
    if not is_market_relevant_title(event_type, title): raise GDELTShadowError("GDELT_TITLE_NOT_MARKET_RELEVANT")
    p=urlsplit(url)
    if p.scheme.lower() not in {"http","https"} or not p.hostname: raise GDELTShadowError("GDELT_ARTICLE_URL_INVALID")
    retrieved_dt=datetime.fromisoformat(retrieved_at.replace("Z","+00:00"))
    if retrieved_dt.tzinfo is None: raise GDELTShadowError("GDELT_RETRIEVED_AT_NAIVE")
    seen=_parse_gdelt_date(article.get("seendate"),fallback=retrieved_dt)
    canonical={"event_type":event_type,"url":url,"title":title,"domain":domain,"seendate":seen,"language":str(article.get("language") or ""),"sourcecountry":str(article.get("sourcecountry") or "")}
    raw=json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(",",":")); digest=hashlib.sha256(raw.encode()).hexdigest()
    e=EventEvidence(event_id="gdelt:"+digest[:24],event_type=event_type,first_seen_at=retrieved_at,published_at=seen,available_to_engine_at=retrieved_at,source=("GDELT_DOC:"+domain) if domain else "GDELT_DOC",source_tier="TIER_C_SINGLE_SOURCE",provenance_url=url,payload_hash=digest,region=str(article.get("sourcecountry") or "GLOBAL") or "GLOBAL",title=title,source_domain=domain)
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
