import rc6_dom_coverage_reconciler as r

def ids(n): return [f'X{i}' for i in range(n)]

def test_dom_50_vs_expected_193_proves_truncation():
    x=r.reconcile(family='CEDEARs',dom_ids=ids(50),expected_ids=ids(193),expected_source='candidate_universe:PPI_PRODUCTION_CATALOG')
    assert x.family=='CEDEARS' and x.status=='PARTIAL_TRUNCATED' and x.missing==143 and not x.complete_proven

def test_options_43_unique_vs_383_expected_is_partial():
    x=r.reconcile(family='Opciones',dom_ids=ids(43),expected_ids=ids(383),expected_source='candidate_universe')
    assert x.status=='PARTIAL_TRUNCATED' and x.missing==340

def test_less_than_50_without_expected_never_becomes_complete():
    x=r.reconcile(family='Letras',dom_ids=ids(30),expected_ids=None)
    assert x.status=='PARTIAL_UNKNOWN_EXPECTED' and not x.complete_proven and r.readiness(x)=='COVERAGE_GRAY'

def test_exact_identity_set_proves_complete_with_provenance():
    x=r.reconcile(family='Acciones',dom_ids=['GGAL','YPFD'],expected_ids=['YPFD','GGAL'],expected_source='api-catalog-run:abc')
    assert x.status=='COMPLETE_PROVEN' and x.complete_proven and x.expected_source=='api-catalog-run:abc'

def test_dom_extra_blocks_complete():
    x=r.reconcile(family='ETF',dom_ids=['A','B'],expected_ids=['A'],expected_source='catalog')
    assert x.status=='DOM_EXTRA_IDENTITY_MISMATCH' and not x.complete_proven

def test_expected_universe_requires_provenance():
    try:r.reconcile(family='Bonos',dom_ids=['A'],expected_ids=['A'])
    except ValueError as e: assert 'EXPECTED_SOURCE_REQUIRED' in str(e)
    else: raise AssertionError('expected provenance failure')
