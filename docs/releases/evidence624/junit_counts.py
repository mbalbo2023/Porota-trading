"""Report actual testcase elements; preserve summary attributes separately."""
import gzip,json,sys,xml.etree.ElementTree as ET
from pathlib import Path

def measured(path):
    raw=path.read_bytes()
    root=ET.fromstring(gzip.decompress(raw) if path.suffix=='.gz' else raw)
    cases=list(root.iter('testcase'))
    return {'path':str(path),'actual_testcases':len(cases),
            'summary_attributes':[suite.attrib for suite in root.iter('testsuite')],
            **{kind:sum(len(list(case.iter(kind))) for case in cases)
               for kind in ('failure','error','skipped')}}

if __name__=='__main__':print(json.dumps([measured(Path(p)) for p in sys.argv[1:]],indent=2))
