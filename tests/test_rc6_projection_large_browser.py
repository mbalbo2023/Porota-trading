"""The browser proof cannot accept a small cut or write into its custody."""
import builtins
from pathlib import Path

import pytest

from tests.ci_rc6_projection_large_browser import run
from tests.ci_rc6_projection_large_reader import GateFailure, custody_inventory
from tests.rc6_dashboard_native_fixture import native_fixture


@pytest.fixture(scope="module")
def native_browser_fixture(tmp_path_factory):
    return native_fixture(tmp_path_factory.mktemp("native-large-browser-negative"))


def test_large_browser_rejects_native_small_cut_before_importing_or_launching_playwright(native_browser_fixture, tmp_path, monkeypatch):
    fixture = native_browser_fixture
    original_import, imports = builtins.__import__, []
    def guarded_import(name, *args, **kwargs):
        if name.startswith("playwright"):
            imports.append(name)
            raise AssertionError("Small cut started a browser")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    output = tmp_path/"new-browser-output"
    before = custody_inventory(fixture.database, fixture.root)
    with pytest.raises(GateFailure, match="^NATIVE_LARGE_REPORT_NOT_EXERCISED$"):
        run(fixture.database, fixture.root, output)
    assert imports == [] and not output.exists()
    assert custody_inventory(fixture.database, fixture.root) == before


@pytest.mark.parametrize("destination", ("root", "authority", "database", "wal", "existing", "symlink"))
def test_large_browser_rejects_output_aliases_before_writing_or_opening_sqlite(native_browser_fixture, tmp_path, monkeypatch, destination):
    fixture = native_browser_fixture
    existing = tmp_path/"existing"
    existing.mkdir()
    symlink = tmp_path/"source-alias"
    symlink.symlink_to(fixture.root, target_is_directory=True)
    output = {"root": fixture.root/"browser-output", "authority": Path(str(fixture.root)+".authority")/"browser-output",
              "database": fixture.database, "wal": Path(str(fixture.database)+"-wal"),
              "existing": existing, "symlink": symlink}[destination]
    before = custody_inventory(fixture.database, fixture.root)
    def no_sqlite(*_args, **_kwargs):
        raise AssertionError("Invalid destination reached SQLite")
    monkeypatch.setattr("sqlite3.connect", no_sqlite)
    with pytest.raises(GateFailure, match="^OUTPUT_MUST_BE_NEW_AND_OUTSIDE_SOURCES$"):
        run(fixture.database, fixture.root, output)
    assert custody_inventory(fixture.database, fixture.root) == before
    assert not list(existing.iterdir())
