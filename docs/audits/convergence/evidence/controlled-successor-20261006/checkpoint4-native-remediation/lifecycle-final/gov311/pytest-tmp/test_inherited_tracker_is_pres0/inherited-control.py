
import json, multiprocessing as mp, os, sys
from pathlib import Path
sys.path.insert(0,'/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source')
from scripts import rc6_controlled_governed_runner as r

def inherited(output):
    initial=r.child_infrastructure_snapshot()
    result=r.finalize_child_infrastructure(initial)
    after=r.child_infrastructure_snapshot()
    Path(output).write_text(json.dumps({'initial':initial,'result':result,'after':after}))

if __name__=='__main__':
    r.phase_namespace('/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source', Path(__file__).parent, 'execution')
    initial=r.child_infrastructure_snapshot()
    context=mp.get_context('spawn'); semaphore=context.Semaphore(1)
    owned=r.child_infrastructure_snapshot()['resource_tracker_pid']
    child=context.Process(target=inherited,args=(sys.argv[1],));child.start();child.join(5)
    assert child.exitcode==0
    os.kill(owned,0)
    assert semaphore.acquire(timeout=1);semaphore.release()
    result=r.finalize_child_infrastructure(initial)
    print(json.dumps({'parent_tracker_stayed_live_and_usable':True,'parent_finalization':result}))
