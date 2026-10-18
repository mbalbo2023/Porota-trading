"""One old/new controlled SQL scope comparison; never contacts a host/provider."""
import hashlib,importlib.util,json,os,sqlite3,subprocess,time
from pathlib import Path
from unittest.mock import patch
root=Path('/workspace/scratch/88594b90c2a3');repo=root/'fix9-624';base='7d0c4533497700d94790bc70be7c8d8473da8c4b'
old_source=subprocess.check_output(['git','show',base+':scripts/rc6_candle_inmarket_evidence.py'],cwd=repo)
old_path=root/'candle_scope_old624.py';old_path.write_bytes(old_source)
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);o=importlib.util.module_from_spec(spec);spec.loader.exec_module(o);return o
old=module('candle_scope_old624',old_path);new=module('candle_scope_new624',repo/'scripts/rc6_candle_inmarket_evidence.py')
db=root/'candle-scope-shared624.db';assert not db.exists()
with sqlite3.connect(db) as c:
 c.executescript("CREATE TABLE candle_worker_state(id INTEGER PRIMARY KEY,cursor INTEGER,heartbeat_at TEXT,state TEXT); CREATE TABLE candle_dirty(series_id TEXT,bar_start TEXT,bar_end TEXT); CREATE INDEX candle_dirty_end ON candle_dirty(bar_end); CREATE TABLE unrelated_history(id INTEGER PRIMARY KEY,payload TEXT NOT NULL); INSERT INTO candle_worker_state VALUES(1,101,'2026-09-17T14:30:00+00:00','RUNNING'); INSERT INTO candle_dirty VALUES('x','2026-09-17T14:30:00+00:00','2026-09-17T14:31:00+00:00');")
 c.executemany('INSERT INTO unrelated_history(payload) VALUES(?)', [('unrelated'*32,)]*200000)
original=sqlite3.connect;before=hashlib.sha256(db.read_bytes()).hexdigest();inode=db.stat().st_ino;rows=[]
for label,code in [('baseline',old),('candidate',new)]:
 counters={'vm_steps':0};trace=[]
 class Counted(sqlite3.Connection):
  def set_progress_handler(self,callback,n):
   def measured():
    counters['vm_steps']+=n
    return callback() if callback is not None else 0
   return super().set_progress_handler(measured,n)
 def connect(*args,**kwargs):
  c=original(*args,factory=Counted,**kwargs);c.set_progress_handler(lambda:0,100);c.set_trace_callback(trace.append);return c
 fd=os.open(db,os.O_RDONLY)
 try:os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
 finally:os.close(fd)
 started=time.monotonic();cpu=time.process_time()
 with patch.object(code.sqlite3,'connect',connect):value=code.collect_inputs(db_path=str(db),observed_at='2026-09-17T14:30:30+00:00')
 rows.append({'arm':label,'cpu_seconds':time.process_time()-cpu,'wall_seconds':time.monotonic()-started,'vm_steps':counters['vm_steps'],'queries':trace,'value':value})
 assert db.stat().st_ino==inode and hashlib.sha256(db.read_bytes()).hexdigest()==before
business=set(rows[0]['value'])-{'quick_check'};assert all(rows[0]['value'][k]==rows[1]['value'][k] for k in business)
assert [x for x in rows[0]['queries'] if x.lower()=='pragma quick_check']==['PRAGMA quick_check']
assert set(x for x in rows[1]['queries'] if 'quick_check' in x.lower())=={"PRAGMA quick_check('candle_dirty')","PRAGMA quick_check('candle_worker_state')"}
result={'status':'GREEN_CONTROLLED_SQL_SCOPE_NOT_NATIVE_ACCEPTANCE','base_source_sha':base,'baseline_source_sha256':hashlib.sha256(old_source).hexdigest(),'candidate_source_sha256':hashlib.sha256((repo/'scripts/rc6_candle_inmarket_evidence.py').read_bytes()).hexdigest(),'fixture':{'path':str(db),'bytes':db.stat().st_size,'inode':inode,'sha256':before,'unrelated_rows':200000,'observed_at':'2026-09-17T14:30:30+00:00'},'same_physical_database_unchanged':True,'all_original_business_fields_equal':True,'explicit_metadata_delta':['quick_check:global_ok→DELEGATED_TO_RC6_FULL_DB_INTEGRITY','table_quick_checks:both_owned_btrees_ok'],'global_full_auditor_changed':False,'cpu_ratio':rows[1]['cpu_seconds']/rows[0]['cpu_seconds'],'vm_step_ratio':rows[1]['vm_steps']/max(1,rows[0]['vm_steps']),'rows':rows,'real_orders_sent':0,'provider_requests':0}
(root/'candle-scope-controlled624.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
