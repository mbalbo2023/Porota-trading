"""Native depth32 reconstruction, bounded retained images and fresh wire proof."""
from datetime import timedelta
import gzip
import shutil
import sys

import pytest

from rc6_shadow_runtime import archive_components as components
from rc6_shadow_runtime.persistence import EvidenceFiles
from tests.test_issue465_generations import publish
from tests.test_rc6_shadow_runtime_wiring import PRE
from tests.test_rc6_component_archive import bytes_at, members, policy, recipe, snapshot, tree_custody


@pytest.fixture(scope="module")
def deepest_native_chain(tmp_path_factory):
    base = tmp_path_factory.mktemp("native-component-depth32")
    root, archive = base / "shadow", base / "archive"
    # LEGACY_CODEC_COMPATIBILITY: these two scenarios instrument the original
    # PAGE decoder specifically. This producer publishes real original PAGE
    # packs/CAS/recipes/ACKs; its output never qualifies the new dispatcher.
    class LegacyPageProducer(components.ComponentArchive):
        def _binary_option(self, *args, **kwargs):
            return None
    original_archive = components.ComponentArchive
    components.ComponentArchive = LegacyPageProducer
    last = None
    try:
        for number in range(1, 35):
            with EvidenceFiles(root) as files:
                cut = publish(files, number, as_of=PRE + timedelta(seconds=30*number))
            if number == 32:
                # A legitimate caller need not archive every current cut. Skipping
                # this modulo anchor reaches the helper's actual depth32 limit;
                # all source cuts remain intact and none is fabricated or dropped.
                continue
            directory = root / ("gen-" + cut["pointer"]["generation_id"])
            last = policy(root, archive).archive_generation(directory)
    finally:
        components.ComponentArchive = original_archive
    assert recipe(archive, last)["members"]["projection.sqlite"]["dependency_depth"] == 32
    return root, archive, cut, last


def test_native_depth32_restore_consumes_images_iteratively_with_bounded_pack_retention_and_exact_clocks(deepest_native_chain, monkeypatch):
    root, archive, cut, receipt = deepest_native_chain
    original = members(root / ("gen-" + receipt["generation_id"]))
    protected = (root, root.with_name(root.name+".authority"), archive)
    before = {str(path): tree_custody(path) for path in protected}
    instances, decoded, retained = [], [], []
    actual_class, actual_decode = components.ComponentArchive, components.decode_page_pack
    class ObservedArchive(actual_class):
        def __init__(self, *args):
            super().__init__(*args)
            instances.append(self)
    def decode(*args, **kwargs):
        verifier = instances[-1]
        # Instrument the real decoder, not a stand-in with fabricated images.
        # A depth32 restore must not retain all33 ancestors/128-MiB packs.
        frame, nesting = sys._getframe(), 0
        while frame is not None:
            nesting += frame.f_code is actual_class._projection.__code__
            frame = frame.f_back
        assert nesting == 1
        assert not verifier.projections
        packed_bytes = sum(len(raw) for raw, _ in verifier.packs.values())
        assert packed_bytes <= components.MAX_PACK_BYTES
        retained.append(packed_bytes)
        source = actual_decode(*args, **kwargs)
        decoded.append(kwargs["previous_depth"])
        return source
    monkeypatch.setattr(components, "ComponentArchive", ObservedArchive)
    monkeypatch.setattr(components, "decode_page_pack", decode)
    monkeypatch.setattr(gzip, "compress", lambda *a, **kw: pytest.fail("RECOVERY_ENCODER_CALLED"))
    result = policy(root, archive).restore_generation(receipt["generation_id"])
    assert result["members"] == original and result["manifest"] == cut["manifest"]
    assert decoded == [None, *range(32)] and len(retained) == 33
    assert all(not instance.projections for instance in instances)
    assert before == {str(path): tree_custody(path) for path in protected}


def test_native_depth32_forward_restore_reopens_original_pack_and_rejects_mid_call_corruption(deepest_native_chain, tmp_path, monkeypatch):
    original_root, original_archive, _, receipt = deepest_native_chain
    root, archive = tmp_path / "shadow", tmp_path / "archive"
    shutil.copytree(original_root, root)
    shutil.copytree(original_archive, archive)
    shutil.copytree(original_root.with_name(original_root.name+".authority"), root.with_name(root.name+".authority"))
    protected = snapshot(root)
    target = archive / recipe(archive, receipt)["packs"][-1]
    actual = components.inspect_page_pack
    mutated = []
    def inspected(*args, **kwargs):
        metadata = actual(*args, **kwargs)
        if metadata["dependency_depth"] == 0 and not mutated:
            wire = bytes_at(target)
            target.write_bytes(wire[:-1] + bytes([wire[-1] ^ 1]))
            mutated.append(target.name)
        return metadata
    monkeypatch.setattr(components, "inspect_page_pack", inspected)
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        policy(root, archive).restore_generation(receipt["generation_id"])
    assert mutated and snapshot(root) == protected


