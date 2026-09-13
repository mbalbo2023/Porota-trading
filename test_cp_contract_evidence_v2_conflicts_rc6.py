import cp_contract_evidence_v2_hf6 as ce


def rec(source, evidence):
    return {"source_class": source, "evidence": evidence}


def test_collection_metadata_differences_are_not_financial_conflicts():
    rows = [
        rec("PPI_AUTHENTICATED_XHR", {
            "readiness_guard": "NO_AUTO_ACTIVATION",
            "source_route": "/api/xhr",
            "source_job": "OPERABILITY",
            "currency": "ARS",
        }),
        rec("PPI_AUTHENTICATED_WEB", {
            "readiness_guard": "WEB_OBSERVATION_ONLY",
            "source_route": "/Cotizaciones/Bonos",
            "source_job": "STATIC_CONTRACTS",
            "currency": "ARS",
        }),
    ]
    assert ce.source_conflict(rows) == {}


def test_real_financial_difference_remains_conflict():
    rows = [
        rec("PPI_AUTHENTICATED_XHR", {"settlement": "24HS", "currency": "ARS"}),
        rec("PPI_AUTHENTICATED_WEB", {"settlement": "CI", "currency": "ARS"}),
    ]
    conflicts = ce.source_conflict(rows)
    assert "settlement" in conflicts
    assert "currency" not in conflicts


def test_same_financial_value_from_multiple_sources_is_not_conflict():
    rows = [
        rec("PPI_STRUCTURED_API", {"isin": "ARTEST000001"}),
        rec("PPI_AUTHENTICATED_XHR", {"isin": "ARTEST000001"}),
    ]
    assert ce.source_conflict(rows) == {}


def test_identity_observation_metadata_cannot_hide_actual_contract_conflict():
    rows = [
        rec("PPI_AUTHENTICATED_WEB", {
            "identity_observed": "AE38",
            "contract_completeness": "PARTIAL",
            "quantity_step": 1,
        }),
        rec("PPI_AUTHENTICATED_XHR", {
            "identity_observed": "AE38 - Bono",
            "contract_completeness": "OPERABILITY_ONLY",
            "quantity_step": 100,
        }),
    ]
    conflicts = ce.source_conflict(rows)
    assert set(conflicts) == {"quantity_step"}
