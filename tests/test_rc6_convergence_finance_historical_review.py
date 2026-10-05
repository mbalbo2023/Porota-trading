"""Independent semantic attacks on synthetic historical package evidence."""
from datetime import timedelta
import hashlib

import pytest

from rc6_audit_evidence import EvidenceError, verify_package
from rc6_performance.common import stamp
from tests.test_issue465_historical_package import export, records, rewrite, source


def test_r75_duplicate_fill_cannot_pass_when_all_public_digests_and_the_pin_are_rebound(tmp_path):
    database = source(tmp_path)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    package = tmp_path/"duplicate-fill-package"
    export(database, package)
    rows = records(package/"fills.jsonl")
    rows[-1]["fill_id"] = rows[0]["fill_id"]
    rewrite(package, "fills.jsonl", rows)
    new_pin = hashlib.sha256((package/"manifest.json").read_bytes()).hexdigest()
    with pytest.raises(EvidenceError, match="DUPLICATE_OR_INVALID_FILL"):
        verify_package(package, expected_manifest_sha256=new_pin)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_r76_fill_one_microsecond_before_entry_is_rejected_despite_complete_semantic_rehash(tmp_path):
    database = source(tmp_path, include_second_currency=False)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    package = tmp_path/"clock-order-package"
    export(database, package)
    position = records(package/"positions.jsonl")[0]
    rows = records(package/"fills.jsonl")
    rows[0]["filled_at"] = (stamp(position["entry_at"])-timedelta(microseconds=1)).isoformat()
    rewrite(package, "fills.jsonl", rows)
    new_pin = hashlib.sha256((package/"manifest.json").read_bytes()).hexdigest()
    with pytest.raises(EvidenceError, match="FILL_CLOCK_ORDER"):
        verify_package(package, expected_manifest_sha256=new_pin)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
