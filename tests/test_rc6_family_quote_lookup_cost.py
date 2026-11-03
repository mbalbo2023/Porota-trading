"""Already resolved quotes must not evaluate unused source fallbacks."""
from rc6_shadow_runtime import families
from tests.test_rc6_shadow_family_runtime import AT, fixture_db, instrument, snapshot


def test_present_identity_avoids_fallback_and_preserves_complete_report(tmp_path, monkeypatch):
    catalog = [instrument('S' + str(n), 'ACCIONES') for n in range(25)]
    path = fixture_db(tmp_path, catalog)
    snapshot(path, catalog[0])
    before = families.family_reports(path, as_of=AT, catalog=catalog)
    original = families._quote
    unused = []
    def observed(rows, at):
        if not rows:
            unused.append(1)
        return original(rows, at)
    monkeypatch.setattr(families, '_quote', observed)
    after = families.family_reports(path, as_of=AT, catalog=catalog)
    assert after == before
    assert unused == []


def test_unused_quote_families_do_not_build_equity_books(tmp_path, monkeypatch):
    catalog = [instrument('FUND', 'FCI'), instrument('FUT', 'FUTUROS'), instrument('CASH', 'CAUCIONES')]
    path = fixture_db(tmp_path, catalog)
    calls = []
    original = families._quote
    def observed(rows, at):
        calls.append(1)
        return original(rows, at)
    monkeypatch.setattr(families, '_quote', observed)
    report = families.family_reports(path, as_of=AT, catalog=catalog)
    assert len(report['instruments']) == 3 and report['catalog_preserved']
    assert calls == []


def test_prepared_views_preserve_each_field_origin_clock_conflict_and_alias(monkeypatch):
    from datetime import timedelta
    from copy import deepcopy
    from tests.test_rc6_shadow_family_runtime import clocked
    rows = [{
        'source': 'PPI', 'source_at': AT.isoformat(), 'received_at': AT.isoformat(),
        'metadata': {'metadata_source': 'IOL', 'source_at': AT.isoformat(),
                     'ratio': clocked(10, source='IOL'), 'conversion_ratio': clocked(20, source='BYMA'),
                     'yield_to_maturity': clocked(.02), 'strike_price': clocked(100),
                     'maturity_at': clocked((AT + timedelta(days=30)).isoformat()),
                     'flows': clocked([{'payment_at': (AT + timedelta(days=20)).isoformat(),
                                        'amount': 10, 'currency': 'ARS'}]),
                     'iv': clocked(.25, received_at=AT + timedelta(seconds=1)),
                     'nav': clocked(10, source_at=AT - timedelta(days=1))}},
        {'source': 'BYMA', 'source_at': AT.isoformat(), 'received_at': AT.isoformat(),
         'ratio': 20, 'price': 110, 'price_unit': 'PER_SHARE'},
        {'source': 'PPI', 'source_at': AT.isoformat(), 'received_at': AT.isoformat(),
         'price': 100, 'price_unit': 'PER_NOMINAL'}]
    before = deepcopy(rows)
    names = list(families._ALIASES) + ['underlying', 'option_right', 'greeks']
    expected = {n: families._field(n, rows, at=AT, numeric=n not in {'flows','curve','maturity_at','underlying','option_right','greeks'}) for n in names}
    calls = []
    original = families._views
    def observed(row):
        calls.append(1)
        yield from original(row)
    monkeypatch.setattr(families, '_views', observed)
    prepared = families._FieldRows(rows)
    flattened_calls = len(calls)
    actual = {n: families._field(n, prepared, at=AT, numeric=n not in {'flows','curve','maturity_at','underlying','option_right','greeks'}) for n in names}
    assert actual == expected
    assert rows == before
    assert len(calls) == flattened_calls
    assert actual['price']['review_required']
    assert actual['iv']['value'] is None


def test_private_missing_fields_preserve_full_report_and_prevent_shared_mutation(tmp_path):
    import json
    import pytest
    from tests.test_rc6_shadow_family_runtime import source_row, clocked
    catalog = [instrument('S'+str(n), family) for n,family in enumerate(
        ['ACCIONES','CEDEARS','BONOS','LETRAS','OBLIGACIONES','OPCIONES','FUTUROS','CAUCIONES','FCI'])]
    path = fixture_db(tmp_path,catalog)
    snapshot(path,catalog[0])
    sources={'family_evidence':[source_row(catalog[1],ratio=clocked(10)),
             source_row(catalog[2],yield_to_maturity=clocked(.05))]}
    expected=families.family_reports(path,as_of=AT,catalog=catalog,sources=sources)
    actual=families.family_reports(path,as_of=AT,catalog=catalog,sources=sources,compact_missing=True)
    assert json.dumps(actual,sort_keys=True)==json.dumps(expected,sort_keys=True)
    missing=actual['instruments'][0]['handler_result']['quote']['field_provenance']['volume']
    assert missing['input_count']==0 and missing['candidate_count']==0
    for mutate in (lambda:missing.update(value=99), lambda:missing.__setitem__('value',99),
                   lambda:missing['candidates'].append({'value':99}),lambda:missing.clear(),
                   lambda:missing['conflicts'].__iadd__([1]),lambda:missing.setdefault('x',99)):
        with pytest.raises(TypeError):mutate()
    assert json.dumps(actual,sort_keys=True)==json.dumps(expected,sort_keys=True)
