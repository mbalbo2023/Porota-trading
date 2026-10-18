import subprocess,sys,resource,json,hashlib
from pathlib import Path
probe="import resource,json; from pathlib import Path; print(json.dumps({'ru_maxrss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'proc':{x.split(':')[0]:int(x.split()[1]) for x in Path('/proc/self/status').read_text().splitlines() if x.startswith(('VmRSS:','VmHWM:'))}}))"
heavy=bytearray(240*1024**2)
parent=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
direct=json.loads(subprocess.check_output([sys.executable,'-c',probe],text=True))
launcher='import subprocess,sys;subprocess.run([sys.executable,"-c",'+repr(probe)+'],check=True)'
clean=json.loads(subprocess.check_output([sys.executable,'-c',launcher],text=True))
result={'parent_ru_maxrss_kib':parent,'direct_child':direct,'via_clean_stdlib_launcher':clean,'kernel':'Linux','alloc_mib':240,'physical_vs_inherited':direct['ru_maxrss_kib']>240*1024 and direct['proc']['VmHWM']<32*1024,'clean_child_is_measured':clean['ru_maxrss_kib']<32*1024}
assert result['physical_vs_inherited'] and result['clean_child_is_measured'],result
Path('inherited-hwm624.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
