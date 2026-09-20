import pytest
from fk_gdelt_shadow_feed_rc6 import GDELTShadowError,build_params,fetch_articles,normalize_article
class FakeResponse:
    def __init__(self,*,url,status_code=200,payload=None): self.url=url; self.status_code=status_code; self._payload=payload if payload is not None else {'articles':[]}
    def json(self): return self._payload
class FakeSession:
    def __init__(self,response): self.response=response; self.calls=[]
    def get(self,url,**kwargs): self.calls.append((url,kwargs)); return self.response
def test_query_pack_is_artlist_json_and_bounded():
    p=build_params(event_type='SANCTIONS',timespan='24h',maxrecords=75); assert p['mode']=='artlist'; assert p['format']=='json'; assert p['sort']=='datedesc'; assert p['maxrecords']=='75'
def test_maxrecords_fail_closed():
    with pytest.raises(GDELTShadowError,match='MAXRECORDS'): build_params(event_type='SANCTIONS',maxrecords=251)
def test_fetch_is_single_read_only_get_and_no_redirects():
    s=FakeSession(FakeResponse(url='https://api.gdeltproject.org/api/v2/doc/doc?query=x',payload={'articles':[{'url':'https://example.com/a','title':'A'}]})); articles,retrieved=fetch_articles(event_type='SANCTIONS',session=s); assert len(articles)==1; assert retrieved.endswith('Z'); assert len(s.calls)==1; url,kwargs=s.calls[0]; assert url=='https://api.gdeltproject.org/api/v2/doc/doc'; assert kwargs['allow_redirects'] is False; assert 'data' not in kwargs and 'json' not in kwargs
def test_redirect_or_wrong_host_is_rejected():
    with pytest.raises(GDELTShadowError,match='HOST'): fetch_articles(event_type='SANCTIONS',session=FakeSession(FakeResponse(url='https://evil.example/api/v2/doc/doc')))
def test_article_normalizes_to_unconfirmed_single_source_shadow_evidence():
    e=normalize_article({'url':'https://news.example/story','title':'EU sanctions target Russian oil exports as energy supply concerns grow','domain':'news.example','seendate':'20260908T170000Z','language':'English','sourcecountry':'United States'},event_type='SANCTIONS',retrieved_at='2026-09-08T17:05:00Z'); assert e.event_type=='SANCTIONS'; assert e.source_tier=='TIER_C_SINGLE_SOURCE'; assert e.confirmed_at is None; assert e.retracted_at is None; assert e.entities==(); assert e.exposures==(); assert e.available_to_engine_at=='2026-09-08T17:05:00Z'; assert e.provenance_url=='https://news.example/story'


@pytest.mark.parametrize("event_type,title", [
    ("DEFAULT_RESTRUCTURING", "Remco Evenepoel wins the world cycling championship"),
    ("DEFAULT_RESTRUCTURING", "Cultural heritage fund expected to start operating in 2028"),
    ("DEFAULT_RESTRUCTURING", "Man accused of killing a woman he mistook for a deer"),
    ("NATURAL_DISASTER", "Earthquake rescue crews search collapsed homes"),
    ("CYBER_INCIDENT", "Ransomware attack disrupts a local school district"),
])
def test_rejects_headlines_without_financial_market_relevance(event_type, title):
    from fk_gdelt_shadow_feed_rc6 import is_market_relevant_title
    assert not is_market_relevant_title(event_type, title)


@pytest.mark.parametrize("event_type,title", [
    ("DEFAULT_RESTRUCTURING", "Sovereign debt restructuring talks resume as bond yields climb"),
    ("OIL_SUPPLY_SHOCK", "Oil supply disruption sends crude prices higher"),
    ("WAR_ESCALATION", "Russia escalates military strikes near Ukraine"),
    ("POLITICAL_SHOCK", "Government collapses in China amid currency market turmoil"),
    ("NATURAL_DISASTER", "Earthquake shuts a major port and disrupts oil exports"),
    ("CENTRAL_BANK", "Federal Reserve signals interest rate cut as inflation slows"),
])
def test_accepts_financial_or_market_relevant_headlines(event_type, title):
    from fk_gdelt_shadow_feed_rc6 import is_market_relevant_title
    assert is_market_relevant_title(event_type, title)


@pytest.mark.parametrize("event_type,title", [
    ("SANCTIONS", "School sanctions student after disciplinary hearing"),
    ("OIL_SUPPLY_SHOCK", "Oil company opens new customer service office"),
    ("SHIPPING_DISRUPTION", "Tanker crew rescued after routine engine failure"),
])
def test_rejects_topic_word_without_market_or_systemic_impact(event_type, title):
    from fk_gdelt_shadow_feed_rc6 import is_market_relevant_title
    assert not is_market_relevant_title(event_type, title)


def test_normalize_articles_drops_irrelevant_search_matches():
    from fk_gdelt_shadow_feed_rc6 import normalize_articles
    articles = [
        {"url": "https://news.example/cycling", "title": "Remco Evenepoel wins the world cycling championship", "domain": "news.example"},
        {"url": "https://news.example/debt", "title": "Sovereign debt restructuring talks resume as bond yields climb", "domain": "news.example"},
    ]
    out = normalize_articles(articles, event_type="DEFAULT_RESTRUCTURING", retrieved_at="2026-09-20T00:00:00Z")
    assert len(out) == 1
    assert out[0].provenance_url.endswith("/debt")


def test_default_restructuring_query_quotes_multiword_phrases():
    query = build_params(event_type="DEFAULT_RESTRUCTURING")["query"]
    assert '"sovereign default"' in query
    assert '"debt restructuring"' in query
    assert '"bond restructuring"' in query


def test_normalize_article_fails_closed_on_irrelevant_title():
    from fk_gdelt_shadow_feed_rc6 import GDELTShadowError
    with pytest.raises(GDELTShadowError, match="NOT_MARKET_RELEVANT"):
        normalize_article({"url": "https://news.example/sports", "title": "Remco Evenepoel wins the world cycling championship", "domain": "news.example"}, event_type="DEFAULT_RESTRUCTURING", retrieved_at="2026-09-20T00:00:00Z")
