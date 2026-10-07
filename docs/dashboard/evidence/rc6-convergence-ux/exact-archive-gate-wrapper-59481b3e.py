import argparse,hashlib,json,os,runpy,socket,sqlite3,sys
from pathlib import Path
from urllib.parse import unquote,urlsplit
p=argparse.ArgumentParser()
p.add_argument('--index',type=Path,required=True)
p.add_argument('--script',required=True)
p.add_argument('--proof',type=Path,required=True)
p.add_argument('arguments',nargs=argparse.REMAINDER)
a=p.parse_args()
sys.dont_write_bytecode=True
index=json.loads(a.index.read_text())
root=Path(index['extracted_root']).resolve()
script=(root/a.script).resolve()
assert script.is_relative_to(root) and script.is_file()
assert index.get('overlays')==[]
def hashes():
 return {str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(root.rglob('*')) if f.is_file()}
before=hashes()
assert before==index['source_file_hashes'], 'ARCHIVE_SOURCE_HASH_MISMATCH'
args=a.arguments[1:] if a.arguments[:1]==['--'] else a.arguments
source=Path(args[args.index('--database')+1]).resolve()
source_members={Path(str(source)+suffix) for suffix in ('','-wal','-shm','-journal')}
net=[]; source_calls=[]
original_connect=sqlite3.connect
def no_network(*args,**kwargs):
 net.append('BLOCKED_BEFORE_IMPORT_OR_DURING_GATE')
 raise AssertionError('NETWORK_FORBIDDEN_FOR_EXACT_ARCHIVE_GATE')
def private_connect(value,*args,**kwargs):
 raw=os.fsdecode(value)
 raw=unquote(urlsplit(raw).path) if raw.startswith('file:') else raw
 path=Path(raw).resolve() if raw!=':memory:' else None
 same=path in source_members if path is not None else False
 if path is not None and path.exists():
  same=same or any(member.exists() and path.samefile(member) for member in source_members)
 if same:
  source_calls.append('BLOCKED')
  raise AssertionError('SOURCE_SQLITE_FORBIDDEN_BEFORE_IMPORT_OR_DURING_GATE')
 return original_connect(value,*args,**kwargs)
socket.socket.connect=no_network
socket.create_connection=no_network
sqlite3.connect=private_connect
sys.argv=[str(script),*args]
exit_code=0
error=None
try:
 runpy.run_path(str(script),run_name='__main__')
except SystemExit as e:
 exit_code=e.code if isinstance(e.code,int) else int(e.code is not None)
except BaseException as e:
 exit_code=1; error=type(e).__name__
finally:
 closure=[]; unexpected=[]
 for name,module in sorted(sys.modules.items()):
  file=getattr(module,'__file__',None)
  if not file: continue
  path=Path(file).resolve()
  if path.is_relative_to(root):
   rel=str(path.relative_to(root))
   closure.append({'module':name,'path':rel,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'matches_archived_blob':index['source_file_hashes'].get(rel)==hashlib.sha256(path.read_bytes()).hexdigest()})
  elif str(path).startswith(('/workspace/porota_','/tmp/rc6_finance_core_original_')):
   unexpected.append({'module':name,'path':str(path)})
 after=hashes()
 good=(before==after and not unexpected and not net and not source_calls and all(m['matches_archived_blob'] for m in closure))
 proof={'schema':'rc6.exact-archive-gate-source-proof.v1','source_sha':index['source_sha'],'candidate_tree_sha':index['candidate_tree_sha'],'archive_sha256':index['archive_sha256'],'overlays':[],'script':a.script,'command_argv':sys.argv,'gate_exit_code':exit_code,'unexpected_error_class':error,'tracked_source_files':len(before),'tracked_source_hashes_unchanged':before==after,'imported_product_modules':closure,'unexpected_product_imports':unexpected,'network_attempts':len(net),'source_sqlite_attempts':len(source_calls),'source_proof_pass':good,'runner_wrapper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
 with a.proof.open('x') as stream: stream.write(json.dumps(proof,indent=2,sort_keys=True)+'\n')
 print(json.dumps({k:proof[k] for k in ('source_sha','gate_exit_code','tracked_source_files','tracked_source_hashes_unchanged','source_proof_pass')}))
 if not good: exit_code=1
sys.exit(exit_code)
