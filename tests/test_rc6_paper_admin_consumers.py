"""Administrative void must not be counted as a market exit by readers."""
from datetime import datetime, timedelta

import rc4_replay_analysis
from scripts import v17_ledger_preflight
from test_production_paper_v1634 import partial_spot

from test_rc6_paper_admin_cancel import _cancel, _pending


def test_admin_void_is_excluded_from_trade_durations_and_known_to_preflight(partial_spot, tmp_path):
    broker, position, quote, _ = partial_spot()
    _pending(broker, position)
    when = (datetime.fromisoformat(quote.observed_at) + timedelta(days=1)).isoformat()
    _cancel(broker, position, when)

    replay = rc4_replay_analysis.analyze(
        db_path=broker.store.path, history_path=tmp_path / 'missing-history.db')
    assert replay['db_available'] is True
    assert replay['durations'] == {'n': 0}

    report = v17_ledger_preflight.collect(broker.store.path)
    assert report['counts']['position_states'] == {'CANCELLED_ADMIN': 1}
    assert report['counts']['positions_inconsistent'] == 0
