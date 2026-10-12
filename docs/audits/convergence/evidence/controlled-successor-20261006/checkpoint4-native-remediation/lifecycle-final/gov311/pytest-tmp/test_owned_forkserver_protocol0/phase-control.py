
import json,multiprocessing as mp,sys,time
from pathlib import Path
sys.path.insert(0,'/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source')
from scripts import rc6_controlled_governed_runner as r
if __name__=='__main__':
    r.phase_namespace(Path('/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source'), Path(__file__).parent, 'execution')
    initial=r.child_infrastructure_snapshot()
    process=mp.get_context('forkserver').Process(target=time.sleep,args=(.05,))
    process.start();process.join(3)
    assert process.exitcode==0
    result=r.finalize_child_infrastructure(initial)
    Path(__file__).with_suffix('.finalization.json').write_text(json.dumps(result))
    assert result['status']=='GREEN'
