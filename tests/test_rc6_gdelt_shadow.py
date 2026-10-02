import rc6_gdelt_shadow as gdelt


def test_compatibility_reader_reports_deprecated_excluded():
    result = gdelt.collect()
    assert gdelt.DEPRECATED is True
    assert result["state"] == "DEPRECATED_EXCLUDED"
    assert result["decision_effect"] == "EXCLUDED"
    assert result["articles_count"] == 0
    assert result["freshness"] == "NOT_APPLICABLE"


def test_refresh_is_inert_alias():
    assert gdelt.refresh()["state"] == "DEPRECATED_EXCLUDED"
