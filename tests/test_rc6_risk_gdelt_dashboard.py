import inspect
import rc6_risk_gdelt_dashboard as panel


def test_gdelt_dashboard_surface_is_removed():
    assert panel.DEPRECATED is True
    assert panel.render_section() == ""
    assert panel._decorate("<main>x</main>") == "<main>x</main>"


def test_install_is_noop_and_has_no_runtime_source_import():
    panel.install()
    assert panel._installed is True
    source = inspect.getsource(panel).lower()
    assert "rc6_gdelt_event_risk_job" not in source
    assert "zz_wave8_dashboard_live_rc6" not in source
