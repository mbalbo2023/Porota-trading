"""Missing data shares storage only after unchanged source validation."""
import copy
import json

import pytest

from rc6_dynamic_universe.common import digest
from rc6_dynamic_universe.runtime import read_runtime
from rc6_shadow_runtime import families
from rc6_shadow_runtime.persistence import FrozenJSONDict, _canonical_chunks, _small_json_tree, _small_json_tree_python
from rc6_shadow_runtime.stages import enrich_pipeline
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.test_rc6_shadow_runtime_wiring import make_store, PRE


def test_missing_quotes_share_one_immutable_view_with_exact_public_output(tmp_path):
    store,_=make_store(tmp_path,count=4)
    catalog=read_runtime(store.path,as_of=PRE)['full_catalog']
    public=families.family_reports(store.path,as_of=PRE,catalog=catalog)
    compact=families.family_reports(store.path,as_of=PRE,catalog=catalog,compact_missing=True)
    assert digest(public)==digest(compact)
    quotes=[r['handler_result']['quote'] for r in compact['instruments'] if 'quote' in (r.get('handler_result') or {})]
    assert len(quotes)>1 and len({id(q) for q in quotes})==1
    quote=quotes[0]
    for value in (quote,quote['field_provenance'],quote['field_provenance']['price']):
        with pytest.raises(TypeError):value['unverified']='forged'
    assert _small_json_tree(quote)==_small_json_tree_python(quote)
    # Encoding always observes actual values, even if a caller bypasses the
    # private immutability API. An encoding hint is never content authority.
    dict.__setitem__(quote,'status','BYPASS_MUST_STILL_ENCODE')
    encoder=json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False)
    assert b''.join(_canonical_chunks(encoder,quote))==encoder.encode(quote).encode()


def test_existing_quote_rows_are_never_replaced_by_cached_absence():
    cache={};missing=families._quote(families._FieldRows([],cache),PRE)
    raw={'last':100,'source':'PPI','source_at':PRE.isoformat(),
         'received_at':PRE.isoformat(),'observed_at':PRE.isoformat(),'price_unit':'PER_SHARE'}
    for row in (raw,{**raw,'source_at':'2020-01-01T00:00:00+00:00'},
                {**raw,'source_at':'2099-01-01T00:00:00+00:00'}):
        compact=families._quote(families._FieldRows([row],cache),PRE)
        public=families._quote(families._FieldRows([row]),PRE)
        assert compact is not missing and digest(compact)==digest(public)


def test_pipeline_missing_defaults_are_exact_immutable_and_scoped_to_one_pass(tmp_path):
    store,_=make_store(tmp_path,count=4)
    report=ShadowRuntime(store.path,evidence_root=tmp_path/'shadow',source_roots=[]).tick(PRE)
    public=enrich_pipeline(store.path,copy.deepcopy(report),as_of=PRE)
    compact=enrich_pipeline(store.path,copy.deepcopy(report),as_of=PRE,compact_missing=True)
    later=enrich_pipeline(store.path,copy.deepcopy(report),as_of=PRE,compact_missing=True)
    assert digest(public)==digest(compact)==digest(later)
    rows=[r for p in compact['engines'].values() for r in p['telemetry']]
    others=[r for p in later['engines'].values() for r in p['telemetry']]
    for key in ('signal_shadow','economics_shadow','risk_shadow'):
        assert len({id(r[key]) for r in rows})==1
        assert rows[0][key] is not others[0][key]
        with pytest.raises(TypeError):rows[0][key]['status']='READY'
    with pytest.raises(TypeError):rows[0]['economics_shadow']['real_order_routes'].append('REAL')
    first=next(iter(public['engines'].values()))['telemetry']
    first[0]['economics_shadow']['real_order_routes'].append('isolated-test-value')
    assert first[1]['economics_shadow']['real_order_routes']==[]


def test_nested_metadata_freeze_rejects_unbounded_or_mutable_leaves():
    with pytest.raises(TypeError):FrozenJSONDict._from_owned_metadata_tree({'rows':[1]})
    with pytest.raises(TypeError):FrozenJSONDict._from_owned_metadata_tree({'bad':object()})
    with pytest.raises(ValueError):FrozenJSONDict._from_owned_metadata_tree({str(n):None for n in range(300)})
