from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _body_between(source: str, start: str, end: str) -> str:
    return source.split(start,1)[1].split(end,1)[0]


def test_caucion_runtime_does_not_repeat_schema_ddl_each_heartbeat():
    source=(ROOT/"di_caucion_cash_sweep_runtime_hf6.py").read_text()
    persist=_body_between(source,"def _persist_runtime(","def _existing_daily_allocation(")
    worker=_body_between(source,"def run_worker(","if __name__") if "if __name__" in source else source.split("def run_worker(",1)[1]
    assert "init_runtime_schema(store)" not in persist
    assert "init_runtime_schema(store)" in worker


def test_frequent_sre_probes_do_not_run_full_sqlite_quick_check():
    candle=(ROOT/"rc6_candle_integrity.py").read_text()
    introspection=(ROOT/"ops_introspection_rc6.py").read_text()
    assert "PRAGMA quick_check" not in candle
    assert "PRAGMA quick_check" not in introspection
    assert "DELEGATED_TO_FULL_DB_INTEGRITY" in candle
    assert "DELEGATED_TO_FULL_DB_INTEGRITY" in introspection


def test_full_integrity_remains_single_quick_check_authority():
    full=(ROOT/"rc6_full_db_integrity.py").read_text()
    assert "PRAGMA quick_check" in full
    assert "read-only, expensive by design" in full
