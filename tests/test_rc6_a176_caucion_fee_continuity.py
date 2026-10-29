"""Fee-model continuity cannot relabel the exact A175 failed predecessor."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from scripts import rc6_caucion_fee_restatement_apply as apply
from scripts import rc6_caucion_fee_restatement_continuity as continuity


MODE = 'CLOSED_A175_PENDING_FIX_FORWARD'
CANDIDATE = 'c' * 40


def receipt():
    book = {'source_sha': continuity.A175_FAILED_CANDIDATE,
            'process_state': 'WAITING_MARKET', 'session_state': 'MARKET_CLOSED',
            'real_orders_sent': 0}
    return {
        'candidate_sha': CANDIDATE,
        'status': 'FIX_FORWARD_VALIDATION_REQUIRED',
        'installation_started': True,
        'real_orders_sent': 0,
        'ppi_watch_accessed': False,
        'market_open_suspended': False,
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'inflight': 'ABSENT_OWNED_RUNTIME_STOPPED',
        'a175_pending_adopted': True,
        'a175_origin_candidate': continuity.A175_FAILED_CANDIDATE,
        'a175_origin_sha256': continuity.A175_FAILED_RECEIPT_SHA256,
        'a175_previous_proof': {
            'intraday_a175': False,
            'origin_candidate': continuity.A175_FAILED_CANDIDATE,
            'origin_sha256': continuity.A175_FAILED_RECEIPT_SHA256,
            'image_id': continuity.A175_LOADED_IMAGE_ID,
            'marker_sha256': continuity.A175_MARKER_SHA256,
            'frozen_sha256': continuity.A175_FROZEN_SHA256,
            'flat_book': book, 'failure_flat_book': dict(book),
            'operator_hold_business_day': '2026-10-29',
            'operator_a151_origin_sha256': continuity.A175_A151_ORIGIN_SHA256,
            'operator_receipt_sha256': 'd' * 64,
            'containers': {name: {'id': cid,
                                  'image': continuity.A175_LOADED_IMAGE_ID}
                           for name, cid in continuity.A175_RUNNING_IDS.items()},
        },
    }


def test_a175_mode_requires_exact_failed_provenance_and_third_owner_literal():
    assert MODE in apply.INTRADAY_MODES and MODE in continuity.INTRADAY_MODES
    assert continuity.validate_prior_receipt(receipt(), CANDIDATE, MODE).tzinfo


def test_a175_market_open_continuity_requires_exact_mode_and_flat_provenance():
    open_mode='OPEN_A175_PENDING_FIX_FORWARD'
    assert open_mode in apply.INTRADAY_MODES and open_mode in continuity.INTRADAY_MODES
    data=receipt()
    data['market_open_suspended']=True
    data['a175_previous_proof']['intraday_a175']=True
    data['a175_previous_proof']['flat_book'].update(
        process_state='RUNNING',session_state='MARKET_OPEN')
    assert continuity.validate_prior_receipt(data,CANDIDATE,open_mode).tzinfo
    with pytest.raises(ValueError,match='PAPER_RESTATEMENT_.*RED'):
        continuity.validate_prior_receipt(data,CANDIDATE,MODE)
    data['a175_previous_proof']['flat_book']['real_orders_sent']=1
    with pytest.raises(ValueError,match='PAPER_RESTATEMENT_A175_ORIGIN_RED'):
        continuity.validate_prior_receipt(data,CANDIDATE,open_mode)


@pytest.mark.parametrize('field,value', (
    ('a175_pending_adopted', False),
    ('a175_origin_sha256', '0' * 64),
    ('inflight', 'UNKNOWN'),
))
def test_a175_origin_tamper_fails_closed(field, value):
    data = receipt()
    data[field] = value
    with pytest.raises(ValueError, match='PAPER_RESTATEMENT_A175_ORIGIN_RED'):
        continuity.validate_prior_receipt(data, CANDIDATE, MODE)


@pytest.mark.parametrize('path,value', (
    (('failure_flat_book', 'process_state'), 'DEGRADED'),
    (('flat_book', 'real_orders_sent'), 1),
    (('operator_a151_origin_sha256',), '0' * 64),
    (('marker_sha256',), '0' * 64),
    (('operator_hold_business_day',), '2026-10-30'),
))
def test_a175_current_and_failure_paper_proof_cannot_be_rewritten(path, value):
    data = deepcopy(receipt())
    item = data['a175_previous_proof']
    for key in path[:-1]:
        item = item[key]
    item[path[-1]] = value
    with pytest.raises(ValueError, match='PAPER_RESTATEMENT_A175_ORIGIN_RED'):
        continuity.validate_prior_receipt(data, CANDIDATE, MODE)
