"""Observed route500: an API year-1 clock underflows historical ART conversion."""
import sqlite3

import pytest

from rc6_trader_dashboard.components import clock
from tests.test_ws_dash_trader_terminal_08 import database, client, STAMP


@pytest.mark.parametrize('value', [
    '0001-01-01T00:00:00-03:00',
    '0001-01-01T00:00:00+00:00',
    '0001-01-01T00:00:00+14:00',
    '9999-12-31T23:59:59-12:00',
])
def test_unrepresentable_art_clock_is_unknown_without_wrapping_or_crashing(value):
    assert clock(value) == 'NO_VERIFICADO'


@pytest.mark.parametrize('value', [
    '0001-01-01T00:00:00-03:00',
    '9999-12-31T23:59:59-12:00',
])
def test_authenticated_sources_page_survives_provider_clock_bounds(database, client, value):
    with sqlite3.connect(database) as c:
        c.execute('INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (1,'PPI',STAMP,'T000','ACCIONES','BYMA','ARS','INMEDIATA',
                   '100','99','101','10','10',value,value,None))
    response = client.get('/en-vivo/fuentes')
    assert response.status_code == 200
    assert "id='terminal-content'" in response.text
    assert 'NO_VERIFICADO' in response.text
    assert 'OverflowError' not in response.text


def test_normal_source_clock_preserves_exact_art_display():
    assert clock('2026-10-16T19:58:39.739+00:00') == '16/10 16:58:39 ART'
