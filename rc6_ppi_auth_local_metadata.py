#!/usr/bin/env python3
"""Local-only metadata audit for the PPI web authentication profile.

Never reads credential values. Reports only existence/owner/mode/mtime and safe
browser cookie names/expiry metadata for relevant auth cookies, never values.
No network access is performed by this script.
"""
from __future__ import annotations
import argparse,json,os,sqlite3,stat
from pathlib import Path
from datetime import datetime,timezone,timedelta

RELEVANT={'fp','tk_ob','rtk_ob','tk_tr','rtk_tr'}

def fsmeta(path:Path):
 try:
  st=path.stat()
  import pwd,grp
  return {'exists':True,'owner':pwd.getpwuid(st.st_uid).pw_name,'group':grp.getgrgid(st.st_gid).gr_name,'mode':oct(stat.S_IMODE(st.st_mode))[2:],'mtime_utc':datetime.fromtimestamp(st.st_mtime,timezone.utc).isoformat(),'size_bytes':st.st_size}
 except Exception as e: return {'exists':False,'error_type':type(e).__name__}

def chrome_time(v):
 try:
  # Chrome microseconds since 1601-01-01 UTC
  return (datetime(1601,1,1,tzinfo=timezone.utc)+timedelta(microseconds=int(v))).isoformat()
 except Exception: return None

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--secret',required=True); a=ap.parse_args()
 p=Path(a.profile); secret=Path(a.secret)
 out={'status':'LOCAL_METADATA_ONLY','network_used':False,'credential_values_read':False,'cookie_values_read':False,'real_orders_sent':0,'secret':fsmeta(secret),'profile':fsmeta(p)}
 for name in ['Local State','Default/Preferences','Default/Cookies','Default/Network/Cookies']:
  q=p/name
  if q.exists(): out.setdefault('profile_files',{})[name]=fsmeta(q)
 cookie_db=None
 for q in [p/'Default/Network/Cookies',p/'Default/Cookies']:
  if q.is_file(): cookie_db=q; break
 cookies=[]
 if cookie_db:
  try:
   c=sqlite3.connect(f'file:{cookie_db}?mode=ro',uri=True,timeout=5); c.execute('pragma query_only=on')
   cols={r[1] for r in c.execute('pragma table_info(cookies)')}
   want=['name','host_key','path','creation_utc','expires_utc','last_access_utc','is_persistent']
   use=[x for x in want if x in cols]
   rows=c.execute('select '+','.join(use)+' from cookies where name in ('+','.join('?' for _ in RELEVANT)+')',tuple(sorted(RELEVANT))).fetchall()
   for row in rows:
    d=dict(zip(use,row))
    for k in ['creation_utc','expires_utc','last_access_utc']:
     if k in d: d[k]=chrome_time(d[k])
    cookies.append(d)
   c.close()
  except Exception as e: out['cookie_metadata_error']=type(e).__name__
 out['relevant_cookie_metadata']=cookies
 print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
