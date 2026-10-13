"""Complete source/bundle/image mode policy, including honestly rebound hashes."""
from copy import copy
import io
import json
import stat
import tarfile

import pytest

from scripts.porota_artifact_provenance import (
    BUNDLE_MANIFEST_NAME, SOURCE_MANIFEST_NAME, ProvenanceError, canonical_bytes,
    create_source_manifest, sha256_file,
)
from scripts.porota_validate_deploy_artifact import validate
from tests.test_issue465_provenance import candidate, check_bundle, check_image


MODES = [0o600, 0o640, 0o666, 0o4644, 0o2644, 0o1644]


@pytest.mark.parametrize("mode", MODES)
def test_checkout_complete_mode_changes_are_rejected(candidate, mode):
    (candidate["repo"] / "app.py").chmod(mode)
    with pytest.raises(ProvenanceError, match="CHECKOUT_MODE_MISMATCH"):
        create_source_manifest(candidate["repo"])


@pytest.mark.parametrize("mode", MODES)
def test_image_complete_mode_changes_are_rejected(candidate, mode):
    (candidate["image"] / "app.py").chmod(mode)
    with pytest.raises(ProvenanceError, match="IMAGE_SOURCE_MODE_MISMATCH"):
        check_image(candidate)
    result = validate(candidate["repo"], candidate["image"], ["app.py", "worker.py"])
    assert result["status"] == "FAILED" and result["artifact_mode_mismatches"] == ["app.py"]


def change_bundle_mode(candidate, name, mode):
    with tarfile.open(candidate["bundle"], "r:gz") as archive:
        rows = [(copy(item), archive.extractfile(item).read()) for item in archive]
    with tarfile.open(candidate["bundle"], "w:gz") as archive:
        for item, payload in rows:
            if item.name == name: item.mode = mode
            archive.addfile(item, io.BytesIO(payload))
    metadata = json.loads(candidate["bundle_meta"].read_bytes())
    metadata["bundle_sha256"] = sha256_file(candidate["bundle"])
    candidate["bundle_meta"].write_bytes(canonical_bytes(metadata))


@pytest.mark.parametrize("mode", MODES)
def test_bundle_complete_mode_changes_reject_even_with_rebound_outer_digest(candidate, mode):
    change_bundle_mode(candidate, "app.py", mode)
    with pytest.raises(ProvenanceError, match="BUNDLE_SOURCE_MODE_MISMATCH"):
        check_bundle(candidate)


@pytest.mark.parametrize("name", [SOURCE_MANIFEST_NAME, BUNDLE_MANIFEST_NAME])
def test_generated_bundle_metadata_is_exactly_0644(candidate, name):
    change_bundle_mode(candidate, name, 0o666)
    with pytest.raises(ProvenanceError, match="BUNDLE_GENERATED_METADATA_MODE_MISMATCH"):
        check_bundle(candidate)


def test_positive_git_modes_remain_0644_and_0755(candidate):
    assert stat.S_IMODE((candidate["repo"] / "app.py").stat().st_mode) == 0o644
    assert stat.S_IMODE((candidate["repo"] / "scripts/run").stat().st_mode) == 0o755
    assert check_image(candidate)["status"] == check_bundle(candidate)["status"] == "GREEN"
