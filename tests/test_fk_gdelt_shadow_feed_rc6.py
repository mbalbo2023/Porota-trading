import inspect
import pytest
import fk_gdelt_shadow_feed_rc6 as m


def test_gdelt_feed_is_retired_and_has_no_http_client():
    assert m.DEPRECATED is True
    source = inspect.getsource(m).lower()
    assert "import requests" not in source
    assert "api.gdeltproject.org" not in source


@pytest.mark.parametrize("fn", [m.build_params, m.fetch_articles, m.normalize_article, m.collect_shadow])
def test_all_active_entrypoints_fail_closed_as_deprecated(fn):
    with pytest.raises(m.GDELTShadowError, match="GDELT_DEPRECATED_EXCLUDED"):
        fn()


def test_normalization_helpers_do_not_create_event_evidence():
    assert m.normalize_articles([]) == []
    assert m.is_market_relevant_title("SANCTIONS", "anything") is False
