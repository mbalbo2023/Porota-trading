"""A374 ten-family OFFHOST gaps: no source/broker/DB/network use."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a374_family_receipt_gaps as audit

PRODUCT = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"


def fixture(time="2026-11-02T16:15:00Z", *, counter=12):
    snap = {"schema": "rc6.market-watch-readonly.v1", "generated_at": time,
            "provenance": {"deploy_sha": PRODUCT, "image_id": "sha256:" + "1"*64,
                           "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                           "real_order_routes": "NOT_CALLED"},
            "observer": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0},
            "real_order_routes_called": 0,
            "cursors": {k: counter for k in audit.COUNTERS}}
    board = {"schema": "rc6.operator-dashboard-readonly.v1", "identity_clocks": {
        "scope": audit.EXPECTED_SCOPE, "status": "BOUNDED", "rows": []}}
    return snap, board


def row(family="ACCIONES", symbol="GGAL", **kw):
    x = {"family": family, "symbol": symbol, "market": "BYMA", "currency": "ARS",
         "settlement": "A-24HS", "book_freshness": "FRESH", "trade_freshness": "FRESH",
         "receipt_freshness": "NO_VERIFICADO", "received_at": None}
    x.update(kw)
    return x


def zip_bytes(snap, board, *, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("snapshot.json", json.dumps(snap))
        z.writestr("dashboard_audit.json", json.dumps(board))
        for path, content in extra or []:
            z.writestr(path, content)
    return stream.getvalue()


def analyze(tmp_path, a, b, *, extra_a=None, extra_b=None, wrong_hash=False):
    raw_a = zip_bytes(*a, extra=extra_a)
    raw_b = zip_bytes(*b, extra=extra_b)
    p1, p2 = tmp_path / "first.zip", tmp_path / "second.zip"
    p1.write_bytes(raw_a)
    p2.write_bytes(raw_b)
    return audit.analyze(p1, p2, first_sha256="0"*64 if wrong_hash else sha256(raw_a).hexdigest(),
                         second_sha256=sha256(raw_b).hexdigest(), product_sha=PRODUCT)


def samples():
    a = fixture()
    b = fixture("2026-11-02T16:20:00Z", counter=16)
    a[1]["identity_clocks"]["rows"] = [row()]
    b[1]["identity_clocks"]["rows"] = [row()]
    return a, b


def test_ten_family_denominator_and_non_global_sample(tmp_path):
    a,b=samples()
    report=analyze(tmp_path,a,b)
    assert report["families_reviewed"] == 10
    assert report["families_pit_e2e_verified"] == 0
    assert report["families"]["ACCIONES"]["latest_sampled"] == 1
    assert report["families"]["ACCIONES"]["latest_book_fresh"] == 1
    assert report["families"]["ETFS"]["coverage"] == "NOT_SAMPLED"
    assert report["paper_entry_authority"] is False
    assert report["cursor_deltas_observed_only_not_trades"]["paper_fills"] == 4
    assert "GGAL" not in json.dumps(report)


def test_fake_positive_receipt_label_never_promotes(tmp_path):
    a,b=samples()
    b[1]["identity_clocks"]["rows"][0].update(
        receipt_freshness="VERIFIED_PIT", received_at="2026-11-02T16:18:00Z",
        first_known_at="2026-11-02T16:18:00Z", trusted=True, entry_authority=True)
    out=analyze(tmp_path,a,b)
    assert out["latest_missing_clocks_or_receipt"]["independent_signed_pit_attestation"] == 1
    assert out["families_pit_e2e_verified"] == 0
    assert out["families"]["ACCIONES"]["paper_entry_allowed_by_this_report"] is False


def test_does_not_relabel_no_fill_increment_as_fail(tmp_path):
    a,b=samples()
    a[0]["cursors"]["paper_fills"] = b[0]["cursors"]["paper_fills"] = 304
    out=analyze(tmp_path,a,b)
    assert out["cursor_deltas_observed_only_not_trades"]["paper_fills"] == 0
    assert out["status"] == "NO_VERIFICADO_E2E"


def test_wrong_hash_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="A374_ARTIFACT_SHA_MISMATCH"):
        analyze(tmp_path,*samples(),wrong_hash=True)


def test_paper_safety_wrong_mode_orders_or_real_route(tmp_path):
    for field,bad in (("mode", "LIVE"), ("real_orders_sent", 1),
                      ("real_order_routes", "CALLED")):
        a,b=samples()
        b[0]["provenance"][field]=bad
        with pytest.raises(ValueError,match="A374_PAPER_SAFETY_UNVERIFIED"):
            analyze(tmp_path,a,b)


def test_bad_product_sha_fails_closed(tmp_path):
    a,b=samples()
    b[0]["provenance"]["deploy_sha"]="a"*40
    with pytest.raises(ValueError,match="A374_PRODUCT_PROVENANCE_MISMATCH"):
        analyze(tmp_path,a,b)


def test_reverse_sample_clock_fails(tmp_path):
    a,b=samples()
    b[0]["generated_at"]="2026-11-02T16:10:00Z"
    with pytest.raises(ValueError,match="A374_RECEIPT_CLOCK_REVERSED"):
        analyze(tmp_path,a,b)


def test_future_first_known_is_rejected(tmp_path):
    a,b=samples()
    b[1]["identity_clocks"]["rows"][0]["first_known_at"]="2026-11-03T00:00:00Z"
    with pytest.raises(ValueError,match="A374_CAUSAL_SAMPLE_CLOCK_IN_FUTURE"):
        analyze(tmp_path,a,b)


def test_missing_five_key_or_unknown_family_rejected(tmp_path):
    a,b=samples()
    b[1]["identity_clocks"]["rows"][0]["market"]=""
    with pytest.raises(ValueError,match="A374_FULL_IDENTITY_INVALID"):
        analyze(tmp_path,a,b)
    a,b=samples()
    b[1]["identity_clocks"]["rows"][0]["family"]="FAMILY_NOT_ALLOWED"
    with pytest.raises(ValueError,match="A374_FULL_IDENTITY_INVALID"):
        analyze(tmp_path,a,b)


def test_duplicate_identity_rejected(tmp_path):
    a,b=samples()
    b[1]["identity_clocks"]["rows"].append(deepcopy(b[1]["identity_clocks"]["rows"][0]))
    with pytest.raises(ValueError,match="A374_DUPLICATE_IDENTITY_IN_SAMPLE"):
        analyze(tmp_path,a,b)


def test_sample_limit_is_fail_closed(tmp_path):
    a,b=samples()
    b[1]["identity_clocks"]["rows"]=[row(symbol=f"SYNTH{i}") for i in range(11)]
    with pytest.raises(ValueError,match="A374_SAMPLE_SCOPE_UNVERIFIED"):
        analyze(tmp_path,a,b)


def test_path_traversal_and_duplicate_zip_members_fail(tmp_path):
    for extra in [[("../bad.json", "private")],[("snapshot.json", "{}")] ]:
        with pytest.raises(ValueError,match="A374_ARTIFACT_MEMBERS_INVALID"):
            analyze(tmp_path,*samples(),extra_b=extra)


def test_counter_regression_is_visible_not_green(tmp_path):
    a,b=samples()
    b[0]["cursors"]["market_snapshots"] = 5
    result=analyze(tmp_path,a,b)
    assert result["counter_regressions"] == ["market_snapshots"]
    assert result["counter_regression_requires_review"] is True
    assert result["status"] == "NO_VERIFICADO_E2E"


def test_mismatched_image_fails_even_with_paper_zero(tmp_path):
    a,b=samples()
    b[0]["provenance"]["image_id"]="sha256:"+"2"*64
    with pytest.raises(ValueError,match="A374_RUNTIME_IMAGE_CHANGED"):
        analyze(tmp_path,a,b)
