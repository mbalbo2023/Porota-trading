"""Shared immutable preopen rows preserve canonical bytes and atomic evidence."""
import gzip
import hashlib
import json
import pytest

from rc6_shadow_runtime import persistence as io
from rc6_shadow_runtime.worker import shared_preopen_row_lists


def rows_fixture():
    return [{"n": n, "label": "José 🚀", "values": [None, True, -0.0, 1.25]}
            for n in range(300)]


def payload_fixture(rows):
    return {"a": rows, "b": rows, "c": rows,
            "numeric_keys": {1: "one", 2: "two"}, "tuple": (1, 2)}


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str, allow_nan=False).encode("ascii")


def measured(payload, rows, *, enabled=True):
    row_ids = {id(row) for row in rows}
    calls = []
    class Encoder(json.JSONEncoder):
        def encode(self, value):
            if id(value) in row_ids:
                calls.append(id(value))
            return super().encode(value)
    encoder = Encoder(sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    kwargs = {"immutable_shared_rows": (rows,)} if enabled else {}
    raw = b"".join(io._canonical_chunks(encoder, payload, **kwargs))
    return raw, len(calls)


def test_shared_rows_encode_once_and_match_standard_json():
    rows = rows_fixture()
    payload = payload_fixture(rows)
    raw, calls = measured(payload, rows)
    generic, generic_calls = measured(payload, rows, enabled=False)
    assert raw == generic == canonical(payload)
    assert calls == len(rows)
    assert generic_calls == 3 * len(rows)


@pytest.mark.parametrize("limit", ("_SHARED_SNAPSHOT_COMPRESSED_LIMIT", "_SHARED_SNAPSHOT_RAW_LIMIT"))
def test_cache_budget_falls_back_to_actual_serialization(monkeypatch, limit):
    rows = rows_fixture()
    payload = payload_fixture(rows)
    monkeypatch.setattr(io, limit, 1)
    raw, calls = measured(payload, rows)
    assert raw == canonical(payload)
    assert calls == 3 * len(rows)


def test_each_write_gets_a_fresh_snapshot_and_digest(tmp_path):
    rows = rows_fixture()
    payload = payload_fixture(rows)
    with io.EvidenceFiles(tmp_path / "shadow") as files:
        first = files.write_streamed_report(payload, immutable_shared_rows=(rows,))
        expected_first = canonical(payload)
        rows[0]["label"] = "changed before the next write"
        second = files.write_streamed_report(payload, immutable_shared_rows=(rows,))
        expected_second = canonical(payload)
    assert first["sha256"] != second["sha256"]
    for receipt, expected in ((first, expected_first), (second, expected_second)):
        raw = gzip.decompress((files.root / receipt["file"]).read_bytes())
        assert raw == expected
        assert hashlib.sha256(raw).hexdigest() == receipt["sha256"]


def test_corrupt_cached_bytes_leave_prior_evidence_and_no_staging(tmp_path, monkeypatch):
    rows = rows_fixture()
    payload = payload_fixture(rows)
    original = io.zlib.decompressobj
    class Corrupt:
        def __init__(self):
            self.inner = original()
            self.changed = False
        def decompress(self, data, maximum):
            block = self.inner.decompress(data, maximum)
            if block and not self.changed:
                self.changed = True
                return bytes([block[0] ^ 1]) + block[1:]
            return block
        def __getattr__(self, name):
            return getattr(self.inner, name)
    with io.EvidenceFiles(tmp_path / "shadow") as files:
        prior = files.write_streamed_report({"valid": "prior"})
        before = (files.root / prior["file"]).read_bytes()
        monkeypatch.setattr(io.zlib, "decompressobj", Corrupt)
        with pytest.raises(ValueError, match="SHADOW_SNAPSHOT_CACHE_DIGEST_MISMATCH"):
            files.write_streamed_report(payload, immutable_shared_rows=(rows,))
        assert (files.root / prior["file"]).read_bytes() == before
        assert sorted(p.name for p in files.root.glob("computed-*")) == [prior["file"]]
        assert not list(files.root.glob("*.tmp"))


@pytest.mark.parametrize("invalid", ("cycle", "nan", "mixed_keys"))
def test_cached_path_rejects_invalid_payload_atomically(tmp_path, invalid):
    rows = rows_fixture()
    if invalid == "cycle":
        rows.append(rows)
    elif invalid == "nan":
        rows[0]["invalid"] = float("nan")
    else:
        rows[0]["invalid"] = {1: 1, "a": 2}
    with io.EvidenceFiles(tmp_path / "shadow") as files:
        with pytest.raises((ValueError, TypeError)):
            files.write_streamed_report(payload_fixture(rows), immutable_shared_rows=(rows,))
        assert not list(files.root.glob("computed-*"))
        assert not list(files.root.glob("*.tmp"))


def test_giant_scalar_capture_and_replay_use_bounded_zlib_buffers(monkeypatch):
    rows = rows_fixture()
    rows[0]["label"] = "ñ🚀" * 20000
    payload = payload_fixture(rows)
    original = io.zlib.compressobj
    sizes = []
    class Observed:
        def __init__(self, *args, **kwargs):
            self.inner = original(*args, **kwargs)
        def compress(self, block):
            sizes.append(len(block))
            return self.inner.compress(block)
        def flush(self):
            return self.inner.flush()
    monkeypatch.setattr(io.zlib, "compressobj", Observed)
    raw, calls = measured(payload, rows)
    assert raw == canonical(payload)
    assert sizes and max(sizes) <= 65536
    # The giant row is streamed structurally; every remaining row is encoded once.
    assert calls == len(rows) - 1


def test_only_shared_owned_preopen_lists_are_selected():
    rows = rows_fixture()
    report = {"preopen_immutable": True, "frozen": {
        "SCALPING": {"payload": {"rows": rows}},
        "EQUITY_SPOT": {"payload": {"rows": rows}},
        "OTHER": {"payload": {"rows": list(rows)}}}}
    assert shared_preopen_row_lists(report) == (rows,)
    report["preopen_immutable"] = False
    assert shared_preopen_row_lists(report) == ()
    report["preopen_immutable"] = True
    report["frozen"]["EQUITY_SPOT"]["payload"]["rows"] = list(rows)
    assert shared_preopen_row_lists(report) == ()


def test_worker_supplies_owned_rows_and_preserves_committed_metadata(tmp_path, monkeypatch):
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_shadow_runtime.persistence import read_committed_generation
    from tests.test_rc6_shadow_runtime_wiring import make_store, PRE
    store, _ = make_store(tmp_path, count=300)
    original = io.EvidenceFiles.write_streamed_report
    seen = []
    def observed(self, report, **kwargs):
        expected = shared_preopen_row_lists(report)
        assert expected and kwargs["immutable_shared_rows"] == expected
        seen.append(1)
        return original(self, report, **kwargs)
    monkeypatch.setattr(io.EvidenceFiles, "write_streamed_report", observed)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    returned = worker.tick(PRE)
    cut = read_committed_generation(worker.root)
    assert seen == [1]
    assert cut["status"]["catalog_ready_count"] == 300
    assert cut["status"]["preopen_digests"] == {
        key: value["digest"] for key, value in returned["frozen"].items()}
    assert cut["status"]["real_orders_sent"] == 0
    assert cut["status"]["real_routes"] == "NOT_CALLED"
