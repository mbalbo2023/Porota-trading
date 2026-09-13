from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNNER=(ROOT/'ops'/'ppi_web_residual_server_runner_rc6.py').read_text(encoding='utf-8')
INGEST=(ROOT/'ops'/'ppi_web_history_ingest_rc6.py').read_text(encoding='utf-8')
UNIT=(ROOT/'ops'/'porota-ppi-web-residual-rc6.service').read_text(encoding='utf-8')
STORE=(ROOT/'cu_history_store_v2_hf6.py').read_text(encoding='utf-8')


def test_runner_is_fail_closed_against_api_writer_and_enable_gate():
    assert 'PPI_WEB_RESIDUAL_NOT_ENABLED' in RUNNER
    assert 'PPI_API_HISTORICAL_WRITER_ACTIVE' in RUNNER
    assert 'load_from_runtime()' in RUNNER
    assert 'PRODUCTION_PAPER' in RUNNER
    assert 'real_orders_sent' in RUNNER


def test_browser_is_unprivileged_and_auth_is_local_only():
    assert 'runuser' in RUNNER
    assert 'PPI_WEB_BROWSER_USER' in RUNNER
    assert '/etc/porota/contract-evidence-web.env' in RUNNER
    assert 'PPI_WEB_PASSWORD' not in RUNNER
    assert 'PPI_WEB_USERNAME' not in RUNNER


def test_web_history_has_distinct_provenance_and_precedence():
    assert 'SOURCE = "PPI_WEB_HISTORY"' in INGEST
    assert 'source=SOURCE' in INGEST
    assert '"PPI_PRODUCTION_HISTORY": 10' in STORE
    assert '"PPI_WEB_HISTORY": 15' in STORE
    assert '"IOL": 30' in STORE


def test_systemd_points_to_real_runner_and_profile_write_exception():
    assert 'ppi_web_residual_server_runner_rc6.py' in UNIT
    assert 'User=root' in UNIT
    assert 'PPI_WEB_BROWSER_USER=porotaadmin' in UNIT
    assert '/home/porotaadmin/porota-browser-lab/chrome-profile' in UNIT
    assert 'ConditionPathExists=/opt/porota-ingest/ppi-web-residual/ENABLED' in UNIT


def test_no_order_execution_api_in_new_runtime():
    forbidden=('place_order','send_order','buy_order','sell_order','cancel_order','requests.post','requests.put','requests.delete','requests.patch')
    lower=(RUNNER+'\n'+INGEST).lower()
    assert all(token not in lower for token in forbidden)
