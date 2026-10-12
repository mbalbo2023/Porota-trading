"""Retained RED control for the actual missing native worker import proof."""
from types import SimpleNamespace

import pytest

from scripts import rc6_controlled_governed_runner as governed
from scripts import rc6_material_big as big


@pytest.mark.parametrize("proof", [{}, {"worker_final": None}, {"worker_final": []}])
def test_missing_worker_import_final_proof_stays_named_fail_closed(proof):
    # This is the closed negative receipt shape, not a qualified native proof.
    native = {"shadow": {"fsync": {}}, "import_provenance": proof,
        "resource_gates": {}, "native_cli_lifecycle": {"child_infrastructure_finalization": {}},
        "factual_exits": {}}
    args = SimpleNamespace(sha="a"*40, tree="b"*40, index_sha256="c"*64)
    with pytest.raises(ValueError, match="^NATIVE_WORKER_IMPORT_FINAL_PROOF_MISSING$") as failure:
        big.native_checks(native, {}, governed, args)
    assert big.safe_failure(failure.value, "native_receipt_qualification") == {
        "stage": "native_receipt_qualification", "class": "ValueError",
        "reason": "NATIVE_WORKER_IMPORT_FINAL_PROOF_MISSING"}
