"""Two own economic workers inside the one original managed 180-second epoch.

No new runner, dependency, deadline or qualification. The unchanged native
manager supervises this entire process group. JUnit testcase nodes are copied
from actual worker reports only; original worker XML and logs remain retained.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET


def require(value, reason):
    if not value: raise ValueError(reason)


def partition(suites):
    require(type(suites) is list and 0 < len(suites) <= 128
        and len(set(suites)) == len(suites)
        and all(type(s) is str and re.fullmatch(r'tests/test_[A-Za-z0-9_]+\.py(?:::[A-Za-z0-9_]+)?', s) for s in suites),
        'DEVELOPMENT_PARALLEL_EXACT_LITERAL_SELECTION_REQUIRED')
    modules = list(dict.fromkeys(s.split('::',1)[0] for s in suites))
    require(len(modules)>1,'DEVELOPMENT_PARALLEL_TWO_NONEMPTY_SOURCE_WORKERS_REQUIRED')
    buckets = {module: index % 2 for index,module in enumerate(modules)}
    shards = [[suite for suite in suites if buckets[suite.split('::',1)[0]] == index] for index in range(2)]
    require(set(shards[0]).isdisjoint(shards[1]) and set(shards[0]+shards[1])==set(suites),
        'DEVELOPMENT_PARALLEL_COMPLETE_DISJOINT_SELECTION_REQUIRED')
    return shards


def merge_junit(raws):
    require(type(raws) is list and len(raws)==2 and all(type(raw) is bytes and 0 < len(raw)<=16*1024**2 for raw in raws),
        'DEVELOPMENT_PARALLEL_TWO_ACTUAL_BOUNDED_JUNITS_REQUIRED')
    original=[]; suites=[]; ids=set()
    for raw in raws:
        root=ET.fromstring(raw)
        rows=[root] if root.tag=='testsuite' else list(root.findall('testsuite'))
        require(len(rows)==1,'DEVELOPMENT_PARALLEL_ORIGINAL_WORKER_SUITE_REQUIRED')
        suite=rows[0]; cases=suite.findall('testcase')
        require(cases,'DEVELOPMENT_PARALLEL_EMPTY_WORKER_REPORT_REFUSED')
        observed={'tests':len(cases), **{name:sum(case.find(tag) is not None for case in cases)
            for name,tag in (('failures','failure'),('errors','error'),('skipped','skipped'))}}
        require(all(suite.get(name)==str(value) for name,value in observed.items()),
            'DEVELOPMENT_PARALLEL_ORIGINAL_JUNIT_HEADERS_REBOUND')
        for case in cases:
            ident=(case.get('classname'),case.get('name'))
            require(all(type(v) is str and v for v in ident) and ident not in ids,
                'DEVELOPMENT_PARALLEL_DUPLICATE_OR_UNKNOWN_CASE_IDENTITY')
            ids.add(ident); original.append(case)
        suites.append(suite)
    merged=ET.Element('testsuite',name='pytest', **{'tests':str(len(original)),
        'failures':str(sum(case.find('failure') is not None for case in original)),
        'errors':str(sum(case.find('error') is not None for case in original)),
        'skipped':str(sum(case.find('skipped') is not None for case in original)),
        'time':str(sum(float(suite.get('time','0')) for suite in suites))})
    # Sorted actual identities make dual-epoch comparison independent of
    # scheduling; each testcase and every failure's original bytes are kept.
    for case in sorted(original,key=lambda c:(c.get('classname'),c.get('name'))): merged.append(case)
    return ET.tostring(merged,encoding='utf-8',xml_declaration=True)


def execute(source, namespace, suites):
    source, namespace=Path(source).absolute(),Path(namespace).absolute()
    info=namespace.lstat()
    require(os.getuid()==os.geteuid()>0 and info.st_uid==os.getuid()
        and stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700
        and not any(p.is_symlink() for p in (source,*source.parents,namespace,*namespace.parents))
        and not namespace.is_relative_to(source) and source.stat().st_dev==info.st_dev,
        'DEVELOPMENT_PARALLEL_ORIGINAL_OWN_EXTERNAL_NAMESPACE_REQUIRED')
    shards=partition(suites); children=[]; handles=[]
    try:
        for index,selected in enumerate(shards):
            root=namespace/('worker-'+str(index));root.mkdir(mode=0o700)
            log=root/'native.log';handle=log.open('xb');handles.append(handle)
            command=[sys.executable,'-B','-m','pytest','--noconftest','-c','/dev/null','--rootdir='+str(source),
                '-p','no:cacheprovider','-o','junit_family=legacy','--basetemp='+str(root/'p'),
                '--junitxml='+str(root/'junit.xml'),'-x','--tb=short','-q',*selected]
            # No separate timeout/reset/session/process group: both workers
            # remain under the one original outer manager's180-second window.
            children.append(subprocess.Popen(command,cwd=source,env=dict(os.environ),
                stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,close_fds=True))
        codes=[child.wait() for child in children]
    finally:
        for handle in handles:handle.close()
    raw_reports=[]
    for index in range(2):
        root=namespace/('worker-'+str(index))
        log=(root/'native.log').read_bytes()
        require(len(log)<=16*1024**2,'DEVELOPMENT_PARALLEL_ORIGINAL_LOG_BOUND')
        sys.stdout.buffer.write(b'ORIGINAL_ECONOMIC_WORKER_'+str(index).encode()+b'\n'+log)
        sys.stdout.buffer.flush()
        raw_reports.append((root/'junit.xml').read_bytes())
    raw=merge_junit(raw_reports)
    target=namespace/'junit.xml'
    with target.open('xb') as stream:stream.write(raw)
    return 0 if codes==[0,0] else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-root','namespace','plan'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args(argv)
    return execute(args.source_root,args.namespace,json.loads(args.plan.read_bytes())['suites'])


if __name__=='__main__': raise SystemExit(main())
