import ast, inspect
import pytest
import fh_forward_lab_v2_rc6 as m


def test_walk_forward_is_chronological_with_embargo():
    days=[f'2026-08-{d:02d}' for d in range(3,32)]
    folds=m.expanding_walk_forward(days,min_train_days=10,test_days=5,embargo_days=2,step_days=5)
    assert folds
    for f in folds:
        assert max(f.train_days)<min(f.embargo_days)<min(f.test_days)
        assert set(f.train_days).isdisjoint(f.test_days)


def test_random_or_duplicate_day_order_is_rejected():
    with pytest.raises(ValueError,match='NOT_CHRONOLOGICAL'):
        m.expanding_walk_forward(['2026-09-02','2026-09-01'],min_train_days=1,test_days=1,embargo_days=0)
    with pytest.raises(ValueError,match='DUPLICATED'):
        m.ordered_unique_days(['2026-09-01','2026-09-01'])


def test_temporal_samples_reject_lookahead_and_mixed_versions():
    base={'sample_id':'1','strategy_version':'v1','features_available_at':'2026-09-01T10:00:00-03:00','decision_at':'2026-09-01T10:01:00-03:00','label_available_at':'2026-09-01T11:00:00-03:00'}
    assert m.validate_temporal_samples([base])['lookahead_free']
    bad=dict(base,features_available_at='2026-09-01T10:02:00-03:00')
    with pytest.raises(ValueError,match='LOOKAHEAD'):
        m.validate_temporal_samples([bad])
    two=dict(base,sample_id='2',strategy_version='v2')
    with pytest.raises(ValueError,match='MIXED_STRATEGY'):
        m.validate_temporal_samples([base,two])


def test_leave_one_symbol_out_has_true_holdout():
    rows=[{'symbol':'GGAL','x':1},{'symbol':'YPFD','x':2},{'symbol':'GGAL','x':3}]
    cohorts=m.leave_one_symbol_out(rows)
    assert {c['held_out_symbol'] for c in cohorts}=={'GGAL','YPFD'}
    for c in cohorts:
        assert all(r['symbol']==c['held_out_symbol'] for r in c['test'])
        assert all(r['symbol']!=c['held_out_symbol'] for r in c['train'])
        assert c['promotion_allowed'] is False


def test_block_bootstrap_is_reproducible_and_contiguous_blocks():
    days=[f'2026-09-{d:02d}' for d in range(1,11)]
    a=m.trading_day_block_bootstrap(days,block_length=3,replications=4,seed=7)
    b=m.trading_day_block_bootstrap(days,block_length=3,replications=4,seed=7)
    assert a==b and all(len(x)==len(days) for x in a)


def test_manifest_is_reproducible_and_never_promotes():
    folds=m.expanding_walk_forward([f'2026-08-{d:02d}' for d in range(1,21)],min_train_days=10,test_days=3,embargo_days=1)
    a=m.manifest(strategy_version='v1',dataset_identity={'source':'PPI'},folds=folds,parameters={'x':1})
    b=m.manifest(strategy_version='v1',dataset_identity={'source':'PPI'},folds=folds,parameters={'x':1})
    assert a['manifest_sha256']==b['manifest_sha256']
    assert not a['promotion_allowed'] and not a['auto_promotion']


def test_module_has_no_network_db_broker_or_order_capability():
    m.assert_offline_only(); tree=ast.parse(inspect.getsource(m)); imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    assert not any(x in {'sqlite3','requests','httpx'} or 'broker' in x.lower() or 'order' in x.lower() for x in imports)
