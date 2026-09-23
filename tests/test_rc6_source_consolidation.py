import rc6_source_consolidation as m

def test_consolidate_keeps_ppi_primary_and_compares_iol():
    result = m.consolidate(
        [{"family":"ACCIONES","symbol":"YPFD","market":"BCBA","term":"T1","last":100,"observed_at":"2026-09-23T12:00:00+00:00"}],
        [{"family":"ACCIONES","symbol":"YPFD","market":"BCBA","term":"T1","last":101,"bid":100,"ask":102,"currency":"ARS","provider_observed_at":"2026-09-23T12:00:01+00:00"}],
    )
    row = result["rows"][0]
    assert row["ppi_primary"]["last"] == 100
    assert row["iol_complement"]["last"] == 101
    assert row["comparison"]["last"]["state"] == "MATCH"
    assert row["decision_effect"] == "OBSERVE_ONLY"
    assert row["real_money_authorized"] is False

def test_html_public_page_is_reference_only():
    result = m.parse_public_payload("BYMA", "https://example.test", b"<title>BYMA</title>")
    assert result["status"] == "REFERENCE_ONLY"
    assert result["record_count"] == 0

def test_structured_public_payload_is_persistable_evidence():
    result = m.parse_public_payload("A3_MATBA_ROFEX", "https://example.test/data", b'{"items":[{"symbol":"DO","maturity":"2026-10-01"}]}')
    assert result["status"] == "REACHABLE_STRUCTURED"
    assert result["record_count"] == 1


def test_html_table_scrape_extracts_instrument_fields():
    html = b"""<table><tr><th>Especie</th><th>Moneda</th><th>P. Cpra.</th><th>P. Vta.</th><th>Ultimo</th><th>Volumen</th><th>Hora</th></tr>
    <tr><td>AAPL</td><td>ARS</td><td>26900</td><td>27500</td><td>27160</td><td>146796</td><td>06:46</td></tr></table>"""
    result = m.parse_public_payload("BYMA", "https://example.test", html)
    assert result["status"] == "SCRAPED_HTML_DATA"
    assert result["record_count"] == 1
    row = result["records"][0]
    assert row["symbol"] == "AAPL"
    assert row["bid"] == "26900"
    assert row["ask"] == "27500"
    assert row["last"] == "27160"
    assert row["volume"] == "146796"
