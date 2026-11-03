"""Numeric JSON serialization changes are not economic intraday revisions."""
from datetime import timedelta

import pytest

from fg_intraday_contract_policy_rc6 import classify_revision, IntradayContractPolicyError
from tests.test_intraday_scalping_rc6_integration import START, make_store, record
import cf_intraday_scalping as scalping


@pytest.mark.parametrize("old_price,new_price,old_volume,new_volume", [
    ("100.0", "100", "10.00", "10"),
    ("100", "100.0000", "10", "1E+1"),
    ("1E+2", "100", "0.0", "0"),
    ("100", "100", "0", "-0"),
])
def test_equal_numbers_do_not_reject_closed_points(old_price, new_price, old_volume, new_volume):
    result = classify_revision(event_at=START, received_at=START+timedelta(minutes=20),
        old_price=old_price, new_price=new_price, old_volume=old_volume, new_volume=new_volume)
    assert result["action"] == "SAME"
    assert result["closed_revision"] is False


@pytest.mark.parametrize("new_price,new_volume", [("100.01", "10"), ("100", "10.01")])
def test_actual_numeric_revision_still_rejects_closed_points(new_price, new_volume):
    result = classify_revision(event_at=START, received_at=START+timedelta(minutes=20),
        old_price="100.0", new_price=new_price, old_volume="10.0", new_volume=new_volume)
    assert result["action"] == "REJECT_CLOSED_REVISION"
    assert result["closed_revision"] is True


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "not-a-number", True])
def test_malformed_or_nonfinite_values_cannot_count_as_stable(bad):
    with pytest.raises(IntradayContractPolicyError, match="NUMERIC_EVIDENCE_INVALID"):
        classify_revision(event_at=START, received_at=START+timedelta(minutes=20),
            old_price=bad, new_price=bad, old_volume="10", new_volume="10")


def test_ppi_float_to_integer_serialization_does_not_poison_session(tmp_path):
    store, identity = make_store(tmp_path), record()

    def payload(count, numeric):
        return [{"date": (START+timedelta(minutes=i)).isoformat(),
                 "price": numeric(100+i), "volume": numeric(10+i%2)}
                for i in range(count)]

    first_at = START+timedelta(minutes=20)
    first = scalping.normalize_payload(payload(20, float), received_at=first_at)
    scalping.persist_payload(store, identity, first, received_at=first_at)
    next_at = START+timedelta(minutes=21)
    second = scalping.normalize_payload(payload(21, int), received_at=next_at)
    result = scalping.persist_payload(store, identity, second, received_at=next_at)
    assert result["changed"] == 0
    assert result["stable"] >= 5
    assert result["state"] == "CONFIRMED_INTERVAL_VOLUME"
    with store.connect() as connection:
        state = dict(connection.execute("SELECT * FROM ppi_intraday_contract_state").fetchone())
    assert state["changed_closed_points"] == 0


def test_equal_serialization_does_not_authenticate_missing_volume_contract(tmp_path):
    store, identity = make_store(tmp_path), record()
    identity.pop("raw", None)
    identity.pop("metadata_json", None)
    for i in range(2):
        at = START+timedelta(minutes=20+i)
        points = [(START+timedelta(minutes=k), "100.0" if i == 0 else "100", "10")
                  for k in range(20+i)]
        result = scalping.persist_payload(store, identity, points, received_at=at)
    assert result["changed"] == 0
    assert result["state"] == "PPI_VOLUME_CONTRACT_UNVERIFIED"
