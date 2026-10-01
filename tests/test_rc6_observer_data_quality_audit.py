from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_market_directory_is_created_and_owned_by_bot_user():
    compose=(ROOT/"docker-compose.yml").read_text(encoding="utf-8")
    assert "/app/data/market /app/data/paper_v17" in compose
    assert "chown 1000:1000 /app/data /app/data/market /app/data/paper_v17" in compose


def test_history_coverage_numerator_and_denominator_share_current_scope():
    source=(ROOT/"bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert "current_scope = {" in source
    assert "historical_scope = {" in source
    assert "covered = len(current_scope & historical_scope)" in source
    assert "cobertura scope actual {covered}/{len(current_scope)} instrumentos" in source
    assert "cobertura acumulada {covered}/{len(all_symbols)} instrumentos" not in source


def test_runtime_data_fix_does_not_change_paper_safety_guards():
    source=(ROOT/"bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert "real_orders_sent=0" in source
    assert "PRODUCTION_PAPER" in source
