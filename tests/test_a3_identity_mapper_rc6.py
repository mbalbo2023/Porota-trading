import ast
import inspect
import pytest

import fd_a3_identity_mapper_rc6 as m


def test_sep26_roundtrip_exact():
    assert m.porota_to_a3_dlr('DLR/SEP26') == 'DLR092026'
    assert m.a3_to_porota_dlr('DLR092026') == 'DLR/SEP26'
    x=m.align_dlr(porota_symbol='DLR/SEP26',a3_symbol='DLR092026')
    assert x.expiry_year == 2026 and x.expiry_month == 9
    assert x.canonical_write == 'DENY'


def test_all_months_roundtrip():
    for month,code in m.MONTHS.items():
        p=f'DLR/{code}27'; a=f'DLR{month:02d}2027'
        assert m.porota_to_a3_dlr(p) == a
        assert m.a3_to_porota_dlr(a) == p


def test_mismatch_is_fail_closed():
    with pytest.raises(m.A3IdentityError,match='A3_EXPIRY_MISMATCH'):
        m.align_dlr(porota_symbol='DLR/SEP26',a3_symbol='DLR102026')


def test_unknown_product_is_not_guessed():
    r=m.alignment_or_unverified(porota_symbol='ORO/SEP26',a3_symbol='ORO092026')
    assert r['status'] == 'ALIGNMENT_UNVERIFIED'
    assert r['canonical_write'] == 'DENY'


def test_malformed_or_ambiguous_is_not_guessed():
    fixtures=[
        ('DLR/SE26','DLR092026'),('DLR/SEP26','DLR92026'),('DLR/XYZ26','DLR092026'),
        ('DLR/SEP2X','DLR092026'),('DLR/SEP26','DLR132026'),
    ]
    for p,a in fixtures:
        assert m.alignment_or_unverified(porota_symbol=p,a3_symbol=a)['status'] == 'ALIGNMENT_UNVERIFIED'


def test_module_has_no_network_db_or_order_capability():
    m.assert_shadow_only()
    tree=ast.parse(inspect.getsource(m)); imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    assert not any(x in {'sqlite3','requests','httpx'} or 'ppi' in x.lower() or 'order' in x.lower() for x in imports)
