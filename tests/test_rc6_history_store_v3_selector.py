import sqlite3
from contextlib import contextmanager
import pytest
from fh_history_store_v3_rc6 import CandleV3,append_candle,init_schema

class Store:
 def __init__(self,p): self.p=str(p)
 @contextmanager
 def connect(self):
  c=sqlite3.connect(self.p); c.row_factory=sqlite3.Row
  try: yield c; c.commit()
  finally: c.close()

def row(store,basis='RAW',settlement='INMEDIATA'):
 with store.connect() as c:
  return c.execute("select * from history_canonical_v3 where symbol='GGAL' and instrument_type='ACCIONES' and market='BYMA' and settlement=? and date='2026-09-07' and price_basis=?",(settlement,basis)).fetchone()

def c(source='IOL',o=100,h=110,l=95,cl=105,v=1000,basis='RAW',sett='INMEDIATA',obs='2026-09-08T00:00:00+00:00'):
 return CandleV3('GGAL','ACCIONES','BYMA',sett,'2026-09-07',o,h,l,cl,v,source,basis,obs)

def test_full_ohlcv_beats_higher_rank_close_only(tmp_path):
 s=Store(tmp_path/'h.db'); append_candle(s,c(source='IOL')); append_candle(s,c(source='A3_CEM_CLOSING',o=None,h=None,l=None,cl=106,v=None,obs='2026-09-08T01:00:00+00:00'))
 r=row(s); assert r['source']=='IOL' and r['completeness']=='FULL_OHLCV'

def test_full_ohlcv_can_replace_close_only_even_lower_source_authority(tmp_path):
 s=Store(tmp_path/'h.db'); append_candle(s,c(source='A3_CEM_CLOSING',o=None,h=None,l=None,cl=106,v=None)); append_candle(s,c(source='IOL',obs='2026-09-08T01:00:00+00:00'))
 r=row(s); assert r['source']=='IOL' and r['completeness']=='FULL_OHLCV'

def test_source_rank_breaks_tie_only_after_completeness(tmp_path):
 s=Store(tmp_path/'h.db'); append_candle(s,c(source='IOL')); append_candle(s,c(source='PPI_PRODUCTION_HISTORY',cl=104,obs='2026-09-08T01:00:00+00:00'))
 assert row(s)['source']=='PPI_PRODUCTION_HISTORY'

def test_raw_and_adjusted_are_distinct_canonical_series(tmp_path):
 s=Store(tmp_path/'h.db'); append_candle(s,c(source='PPI_API',basis='RAW')); append_candle(s,c(source='PPI_API',basis='ADJUSTED',cl=52,o=50,h=55,l=47.5,v=1000))
 with s.connect() as db:
  rs=db.execute("select price_basis,close from history_canonical_v3 order by price_basis").fetchall()
 assert [(x['price_basis'],x['close']) for x in rs]==[('ADJUSTED',52.0),('RAW',105.0)]

def test_settlement_identity_is_separate(tmp_path):
 s=Store(tmp_path/'h.db'); append_candle(s,c(sett='INMEDIATA')); append_candle(s,c(sett='A-24HS',cl=107,obs='2026-09-08T01:00:00+00:00'))
 with s.connect() as db: assert db.execute('select count(*) from history_canonical_v3').fetchone()[0]==2

def test_unknown_identity_fails_closed(tmp_path):
 s=Store(tmp_path/'h.db')
 with pytest.raises(ValueError,match='HISTORY_IDENTITY_UNVERIFIED'): append_candle(s,c(sett='UNKNOWN'))

def test_partial_ohlc_is_not_canonicalizable(tmp_path):
 s=Store(tmp_path/'h.db')
 with pytest.raises(ValueError,match='HISTORY_COMPLETENESS_UNSAFE'): append_candle(s,c(o=100,h=None,l=None))

def test_unknown_price_basis_fails_closed(tmp_path):
 s=Store(tmp_path/'h.db')
 with pytest.raises(ValueError,match='HISTORY_PRICE_BASIS_UNVERIFIED'): append_candle(s,c(basis='UNKNOWN'))

def test_duplicate_evidence_does_not_grow_versions(tmp_path):
 s=Store(tmp_path/'h.db'); x=c(); a=append_candle(s,x); b=append_candle(s,x)
 assert a['version_appended'] is True and b['version_appended'] is False
 with s.connect() as db: assert db.execute('select count(*) from history_versions_v3').fetchone()[0]==1
