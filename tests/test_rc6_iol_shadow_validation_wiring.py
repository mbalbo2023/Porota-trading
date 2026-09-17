import en_validation_project_dashboard_rc6 as dashboard


def test_validation_page_includes_iol_shadow_fragment(monkeypatch):
    monkeypatch.setattr(dashboard.projection, "read", lambda: None)
    monkeypatch.setattr(dashboard.iol_shadow_view, "render",
                        lambda: "<section>IOL_SHADOW_FRAGMENT</section>")

    html = dashboard.page()

    assert "IOL_SHADOW_FRAGMENT" in html
    assert "Camino a Producción" in html
