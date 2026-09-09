import rc6_dom_semantic_contract_importer as s


def _idx(*rows):
    return s.candidate_index(list(rows))


def test_exact_visible_identity_links_only_unique_financial_identity():
    idx = _idx({'ticker':'AAL','instrument_type':'CEDEARS','market':'BYMA','settlement':'CI'})
    match, state = s.match_candidate('CEDEARS', 'AAL', idx)
    assert state == 'UNIQUE_MATCH'
    assert match[1] == 'EXACT_VISIBLE_IDENTITY'
    assert match[2]['ticker'] == 'AAL'


def test_visible_family_prefix_can_be_removed_without_parsing_contract_fields():
    idx = _idx({'ticker':'2707','instrument_type':'BONOS','market':'BYMA','settlement':'CI'})
    match, state = s.match_candidate('BONOS', 'BON 2707', idx)
    assert state == 'UNIQUE_MATCH'
    assert match[1] == 'VISIBLE_FAMILY_PREFIX_REMOVED'
    assert match[2]['ticker'] == '2707'


def test_same_ticker_multiple_settlements_is_ambiguous_and_not_linked():
    idx = _idx(
        {'ticker':'AAL','instrument_type':'CEDEARS','market':'BYMA','settlement':'CI'},
        {'ticker':'AAL','instrument_type':'CEDEARS','market':'BYMA','settlement':'24HS'},
    )
    match, state = s.match_candidate('CEDEARS', 'AAL', idx)
    assert match is None
    assert state == 'AMBIGUOUS_MATCH'


def test_wrong_family_never_links_even_when_ticker_text_matches():
    idx = _idx({'ticker':'AAL','instrument_type':'ACCIONES','market':'BYMA','settlement':'CI'})
    match, state = s.match_candidate('CEDEARS', 'AAL', idx)
    assert match is None
    assert state == 'NO_MATCH'


def test_caucion_days_are_not_promoted_to_instrument_identity():
    headers = ['CANT. DE DÍAS','ÚLTIMO OPER.','FECHA VTO.']
    row = ['1 Pesos 1d','20,00 %','10/09/2026']
    h, identity = s.identity_from_row(headers, row, 'CAUCIONES')
    assert h is None and identity is None
    assert s.contract_pairs(headers, row) == [
        {'header':'CANT. DE DÍAS','value':'1 Pesos 1d'},
        {'header':'FECHA VTO.','value':'10/09/2026'},
    ]


def test_contract_values_keep_provider_header_names_without_canonical_inference():
    headers = ['ESPECIE','ÚLTIMO PRECIO','RATIO','Últimaact.']
    row = ['AAL','AR$ 10.290,00','2:1','12:00']
    assert s.contract_pairs(headers, row) == [{'header':'RATIO','value':'2:1'}]
