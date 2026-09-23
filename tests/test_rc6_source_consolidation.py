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
