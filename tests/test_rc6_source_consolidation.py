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


def test_consolidate_records_cascade_provenance():
    result = m.consolidate(
        [{"family":"ACCIONES","symbol":"YPFD","market":"BCBA","term":"T1","last":100}],
        [{"family":"ACCIONES","symbol":"YPFD","market":"BCBA","term":"T1","bid":99,"ask":101}],
        [{"family":"ACCIONES","symbol":"YPFD","market":"BCBA","term":"T1","vwap":100.5}],
    )
    fields = result["rows"][0]["effective_fields"]
    assert fields["last"]["source"] == "PPI"
    assert fields["bid"] == {"value": 99.0, "source": "IOL", "ppi": None, "iol": 99.0, "byma": None}
    assert fields["vwap"]["source"] == "BYMA"
    assert result["rows"][0]["decision_effect"] == "OBSERVE_ONLY"
    assert result["rows"][0]["shadow_promotion"] is True

def test_html_public_page_is_reference_only():
    result = m.parse_public_payload("BYMA", "https://example.test", b"<title>BYMA</title>")
    assert result["status"] == "REFERENCE_ONLY"
    assert result["record_count"] == 0

def test_structured_public_payload_is_persistable_evidence():
    result = m.parse_public_payload("A3_MATBA_ROFEX", "https://example.test/data", b'{"items":[{"symbol":"DO","maturity":"2026-10-01"}]}')
    assert result["status"] == "REACHABLE_STRUCTURED_DATA"
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


def test_bymadata_json_payload_normalizes_live_fields():
    payload = b'{"data":[{"symbol":"AAPL","bidPrice":26900,"offerPrice":27500,"tradePrice":27160,"tradeVolume":146796,"vwap":27272.378,"tradeDate":"2026-09-23T19:00:00Z"}]}'
    result = m.parse_public_payload("BYMA", "https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/cedears", payload)
    assert result["status"] == "REACHABLE_STRUCTURED_DATA"
    assert result["scrape_method"] == "json"
    assert result["record_count"] == 1
    row = result["records"][0]
    assert row["symbol"] == "AAPL"
    assert row["bid"] == 26900
    assert row["ask"] == 27500
    assert row["last"] == 27160
    assert row["volume"] == 146796
    assert row["vwap"] == 27272.378
    assert row["timestamp"] == "2026-09-23T19:00:00Z"


def test_bymadata_public_collection_merges_read_only_panels(monkeypatch):
    def fake_post(_base_url, endpoint, family):
        symbol = "AAPL" if endpoint == "cedears" else "BBAR"
        return {
            "source": "BYMA",
            "endpoint": endpoint,
            "status": "REACHABLE_STRUCTURED_DATA",
            "http_status": 200,
            "record_count": 1,
            "records": [{"source": "BYMA", "family": family, "market": "BYMA", "symbol": symbol, "currency": "ARS", "last": "27160"}],
        }

    monkeypatch.setattr(m, "_byma_post", fake_post)
    result = m._collect_byma_public("https://open.bymadata.com.ar/")
    assert result["status"] == "SCRAPED_PUBLIC_DATA"
    assert result["scrape_method"] == "bymadata_public_post"
    assert result["record_count"] == 6
    assert {item["endpoint"] for item in result["endpoints"]} == {
        "leading-equity", "cedears", "public-bonds", "negociable-obligations", "cauciones", "options"
    }
    assert result["errors"] == []


def test_complete_multi_source_contract_is_paper_shadow_ready():
    import cp_contract_evidence_v2_hf6 as evidence

    fields = {
        "instrument_id": "YPFD",
        "ticker": "YPFD",
        "market": "BYMA",
        "currency": "ARS",
        "settlement": "T1",
        "quantity_min": 1,
        "quantity_step": 1,
        "price_precision": 2,
        "cost_model": "PAPER",
    }
    result = evidence.family_readiness_state(
        [{"source_class": "PPI_STRUCTURED_API", "observed_at": "2026-09-24T12:00:00+00:00",
          "evidence": fields},
         {"source_class": "PPI_AUTHENTICATED_WEB", "observed_at": "2026-09-24T12:00:01+00:00",
          "evidence": {"bid": 100, "ask": 101}}],
        family="ACCIONES",
        max_age_seconds=3600,
        now=__import__("datetime").datetime.fromisoformat("2026-09-24T12:01:00+00:00"),
    )
    assert result["status"] == "READY_PAPER_SHADOW"
    assert result["paper_auto_enabled"] is True
    assert result["real_money_authorized"] is False


def test_consolidate_unions_complementary_identity_and_canonicalizes_bcba_t1():
    result = m.consolidate(
        [],
        [{"asset_type":"BONOS","symbol":"GD30","market":"BCBA","term":"T1",
          "last":87530,"provider_observed_at":"2026-09-25T15:00:00+00:00"}],
        [{"family":"BONOS","symbol":"GD30","market":"BYMA","term":"A-24HS",
          "vwap":87400,"provider_observed_at":"2026-09-25T15:00:00+00:00"}],
    )
    assert len(result["rows"]) == 1
    row=result["rows"][0]
    assert row["identity"] == {"family":"BONOS","symbol":"GD30","market":"BYMA","term":"A-24HS"}
    assert row["effective_fields"]["last"]["source"] == "IOL"
    assert row["effective_fields"]["vwap"]["source"] == "BYMA"
    assert result["source_order"] == "PPI_PRIMARY_IOL_COMPLEMENTARY_BYMA_PUBLIC_COMPLEMENTARY"
