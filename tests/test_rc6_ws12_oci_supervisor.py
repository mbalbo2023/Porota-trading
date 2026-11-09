"""Protected-main WS12 outer transport: no candidate code on host runner."""

from io import BytesIO
import json
import tarfile

import pytest

from scripts import rc6_ci_ws12_oci_supervisor as outer
from tests.test_rc6_ws12_trusted_receipt import C, TREE, T, D, receipt_input, AF_UNIX


def test_unapproved_image_denies_any_docker_call():
    called = []
    result = outer.admitted_image(D, approved=frozenset(),
                                  docker=lambda argv, timeout: called.append(argv))
    assert result is False
    assert called == []


def test_digest_pinned_image_and_separate_readonly_mounts(tmp_path):
    candidate = tmp_path / "candidate"
    control = tmp_path / "trusted-main"
    candidate.mkdir()
    control.mkdir()
    ref = "ghcr.io/mbalbo2023/porota-rc6-qa@" + D
    args = outer.container_args(
        candidate=str(candidate), control=str(control), image=ref,
        sha=C, tree=TREE, run_id="123", attempt="1", nonce="f" * 32,
        approved=frozenset((ref,)),
    )
    assert args.count("--mount") == 2
    mounts = [args[i + 1] for i, value in enumerate(args) if value == "--mount"]
    assert mounts == [
        f"type=bind,src={candidate},dst=/src,readonly,bind-recursive=disabled",
        f"type=bind,src={control},dst=/control,readonly,bind-recursive=disabled",
    ]
    assert args[args.index("--user") + 1] == "65532:65532"
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in args
    assert "--privileged" not in args and "--env-file" not in args
    assert "--log-driver" in args and args[args.index("--log-driver") + 1] == "none"
    assert "RC6_CANDIDATE_SHA=" + C in args
    assert "RC6_CANDIDATE_TREE_SHA=" + TREE in args
    assert "/control/scripts/rc6_ci_ws12_oci_inner.py" in args
    assert not any(item == "/var/run/docker.sock" for item in args)


def test_valid_foreign_image_digest_cannot_self_authorize_container_create(tmp_path):
    candidate = tmp_path / "candidate"
    control = tmp_path / "trusted-main"
    candidate.mkdir()
    control.mkdir()
    foreign = "ghcr.io/mbalbo2023/foreign@sha256:" + "e" * 64
    reviewed = "ghcr.io/mbalbo2023/porota-rc6-qa@" + D
    with pytest.raises(outer.OCIHOLD):
        outer.container_args(
            candidate=str(candidate), control=str(control),
            image=foreign, approved=frozenset((reviewed,)),
            sha=C, tree=TREE, run_id="123", attempt="1", nonce="f" * 32,
        )

def test_symlink_or_dotdot_mount_is_denied_before_oci(tmp_path):
    candidate = tmp_path / "candidate"
    control = tmp_path / "control"
    candidate.mkdir()
    control.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(candidate, target_is_directory=True)
    ref = "ghcr.io/mbalbo2023/porota-rc6-qa@" + D
    for source in (str(alias), str(tmp_path / "candidate/../candidate"),
                   str(tmp_path / "missing"), str(control)):
        with pytest.raises(outer.OCIHOLD):
            outer.container_args(
                candidate=source, control=str(control), image=ref,
                sha=C, tree=TREE, run_id="123", attempt="1", nonce="f" * 32,
                approved=frozenset((ref,)),
            )


def _archive(name: str, content: bytes, *, link=False):
    bio = BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as archive:
        item = tarfile.TarInfo(name)
        item.size = len(content)
        if link:
            item.type = tarfile.SYMTYPE
            item.linkname = "../../secrets"
            item.size = 0
        archive.addfile(item, BytesIO(content if not link else b""))
    return bio.getvalue()


def test_container_raw_file_transfer_checks_tar_structure_without_extraction():
    assert outer.read_docker_cp_tar(_archive("full-collected.json", b"{}"),
                                    expected="full-collected.json", max_bytes=16) == b"{}"
    for blob in (_archive("../escape", b"SECRET"),
                 _archive("full-collected.json", b"SECRET", link=True),
                 _archive("full-collected.json", b"X" * 17)):
        with pytest.raises(outer.OCIHOLD):
            outer.read_docker_cp_tar(blob, expected="full-collected.json", max_bytes=16)


def test_publisher_parses_real_junit_not_candidate_success_flag():
    from xml.etree import ElementTree as ET
    raw = receipt_input()
    root = ET.Element("testsuite", tests="4", failures="0", errors="0", skipped="0")
    for record in raw["cases"]:
        item = ET.SubElement(root, "testcase")
        props = ET.SubElement(item, "properties")
        ET.SubElement(props, "property", name="rc6_nodeid_sha256",
                      value=record["nodeid_sha256"])
    xml = ET.tostring(root)
    report = outer.review_private_files(
        full_collected=json.dumps(raw["full_manifest"]).encode(),
        subset_collected=json.dumps(raw["subset_manifest"]).encode(),
        full_executed=json.dumps(raw["full_manifest"]).encode(),
        junit=xml, pytest_exit_code=0,
        trusted_main_sha=T, candidate_sha=C, tree=TREE, qa_image_digest=D,
        run_id="123", attempt="1", expected_full=4, expected_subset=3,
    )
    assert report["status"] == "READY_FOR_PHYSICAL_ATTESTATION"
    assert report["qa_certified"] is False
    assert report["runtime_artifact_digest"] is None
    assert report["junit_sha256"] != "e" * 64
    root.set("tests", "5")
    with pytest.raises(outer.OCIHOLD):
        outer.review_private_files(
            full_collected=json.dumps(raw["full_manifest"]).encode(),
            subset_collected=json.dumps(raw["subset_manifest"]).encode(),
            full_executed=json.dumps(raw["full_manifest"]).encode(),
            junit=ET.tostring(root), pytest_exit_code=0,
            trusted_main_sha=T, candidate_sha=C, tree=TREE,
            qa_image_digest=D, run_id="123", attempt="1",
            expected_full=4, expected_subset=3,
        )
    root.set("tests", "4")
    root.set("skipped", "1")
    with pytest.raises(outer.OCIHOLD):
        outer.review_private_files(
            full_collected=json.dumps(raw["full_manifest"]).encode(),
            subset_collected=json.dumps(raw["subset_manifest"]).encode(),
            full_executed=json.dumps(raw["full_manifest"]).encode(),
            junit=ET.tostring(root), pytest_exit_code=0,
            trusted_main_sha=T, candidate_sha=C, tree=TREE,
            qa_image_digest=D, run_id="123", attempt="1",
            expected_full=4, expected_subset=3,
        )
    root.set("skipped", "0")
    ET.SubElement(root[0], "skipped")
    with pytest.raises(outer.OCIHOLD):
        outer.review_private_files(
            full_collected=json.dumps(raw["full_manifest"]).encode(),
            subset_collected=json.dumps(raw["subset_manifest"]).encode(),
            full_executed=json.dumps(raw["full_manifest"]).encode(),
            junit=ET.tostring(root), pytest_exit_code=0,
            trusted_main_sha=T, candidate_sha=C, tree=TREE,
            qa_image_digest=D, run_id="123", attempt="1",
            expected_full=4, expected_subset=3,
        )
