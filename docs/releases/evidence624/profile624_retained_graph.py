"""One local allocation attribution; instrumentation invalidates timing/RSS comparisons."""
from collections import Counter
from datetime import timedelta
import gc
import hashlib
import json
from pathlib import Path
import sys

root = Path('fix2-624').resolve()
sys.path.insert(0, str(root))
from tests.test_rc6_shadow_runtime_wiring import PRE
from rc6_shadow_runtime.persistence import EvidenceFiles, _native_json_bound
from rc6_shadow_runtime.worker import ShadowRuntime, release_idle_heap

assert _native_json_bound is not None
assert Path(sys.modules['rc6_shadow_runtime.worker'].__file__).resolve().is_relative_to(root)
database = Path('shared624.db').resolve()
output = Path('allocation-202e624')
output.mkdir(exist_ok=False)
database_hash = hashlib.sha256(database.read_bytes()).hexdigest()
original = EvidenceFiles.write_streamed_report
receipts = []


def size_graph(value):
    seen = set()
    pending = [value]
    totals, counts = Counter(), Counter()
    while pending:
        item = pending.pop()
        ident = id(item)
        if ident in seen:
            continue
        seen.add(ident)
        name = type(item).__name__
        totals[name] += sys.getsizeof(item)
        counts[name] += 1
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
            if hasattr(item, '_canonical_json'):
                pending.append(item._canonical_json)
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return {'retained_bytes': sum(totals.values()), 'bytes_by_type': dict(totals),
            'objects_by_type': dict(counts)}


def measured_write(self, report, **kwargs):
    row = {'as_of': report['as_of'], 'whole_report': size_graph(report),
           'sections': {}, 'engines': {}}
    for name in ('frozen', 'tradeability', 'catalog_ready', 'family_routing',
                 'operational_funnel', 'economic_exit_lab', 'entry_signal_lab'):
        if name in report:
            row['sections'][name] = size_graph(report[name])
    for engine, plan in report.get('engines', {}).items():
        row['engines'][engine] = {name: size_graph(plan[name]) for name in
            ('instruments', 'telemetry') if name in plan}
    receipts.append(row)
    (output/'retained-graphs.json').write_text(json.dumps({
        'schema': 'rc6.issue624.local-retained-graph-attribution.v1',
        'source_sha': '202e49c750ab0a69b5ba6790fcb14cc0c0d9039f',
        'source_tree': 'f3ddca733e40649c4e2605f69d5b7ec59dd839e8',
        'database_sha256': database_hash, 'database_inode': database.stat().st_ino,
        'timing_or_peak_rss_comparable': False,
        'limitations': ['Local synthetic fixture, not a native heap snapshot.',
                        'Sections share values; section byte totals must not be added.',
                        'Instrumentation creates a traversal set; no speed or RSS claim.'],
        'cuts': receipts}, indent=2)+'\n')
    print(json.dumps({'as_of': row['as_of'], 'whole_report_bytes': row['whole_report']['retained_bytes'],
                      'section_bytes': {k:v['retained_bytes'] for k,v in row['sections'].items()},
                      'engine_bytes': {k:{n:v['retained_bytes'] for n,v in values.items()}
                                       for k,values in row['engines'].items()}}), flush=True)
    gc.collect()
    release_idle_heap()
    return original(self, report, **kwargs)


EvidenceFiles.write_streamed_report = measured_write
worker = ShadowRuntime(database, evidence_root=output/'shadow', source_roots=[])
for offset in (0, 30):
    report = worker.tick(PRE+timedelta(seconds=offset))
    del report
    release_idle_heap()
assert database_hash == hashlib.sha256(database.read_bytes()).hexdigest()
