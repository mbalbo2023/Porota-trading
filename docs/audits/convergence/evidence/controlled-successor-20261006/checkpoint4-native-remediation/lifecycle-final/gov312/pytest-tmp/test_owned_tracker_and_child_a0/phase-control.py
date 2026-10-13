
import json, multiprocessing as mp, sys, time
from pathlib import Path
sys.path.insert(0,'/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source')
from scripts import rc6_controlled_governed_runner as r
initial=r.child_infrastructure_snapshot()
semaphore=mp.get_context('spawn').Semaphore(1)
child=mp.get_context('fork').Process(target=time.sleep,args=(.05,))
child.start()
result=r.finalize_child_infrastructure(initial)
Path(__file__).with_suffix('.finalization.json').write_text(json.dumps(result))
assert result['status']=='GREEN' and result['kernel_echild_before_phase_return']
