"""RC6 shadow History Store v3.

Purpose: remove two selector ambiguities before any secondary source can write
canonical history: completeness precedence and explicit RAW/ADJUSTED basis.
This module is intentionally parallel to v2; it does not migrate or delete v2.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

SOURCE_RANK={"PPI_PRODUCTION_HISTORY":10,"PPI_API":10,"BYMA_EOD":20,"BYMA":20,"A3_CEM_CLOSING":20,"IOL":30,"DATA912":50,"DATA912_POROTA_BATCH":50,"YAHOO":90}
DEFAULT_SOURCE_RANK=100
COMPLETENESS_RANK={"FULL_OHLCV":0,"FULL_OHLC":1,"CLOSE_ONLY":2}
PRICE_BASES={"RAW","ADJUSTED"}

DDL='''
CREATE TABLE IF NOT EXISTS history_versions_v3(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 symbol TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,settlement TEXT NOT NULL,date TEXT NOT NULL,
 price_basis TEXT NOT NULL,completeness TEXT NOT NULL,
 open REAL,high REAL,low REAL,close REAL NOT NULL,volume REAL,
 source TEXT NOT NULL,source_rank INTEGER NOT NULL,observed_at TEXT NOT NULL,payload_hash TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_history_versions_v3_evidence ON history_versions_v3(symbol,instrument_type,market,settlement,date,price_basis,source,payload_hash);
CREATE INDEX IF NOT EXISTS idx_history_versions_v3_identity ON history_versions_v3(symbol,instrument_type,market,settlement,date,price_basis,id DESC);
CREATE TABLE IF NOT EXISTS history_canonical_v3(
 symbol TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,settlement TEXT NOT NULL,date TEXT NOT NULL,
 price_basis TEXT NOT NULL,completeness TEXT NOT NULL,
 open REAL,high REAL,low REAL,close REAL NOT NULL,volume REAL,
 source TEXT NOT NULL,source_rank INTEGER NOT NULL,observed_at TEXT NOT NULL,version_id INTEGER NOT NULL,
 PRIMARY KEY(symbol,instrument_type,market,settlement,date,price_basis),
 FOREIGN KEY(version_id) REFERENCES history_versions_v3(id)
);
'''

def source_rank(source:str)->int:
 s=str(source or '').upper()
 for prefix,rank in SOURCE_RANK.items():
  if s.startswith(prefix): return rank
 return DEFAULT_SOURCE_RANK

def classify_completeness(o,h,l,c,v)->str:
 if c is None: raise ValueError('HISTORY_CLOSE_MISSING')
 ohl=(o is not None,h is not None,l is not None)
 if all(ohl): return 'FULL_OHLCV' if v is not None else 'FULL_OHLC'
 if not any(ohl) and v is None: return 'CLOSE_ONLY'
 raise ValueError('HISTORY_COMPLETENESS_UNSAFE')

@dataclass(frozen=True)
class CandleV3:
 symbol:str; instrument_type:str; market:str; settlement:str; date:str
 open:float|None; high:float|None; low:float|None; close:float; volume:float|None
 source:str; price_basis:str='RAW'; observed_at:str|None=None; metadata:dict|None=None
 def normalized(self):
  return CandleV3(str(self.symbol or '').strip().upper(),str(self.instrument_type or '').strip().upper(),str(self.market or '').strip().upper(),str(self.settlement or '').strip().upper(),str(self.date or '')[:10],None if self.open is None else float(self.open),None if self.high is None else float(self.high),None if self.low is None else float(self.low),float(self.close),None if self.volume is None else float(self.volume),str(self.source or '').strip().upper(),str(self.price_basis or '').strip().upper(),self.observed_at or datetime.now(timezone.utc).isoformat(),dict(self.metadata or {}))
 def validate(self):
  x=self.normalized()
  if not all((x.symbol,x.instrument_type,x.market,x.settlement,x.date,x.source)): raise ValueError('HISTORY_IDENTITY_INCOMPLETE')
  if x.market=='UNKNOWN' or x.settlement=='UNKNOWN': raise ValueError('HISTORY_IDENTITY_UNVERIFIED')
  if x.price_basis not in PRICE_BASES: raise ValueError('HISTORY_PRICE_BASIS_UNVERIFIED')
  comp=classify_completeness(x.open,x.high,x.low,x.close,x.volume)
  for name,val in [('close',x.close),('open',x.open),('high',x.high),('low',x.low)]:
   if val is not None and val<=0: raise ValueError('HISTORY_'+name.upper()+'_NONPOSITIVE')
  if x.volume is not None and x.volume<0: raise ValueError('HISTORY_VOLUME_NEGATIVE')
  if comp!='CLOSE_ONLY' and not x.low<=min(x.open,x.close)<=max(x.open,x.close)<=x.high: raise ValueError('HISTORY_OHLC_INCONSISTENT')
  return comp

def init_schema(store):
 with store.connect() as c: c.executescript(DDL)

def _json(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
def _hash(x:CandleV3,comp:str):
 p={'symbol':x.symbol,'instrument_type':x.instrument_type,'market':x.market,'settlement':x.settlement,'date':x.date,'price_basis':x.price_basis,'completeness':comp,'open':x.open,'high':x.high,'low':x.low,'close':x.close,'volume':x.volume,'source':x.source}
 return sha256(_json(p).encode()).hexdigest()
def _prefer(new:CandleV3,new_comp:str,current)->bool:
 if current is None:return True
 cur_comp=str(current['completeness'])
 if COMPLETENESS_RANK[new_comp]!=COMPLETENESS_RANK[cur_comp]: return COMPLETENESS_RANK[new_comp]<COMPLETENESS_RANK[cur_comp]
 nr=source_rank(new.source); cr=int(current['source_rank'])
 if nr!=cr:return nr<cr
 return str(new.observed_at)>=str(current['observed_at'])

def append_candle(store,candle:CandleV3):
 x=candle.normalized(); comp=x.validate(); init_schema(store); rank=source_rank(x.source); ph=_hash(x,comp)
 with store.connect() as c:
  dup=c.execute('''SELECT id FROM history_versions_v3 WHERE symbol=? AND instrument_type=? AND market=? AND settlement=? AND date=? AND price_basis=? AND source=? AND payload_hash=?''',(x.symbol,x.instrument_type,x.market,x.settlement,x.date,x.price_basis,x.source,ph)).fetchone()
  appended=dup is None
  if dup is None:
   cur=c.execute('''INSERT INTO history_versions_v3(symbol,instrument_type,market,settlement,date,price_basis,completeness,open,high,low,close,volume,source,source_rank,observed_at,payload_hash,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(x.symbol,x.instrument_type,x.market,x.settlement,x.date,x.price_basis,comp,x.open,x.high,x.low,x.close,x.volume,x.source,rank,x.observed_at,ph,_json(x.metadata or {})))
   vid=int(cur.lastrowid)
  else: vid=int(dup[0])
  current=c.execute('''SELECT * FROM history_canonical_v3 WHERE symbol=? AND instrument_type=? AND market=? AND settlement=? AND date=? AND price_basis=?''',(x.symbol,x.instrument_type,x.market,x.settlement,x.date,x.price_basis)).fetchone()
  chosen=_prefer(x,comp,current)
  if chosen:
   c.execute('''INSERT INTO history_canonical_v3(symbol,instrument_type,market,settlement,date,price_basis,completeness,open,high,low,close,volume,source,source_rank,observed_at,version_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,instrument_type,market,settlement,date,price_basis) DO UPDATE SET completeness=excluded.completeness,open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,source=excluded.source,source_rank=excluded.source_rank,observed_at=excluded.observed_at,version_id=excluded.version_id''',(x.symbol,x.instrument_type,x.market,x.settlement,x.date,x.price_basis,comp,x.open,x.high,x.low,x.close,x.volume,x.source,rank,x.observed_at,vid))
 return {'version_id':vid,'version_appended':appended,'canonical_updated':chosen,'completeness':comp,'price_basis':x.price_basis,'source_rank':rank,'payload_hash':ph}

def migrate_v2_row_shadow(row)->CandleV3:
 """Pure adapter for replay; does not write/delete v2."""
 return CandleV3(row['symbol'],row['instrument_type'],row['market'],row['settlement'],row['date'],row['open'],row['high'],row['low'],row['close'],row['volume'],row['source'],'ADJUSTED' if bool(row['adjusted']) else 'RAW',row['observed_at'],{'v2_version_id':row['id'] if 'id' in row.keys() else row['version_id'] if 'version_id' in row.keys() else None,'migration':'SHADOW'})
