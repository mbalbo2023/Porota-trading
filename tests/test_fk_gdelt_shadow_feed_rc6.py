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
    e=normalize_article({'url':'https://news.example/story','title':'Sanctions headline','domain':'news.example','seendate':'20260908T170000Z','language':'English','sourcecountry':'United States'},event_type='SANCTIONS',retrieved_at='2026-09-08T17:05:00Z'); assert e.event_type=='SANCTIONS'; assert e.source_tier=='TIER_C_SINGLE_SOURCE'; assert e.confirmed_at is None; assert e.retracted_at is None; assert e.entities==(); assert e.exposures==(); assert e.available_to_engine_at=='2026-09-08T17:05:00Z'; assert e.provenance_url=='https://news.example/story'
