"""The acceptance harness must catch every spelling of a source SQLite path."""
from pathlib import Path
from copy import deepcopy
from time import monotonic

import pytest

from tests.ci_rc6_projection_large_reader import GateFailure, require_native_large_cut, source_sqlite_guard


@pytest.mark.parametrize("spelling", (
    "path", "string", "bytes", "relative", "uri", "uri_query", "uri_localhost",
    "uri_short", "symlink", "hardlink", "wal", "shm", "journal", "wal_hardlink",
))
def test_source_sqlite_guard_rejects_uri_aliases_and_sidecars_before_connect(tmp_path, monkeypatch, spelling):
    database = tmp_path/"source with space.sqlite"
    database.write_bytes(b"source bytes; the guard must never open SQLite")
    sidecars = {suffix: Path(str(database)+suffix) for suffix in ("-wal", "-shm", "-journal")}
    for sidecar in sidecars.values():
        sidecar.write_bytes(b"source sidecar")
    symlink, hardlink, wal_hardlink = tmp_path/"symlink.sqlite", tmp_path/"hardlink.sqlite", tmp_path/"wal-alias"
    symlink.symlink_to(database)
    hardlink.hardlink_to(database)
    wal_hardlink.hardlink_to(sidecars["-wal"])
    monkeypatch.chdir(tmp_path)
    argument = {
        "path": database, "string": str(database), "bytes": bytes(database), "relative": database.name,
        "uri": database.as_uri(), "uri_query": database.as_uri()+"?mode=ro&immutable=1",
        "uri_localhost": database.as_uri().replace("file://", "file://localhost")+"?mode=ro",
        "uri_short": "file:"+str(database)+"?mode=ro", "symlink": symlink,
        "hardlink": hardlink, "wal": sidecars["-wal"], "shm": sidecars["-shm"],
        "journal": sidecars["-journal"], "wal_hardlink": wal_hardlink,
    }[spelling]
    calls, opened = [], []
    def connect(*args, **kwargs):
        opened.append((args, kwargs))
        raise AssertionError("Source path reached SQLite")
    with pytest.raises(GateFailure, match="^SQLITE_OPENED_SOURCE$"):
        source_sqlite_guard(database, connect, calls)(argument, uri=spelling.startswith("uri"))
    assert calls == ["SOURCE_BLOCKED"] and opened == []


@pytest.mark.parametrize("spelling", ("memory", "memory_uri", "private_path", "private_uri"))
def test_source_sqlite_guard_allows_private_or_memory_handles_without_changing_arguments(tmp_path, spelling):
    database = tmp_path/"source.sqlite"
    database.write_bytes(b"source")
    private = tmp_path/"private-copy.sqlite"
    private.write_bytes(database.read_bytes())
    argument = {"memory": ":memory:", "memory_uri": "file::memory:?cache=shared",
                "private_path": private, "private_uri": private.as_uri()+"?mode=ro"}[spelling]
    calls, opened, sentinel = [], [], object()
    def connect(*args, **kwargs):
        opened.append((args, kwargs))
        return sentinel
    kwargs = {"uri": spelling.endswith("uri"), "timeout": .25}
    assert source_sqlite_guard(database, connect, calls)(argument, **kwargs) is sentinel
    assert calls == ["PRIVATE_COPY_OR_MEMORY"] and opened == [((argument,), kwargs)]


@pytest.fixture(scope="module")
def native_harness_cut(tmp_path_factory):
    from rc6_shadow_runtime.persistence import read_committed_projection
    from tests.rc6_dashboard_native_fixture import native_fixture
    fixture = native_fixture(tmp_path_factory.mktemp("native-harness-guard"))
    return read_committed_projection(fixture.root, deadline=monotonic()+1)


@pytest.mark.parametrize("case,expected", (
    ("small_report", "NATIVE_LARGE_REPORT_NOT_EXERCISED"),
    ("preopen", "NATIVE_COMPLETED_OPEN_CUT_REQUIRED"),
    ("missing_phase", "NATIVE_COMPLETED_OPEN_CUT_REQUIRED"),
    ("small_catalog", "NATIVE_LARGE_POPULATION_REQUIRED"),
    ("small_observations", "NATIVE_LARGE_POPULATION_REQUIRED"),
))
def test_native_large_acceptance_rejects_small_or_preopen_cuts(native_harness_cut, case, expected):
    # Each negative begins with a real canonical cut. Alter only the copy to
    # exercise a later guard; no invented positive shape can certify the gate.
    original = deepcopy(native_harness_cut)
    cut = deepcopy(native_harness_cut)
    catalog_count, observation_count = 12000, 60000
    if case != "small_report":
        cut["export_contract"]["verified_payloads"]["report"]["logical_bytes"] = 4*1024**2+1
    if case == "preopen": cut["report"]["phase"] = "PREOPEN"
    elif case == "missing_phase": cut["report"].pop("phase")
    elif case == "small_catalog": catalog_count = 25
    elif case == "small_observations": observation_count = 0
    with pytest.raises(GateFailure, match="^"+expected+"$"):
        require_native_large_cut(cut, catalog_count=catalog_count, observation_count=observation_count)
    assert native_harness_cut == original
