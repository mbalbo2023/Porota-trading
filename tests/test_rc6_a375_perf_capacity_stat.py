"""A375: no-write bounded stat of performance evidence quota."""
from pathlib import Path
import inspect
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_market_watch_readonly as watch


def files(root):
    directory = root / "data" / "paper_v17"
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / "observer_v17.db.performance.sqlite"
    return base, Path(str(base) + "-wal"), Path(str(base) + "-shm")


def test_missing_evidence_files_never_green(tmp_path):
    result = watch.performance_evidence_capacity_stat(tmp_path)
    assert result["state"] == "NO_EVIDENCE_FILES"
    assert result["report_freshness"] == "NO_VERIFICADO"
    assert result["evidence_total_bytes"] == 0
    assert result["real_order_routes_called"] == 0


def test_capacity_components_and_existing_file_metadata_unchanged(tmp_path):
    db, wal, shm = files(tmp_path)
    db.write_bytes(b"ABCD")
    wal.write_bytes(b"EFG")
    shm.write_bytes(b"HI")
    before = [(p.stat().st_mtime_ns, p.read_bytes()) for p in (db, wal, shm)]
    observed = watch.performance_evidence_capacity_stat(tmp_path, default_limit=10)
    after = [(p.stat().st_mtime_ns, p.read_bytes()) for p in (db, wal, shm)]
    assert observed["state"] == "BELOW_DEFAULT_LIMIT_NOT_HEALTH"
    assert observed["evidence_total_bytes"] == 9
    assert observed["evidence_by_component_bytes"] == {"db": 4, "wal": 3, "shm": 2}
    assert observed["report_freshness"] == "NO_VERIFICADO"
    assert before == after
    assert "observer_v17.db" not in json.dumps(observed)


def test_exact_capacity_is_an_alert_not_quota_increase(tmp_path):
    db, wal, _ = files(tmp_path)
    db.write_bytes(b"a" * 6)
    wal.write_bytes(b"b" * 4)
    observed = watch.performance_evidence_capacity_stat(tmp_path, default_limit=10)
    assert observed["state"] == "AT_OR_ABOVE_DEFAULT_LIMIT"
    assert observed["evidence_total_bytes"] == 10
    assert observed["provider_requests"] == 0


def test_symlink_rejected_without_following_it(tmp_path):
    db, _, _ = files(tmp_path)
    db.symlink_to(tmp_path / "private-secret.txt")
    result = watch.performance_evidence_capacity_stat(tmp_path)
    assert result["state"] == "NO_VERIFICADO_UNSAFE_FILE_TYPE"
    assert result["health"] == "NO_VERIFICADO"
    assert "private-secret" not in json.dumps(result)


def test_no_runtime_db_open_or_market_mutation_in_probe_contract():
    source = inspect.getsource(watch.performance_evidence_capacity_stat)
    assert "lstat()" in source
    assert "sqlite3.connect" not in source
    assert "unlink(" not in source
    assert "write_" not in source
    assert "PPI" not in source
    assert 'result["performance_evidence_capacity"] = performance_evidence_capacity_stat(root)' in inspect.getsource(watch.probe)


def test_limit_must_be_int_positive(tmp_path):
    for value in (0, -1, True, 1.0):
        with pytest.raises(ValueError, match="PERFORMANCE_DEFAULT_CAPACITY_LIMIT_INVALID"):
            watch.performance_evidence_capacity_stat(tmp_path, default_limit=value)
