"""R89: legacy historical providers cannot activate themselves on startup."""

import os
import subprocess
import sys

import ba_data912_history as data912
import m_instrument_universe as universe


def test_data912_and_archive_fallback_default_off_without_env():
    env = os.environ.copy()
    env.pop("DATA912_REFRESH_ENABLED", None)
    env.pop("HISTORICAL_UNIVERSE_FALLBACK", None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", (
            "import ba_data912_history as d, m_instrument_universe as u; "
            "assert d.DATA912_REFRESH_ENABLED is False; "
            "assert u.HISTORICAL_UNIVERSE_FALLBACK is False"
        )], env=env, text=True, capture_output=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def test_archive_off_means_no_archive_read_or_provider_egress_on_ppi_miss(monkeypatch):
    instrument = universe.Instrument("GGAL", "ACCIONES", "A-24HS", "ACCIONES")
    monkeypatch.setattr(universe, "HISTORICAL_UNIVERSE_FALLBACK", False)
    monkeypatch.setattr(universe, "load_watchlist", lambda: [instrument])
    monkeypatch.setattr(data912, "archived_liquidity", lambda *_a: (_ for _ in ()).throw(
        AssertionError("DATA912_ARCHIVE_READ_WHILE_DISABLED")))
    monkeypatch.setattr(data912, "ensure_symbol", lambda *_a: (_ for _ in ()).throw(
        AssertionError("DATA912_PROVIDER_EGRESS_WHILE_DISABLED")))

    class NoPPIHistory:
        def get_historical_series(self, *_args):
            return []

    assert universe.filter_by_liquidity([instrument], NoPPIHistory()) == []


def test_archive_off_keeps_explicit_watchlist_but_never_imports_archived_universe(monkeypatch):
    instrument = universe.Instrument("AL30", "BONOS", "A-24HS", "BONOS")
    monkeypatch.setattr(universe, "HISTORICAL_UNIVERSE_FALLBACK", False)
    monkeypatch.setattr(universe, "AUTO_DISCOVER_INSTRUMENTS", False)
    monkeypatch.setattr(universe, "load_watchlist", lambda: [instrument])
    monkeypatch.setattr(data912, "archived_instruments", lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("UNATTESTED_ARCHIVE_UNIVERSE_WHILE_DISABLED")))
    assert universe.build_universe(object()) == [instrument]


def test_explicit_legacy_opt_in_remains_available_only_in_research(monkeypatch):
    instrument = universe.Instrument("GGAL", "ACCIONES", "A-24HS", "ACCIONES")
    monkeypatch.setattr(universe, "HISTORICAL_UNIVERSE_FALLBACK", True)
    monkeypatch.setattr(data912, "DATA912_REFRESH_ENABLED", True)
    monkeypatch.setattr(universe, "load_watchlist", lambda: [instrument])
    monkeypatch.setattr(data912, "archived_liquidity", lambda *_a: None)
    called = []
    monkeypatch.setattr(data912, "ensure_symbol", lambda *args: called.append(args) or 0)

    class MissingPPIHistory:
        def get_historical_series(self, *_args):
            return []

    assert universe.filter_by_liquidity([instrument], MissingPPIHistory()) == []
    assert called == [("GGAL", "ACCIONES")]
