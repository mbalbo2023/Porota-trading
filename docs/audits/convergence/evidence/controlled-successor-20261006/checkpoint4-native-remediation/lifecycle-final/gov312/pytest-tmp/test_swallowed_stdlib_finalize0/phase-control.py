
import json, multiprocessing as mp, os, signal, sys, time
from multiprocessing import util
from pathlib import Path
sys.path.insert(0,'/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source')
from scripts import rc6_controlled_governed_runner as r
initial=r.child_infrastructure_snapshot()
child=mp.get_context('fork').Process(target=time.sleep,args=(30,))
child.start()
# This actual stdlib finalizer swallows the callback's exception. The signal
# must still be vetoed, recorded and RED independently of that exception.
finalizer=util.Finalize(child, os.kill, args=(child.pid,signal.SIGTERM), exitpriority=1)
result=r.finalize_child_infrastructure(initial)
os.kill(child.pid,0)  # A native liveness probe grants no signal authority.
alive=child.is_alive()
Path(__file__).with_suffix('.finalization.json').write_text(json.dumps({
    'result':result,'child_alive_after_veto':alive,'child_pid':child.pid,
    'finalizer_no_longer_active':not finalizer.still_active()}))
assert result['status']=='RED' and alive
# Do not let atexit's unbounded child join hide this RED. The unchanged native
# parent observes and cleans only its own process group as a failed phase.
os._exit(0)
