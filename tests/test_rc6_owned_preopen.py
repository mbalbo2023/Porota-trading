"""Private preopen ownership preserves public isolation and every canonical byte."""
import copy
from decimal import Decimal
import json
from unittest.mock import patch

import pytest

from rc6_dynamic_universe.common import digest
from rc6_dynamic_universe.live import run_shadow
from rc6_dynamic_universe.tradeability import freeze_preopen, freeze_preopen_owned
from tests.test_rc6_dynamic_tradeability import ranking, instrument, FROZEN, OPEN
from tests.test_rc6_dynamic_orchestrator import plan


def freeze(report, owned=False, **changes):
    kwargs={'frozen_at':FROZEN,'session_open':OPEN,'capacity_fingerprint':'capacity-v1'} | changes
    return freeze_preopen_owned(report,**kwargs) if owned else freeze_preopen(report,**kwargs)


def test_owned_freeze_has_exact_public_bytes_and_hash_without_document_sized_encode():
    report=ranking(assets=[instrument(f'A{n:04d}') for n in range(600)],history=[],observations=[])
    public=freeze(report).to_dict()
    original=json.JSONEncoder.encode
    sizes=[]
    def bounded(self,value):
        raw=original(self,value)
        sizes.append(len(raw.encode()))
        assert sizes[-1]<=65536
        return raw
    with patch.object(json.JSONEncoder,'encode',bounded):
        owned=freeze(report,owned=True)
    assert owned==public and owned['digest']==digest(public['payload'])
    assert owned['payload']['rows'] is report['rows']
    assert sizes and max(sizes)<=65536
    # The public API retains a fresh independent JSON tree on every access.
    frozen=freeze(report)
    one=frozen.payload;one['rows'][0]['rank']=-1
    assert frozen.payload['rows'][0]['rank']!=-1
    assert report['rows'][0]['rank']!=-1


def test_mutation_of_owned_input_is_rejected_by_the_real_planner():
    assets=[instrument('A')];report=ranking(assets=assets)
    owned=freeze(report,owned=True)
    report['rows'][0]['tradeable']=not report['rows'][0]['tradeable']
    with pytest.raises(ValueError,match='INVALID_PREOPEN_FREEZE'):
        plan(assets,freeze=owned)


@pytest.mark.parametrize('bad',[Decimal('1.20'),float('nan'),float('inf')])
def test_owned_freeze_keeps_strict_public_json_type_policy(bad):
    report=ranking();report['unexpected']=bad
    for owned in (False,True):
        with pytest.raises((ValueError,TypeError)):
            freeze(report,owned=owned)


@pytest.mark.parametrize('changes',[{'frozen_at':OPEN},{'capacity_fingerprint':''},
                                  {'frozen_at':'2026-10-02T19:00:00+00:00'}])
def test_same_preopen_causal_validation_applies_to_both_ownership_modes(changes):
    for owned in (False,True):
        with pytest.raises(ValueError):freeze(ranking(),owned=owned,**changes)


@pytest.mark.parametrize('kwargs',[{'owned_frozen':True},{'owned_frozen':1,'frozen_only':True}])
def test_owned_mode_is_restricted_to_private_build_not_the_live_planner(kwargs):
    with pytest.raises(ValueError,match='OWNED_FREEZE_PRIVATE_BUILD_ONLY'):
        run_shadow({},**kwargs)
