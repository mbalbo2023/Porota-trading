import sys
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from scripts.porota_dependency_repro_audit import (
    audit,
    audit_installed_closure,
    main,
    requirement_rows,
)


def test_exact_requirement_is_recognized():
    rows = requirement_rows("requests==2.34.2\n# comment\n")
    assert rows == [{"requirement": "requests==2.34.2", "exact_pin": True}]


def test_source_ranges_are_allowed_when_lock_is_exact():
    result = audit(
        "requests>=2.32\npandas>=2,<3\n",
        "requests==2.34.2\npandas==2.3.3\n",
        "FROM python:3.11-slim@sha256:" + "a"*64 + "\nRUN pip install -r requirements.lock.txt\n",
    )
    assert result["status"] == "INPUTS_PINNED_ONLY"
    assert result["lock_non_exact_requirements"] == []


def test_non_exact_lock_is_reported():
    result = audit(
        "requests>=2.32\n",
        "requests>=2.32\n",
        "FROM python:3.11-slim@sha256:" + "a"*64 + "\nRUN pip install -r requirements.lock.txt\n",
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["lock_non_exact_requirements"] == ["requests>=2.32"]


def test_unpinned_base_image_is_reported():
    result = audit(
        "requests>=2.32\n",
        "requests==2.34.2\n",
        "FROM python:3.11-slim\nRUN pip install -r requirements.lock.txt\n",
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["base_image_digest_pinned"] is False


def test_docker_must_install_the_lock():
    result = audit(
        "requests>=2.32\n",
        "requests==2.34.2\n",
        "FROM python:3.11-slim@sha256:" + "b"*64 + "\nRUN pip install -r requirements.txt\n",
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["docker_uses_lock"] is False

def test_every_build_stage_image_must_be_digest_pinned():
    pinned="python:3.11-slim@sha256:"+"a"*64
    result=audit("requests>=2\n","requests==2.34.2\n",
        "FROM "+pinned+" AS headers\nFROM python:3.11-bookworm AS compiler\nFROM "+pinned+
        "\nRUN pip install -r requirements.lock.txt\n")
    assert result["status"]=="REPRODUCIBILITY_GAP"
    assert result["unpinned_stage_images"]==["python:3.11-bookworm"]

def test_immutable_build_only_compiler_and_runtime_bases_pass():
    pinned="python:3.11-slim@sha256:"+"a"*64
    builder="python:3.11-bookworm@sha256:"+"b"*64
    result=audit("requests>=2\n","requests==2.34.2\n",
        "FROM "+pinned+" AS headers\nFROM "+builder+" AS compiler\nFROM "+pinned+
        "\nRUN pip install -r requirements.lock.txt\n")
    assert result["status"]=="INPUTS_PINNED_ONLY"
    assert result["build_stage_images"]==[pinned,builder,pinned]
    assert result["unpinned_stage_images"]==[]


_PINNED_DOCKER = (
    "FROM python:3.11-slim@sha256:" + "a" * 64
    + "\nRUN pip install -r requirements.lock.txt\n"
)


def test_missing_direct_dependency_is_not_declared_reproducible():
    result = audit(
        "requests>=2\npandas>=2\n",
        "requests==2.34.2\n",
        _PINNED_DOCKER,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["missing_direct_pins"] == ["pandas"]


def test_direct_dependency_pin_must_satisfy_source_constraint():
    result = audit("requests>=3\n", "requests==2.34.2\n", _PINNED_DOCKER)
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["incompatible_direct_pins"] == ["requests>=3"]


def test_duplicate_normalized_lock_name_is_a_gap():
    result = audit(
        "requests>=2\n",
        "requests==2.34.2\nRequests==2.34.2\n",
        _PINNED_DOCKER,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["duplicate_lock_names"] == ["requests"]


def test_marker_resolution_uses_docker_python_not_runner():
    inactive = audit(
        'requests>=2; python_version >= "3.12"\n',
        "pandas==2.3.3\n",
        _PINNED_DOCKER,
    )
    assert inactive["status"] == "INPUTS_PINNED_ONLY"
    active = audit(
        'requests>=2; python_version < "3.12"\n',
        "pandas==2.3.3\n",
        _PINNED_DOCKER,
    )
    assert active["status"] == "REPRODUCIBILITY_GAP"
    assert active["missing_direct_pins"] == ["requests"]


def _inspect_report(packages, *, python_version="3.11"):
    env = default_environment()
    env["python_version"] = python_version
    env["python_full_version"] = python_version + ".9"
    env["sys_platform"] = "linux"
    return {
        "environment": env,
        "installed": [
            {"metadata": {"name": name, "version": version, "requires_dist": requires}}
            for name, version, requires in packages
        ],
    }


_IMAGE_ID = "sha256:" + "f" * 64


def test_installed_closure_requires_all_active_transitive_pins_even_if_pip_check_passes():
    report = _inspect_report([
        ("pytest", "9.1.1", ["packaging>=22"]),
        ("packaging", "26.3", []),
        ("pip", "25.3", []),
    ])
    result = audit_installed_closure(
        "pytest==9.1.1\n", report, image_id=_IMAGE_ID, pip_check_ok=True
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["missing_transitive_pins"] == ["packaging"]
    assert "packaging" in result["unexpected_installed"]


def test_installed_closure_detects_setuptools_dependency_from_ppi_client():
    report = _inspect_report([
        ("ppi_client", "1.3.0", ["setuptools>=52.0.0"]),
        ("setuptools", "84.2.1", []),
        ("pip", "25.3", []),
    ])
    result = audit_installed_closure(
        "ppi-client==1.3.0\n", report, image_id=_IMAGE_ID, pip_check_ok=True
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["missing_transitive_pins"] == ["setuptools"]


def test_rc6_python311_lock_pins_ppi_and_pytest_transitives_without_ambient_tooling():
    """Guard two Python 3.11 resolver additions, not a synthetic image receipt."""
    lock_text = (Path(__file__).resolve().parents[1] / "requirements.lock.txt").read_text(
        encoding="utf-8"
    )
    rows = requirement_rows(lock_text)
    assert len(rows) == 155
    assert all(row["exact_pin"] for row in rows)
    requirements = [Requirement(str(row["requirement"])) for row in rows]
    pins = {
        canonicalize_name(req.name): next(iter(req.specifier)).version
        for req in requirements
    }
    assert len(pins) == len(rows)  # No duplicate distribution under an alias.
    assert {name: pins[name] for name in ("pytest", "ppi-client", "packaging", "setuptools")} == {
        "pytest": "9.1.1",
        "ppi-client": "1.3.0",
        "packaging": "26.3",
        "setuptools": "84.2.1",
    }

    # A synthetic projection of the two observed Python 3.11 metadata edges.
    # No pip/wheel is inserted: those need independent, image-bound base evidence.
    report = _inspect_report([
        ("pytest", "9.1.1", ["packaging>=22"]),
        ("packaging", "26.3", []),
        ("ppi_client", "1.3.0", ["setuptools>=52.0.0"]),
        ("setuptools", "84.2.1", []),
    ])
    projection = "".join(f"{name}=={pins[name]}\n" for name in (
        "pytest", "packaging", "ppi-client", "setuptools"
    ))
    result = audit_installed_closure(
        projection, report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["graph_status"] == "CONSISTENT"
    assert result["status"] == "NO_VERIFICADO"
    assert result["locked_packages_verified"] == 4
    assert result["missing_transitive_pins"] == []
    assert result["unexpected_installed"] == []

    # A system-provided installer may not be silently exempted from exact closure.
    report["installed"].append({
        "metadata": {"name": "pip", "version": "26.2.2", "requires_dist": []}
    })
    with_unpinned_installer = audit_installed_closure(
        projection, report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert with_unpinned_installer["status"] == "REPRODUCIBILITY_GAP"
    assert with_unpinned_installer["unexpected_installed"] == ["pip"]


def test_installed_closure_detects_incompatible_pin_and_unlocked_extra():
    report = _inspect_report([
        ("Requests", "2.31.0", []),
        ("Pandas", "2.3.3", []),
        ("pip", "25.3", []),
    ])
    result = audit_installed_closure(
        "requests==2.34.2\n", report, image_id=_IMAGE_ID, pip_check_ok=True
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["incompatible_installed"] == ["requests"]
    assert result["unexpected_installed"] == ["pandas", "pip"]


def test_installed_closure_evaluates_transitive_markers_for_target_python():
    requirements = ['urllib3>=2; python_version < "3.12"', 'colorama; sys_platform == "win32"']
    report_311 = _inspect_report([
        ("requests", "2.34.2", requirements),
        ("pip", "25.3", []),
    ], python_version="3.11")
    result_311 = audit_installed_closure(
        "requests==2.34.2\n", report_311, image_id=_IMAGE_ID, pip_check_ok=True
    )
    assert result_311["status"] == "REPRODUCIBILITY_GAP"
    assert result_311["missing_transitive_pins"] == ["urllib3"]
    assert "colorama" not in result_311["missing_transitive_pins"]

    report_312 = _inspect_report([
        ("requests", "2.34.2", requirements),
        ("pip", "25.3", []),
    ], python_version="3.12")
    result_312 = audit_installed_closure(
        "requests==2.34.2\npip==25.3\n", report_312, image_id=_IMAGE_ID, pip_check_ok=True
    )
    assert result_312["status"] == "NO_VERIFICADO"
    assert result_312["graph_status"] == "CONSISTENT"


def test_installed_closure_requires_actual_receipt_identity_and_pip_check():
    report = _inspect_report([("requests", "2.34.2", [])])
    no_identity = audit_installed_closure(
        "requests==2.34.2\n", report, image_id="", pip_check_ok=True
    )
    assert no_identity["status"] == "NO_VERIFICADO"
    no_metadata = audit_installed_closure(
        "requests==2.34.2\n", {"installed": report["installed"]},
        image_id=_IMAGE_ID, pip_check_ok=True
    )
    assert no_metadata["status"] == "NO_VERIFICADO"
    pip_red = audit_installed_closure(
        "requests==2.34.2\n", report, image_id=_IMAGE_ID, pip_check_ok=False
    )
    assert pip_red["status"] == "REPRODUCIBILITY_GAP"


def test_complete_pinned_distribution_graph_does_not_certify_image():
    report = _inspect_report([
        ("Requests", "2.34.2", ['urllib3>=2; python_version < "3.12"']),
        ("urllib3", "2.5.0", []),
        ("pip", "25.3", []),
    ])
    result = audit_installed_closure(
        "requests==2.34.2\nurllib3==2.5.0\npip==25.3\n",
        report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "NO_VERIFICADO"
    assert result["graph_status"] == "CONSISTENT"
    assert result["image_id"] == _IMAGE_ID
    assert result["baseline_tools"] == {}
    assert result["locked_packages_verified"] == 3


def test_cli_refuses_to_attest_image_closure_from_unbound_runner(monkeypatch, tmp_path, capsys):
    (tmp_path / "requirements.txt").write_text("requests>=2\n", encoding="utf-8")
    (tmp_path / "requirements.lock.txt").write_text("requests==2.34.2\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(_PINNED_DOCKER, encoding="utf-8")
    monkeypatch.delenv("POROTA_BUILD_SHA", raising=False)
    monkeypatch.delenv("POROTA_CANDIDATE_TREE_SHA", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "dependency_audit", "--repo-root", str(tmp_path),
        "--verify-installed-runtime", "--image-id", _IMAGE_ID,
    ])
    assert main() == 1
    output = capsys.readouterr().out
    assert "POROTA_EXACT_IMAGE_DEPENDENCIES=NO_VERIFICADO" in output


def test_installed_closure_rejects_requested_transitive_extra_without_resolving_it():
    report = _inspect_report([
        ("parent", "1.0", ["child[security]>=1"]),
        ("child", "1.0", ['cryptolib>=1; extra == "security"']),
        ("pip", "25.3", []),
    ])
    result = audit_installed_closure(
        "parent==1.0\nchild==1.0\npip==25.3\n",
        report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["unsupported_requested_extras"] == ["child[security]"]


@pytest.mark.parametrize("extra_line", ["untracked>=2", "@@invalid requirement"])
def test_installed_closure_never_ignores_nonexact_or_malformed_lock_line(extra_line):
    report = _inspect_report([("requests", "2.34.2", []), ("pip", "25.3", [])])
    result = audit_installed_closure(
        "requests==2.34.2\npip==25.3\n" + extra_line + "\n",
        report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert extra_line in result["invalid_lock_rows"]


def test_supplied_digest_cannot_attest_physical_image_even_for_consistent_graph():
    report = _inspect_report([("requests", "2.34.2", []), ("pip", "25.3", [])])
    result = audit_installed_closure(
        "requests==2.34.2\npip==25.3\n", report,
        image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "NO_VERIFICADO"
    assert result["graph_status"] == "CONSISTENT"
    assert result["physical_binding_verified"] is False


def test_unlocked_pip_cannot_be_whitelisted_without_independent_base_receipt():
    report = _inspect_report([("requests", "2.34.2", []), ("pip", "25.3", [])])
    result = audit_installed_closure(
        "requests==2.34.2\n", report,
        image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert "pip" in result["unexpected_installed"]
    assert result["baseline_tools"] == {}


def test_pip_check_must_be_boolean_true():
    report = _inspect_report([("requests", "2.34.2", []), ("pip", "25.3", [])])
    result = audit_installed_closure(
        "requests==2.34.2\npip==25.3\n", report,
        image_id=_IMAGE_ID, pip_check_ok="false",
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"


def test_invalid_requires_dist_metadata_is_not_treated_as_no_dependencies():
    report = _inspect_report([("requests", "2.34.2", []), ("pip", "25.3", [])])
    report["installed"][0]["metadata"]["requires_dist"] = False
    result = audit_installed_closure(
        "requests==2.34.2\npip==25.3\n", report,
        image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["invalid_distribution_metadata"] == ["0"]


def test_unknown_python_patch_marker_is_not_silently_skipped_as_3_11_0():
    result = audit(
        'requests>=2; python_full_version >= "3.11.9"\n',
        "pandas==2.3.3\n", _PINNED_DOCKER,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["unresolved_environment_markers"] == [
        'requests>=2; python_full_version >= "3.11.9"'
    ]


def test_source_requested_extra_requires_closure_analysis_not_source_green():
    result = audit("requests[security]>=2\n", "requests==2.34.2\n", _PINNED_DOCKER)
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["unsupported_requested_extras"] == ["requests[security]"]


def test_inactive_platform_marker_lock_pin_not_required_in_installed_graph():
    report = _inspect_report([("requests", "2.34.2", []), ("pip", "25.3", [])])
    result = audit_installed_closure(
        'requests==2.34.2\npip==25.3\ncolorama==0.4.6; sys_platform == "win32"\n',
        report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["missing_locked_distributions"] == []
    assert result["graph_status"] == "CONSISTENT"
    assert result["status"] == "NO_VERIFICADO"


def test_wildcard_locked_version_is_not_exact():
    report = _inspect_report([("requests", "2.34.2", []), ("pip", "25.3", [])])
    result = audit_installed_closure(
        "requests==2.34.2\npip==25.3\nloose==1.*\n",
        report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert "loose==1.*" in result["invalid_lock_rows"]


def test_invalid_installed_version_is_rejected_before_marker_specifier_comparison():
    report = _inspect_report([("requests", "malformed-version", []), ("pip", "25.3", [])])
    result = audit_installed_closure(
        "requests==2.34.2\npip==25.3\n",
        report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["invalid_distribution_metadata"] == ["0"]


def test_installed_environment_must_have_consistent_typed_python_versions():
    report = _inspect_report([("requests", "2.34.2", []), ("pip", "25.3", [])])
    report["environment"]["python_full_version"] = False
    result = audit_installed_closure(
        "requests==2.34.2\npip==25.3\n", report,
        image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "NO_VERIFICADO"
    assert result["graph_status"] == "NOT_CHECKED"


def test_source_url_requirement_is_not_certified_by_unrelated_version_pin():
    result = audit(
        "parent @ https://example.invalid/download/parent.whl\n",
        "parent==1.0\n", _PINNED_DOCKER,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["unsupported_direct_urls"] == [
        "parent @ https://example.invalid/download/parent.whl"
    ]


def test_transitive_url_does_not_match_arbitrary_installed_name_and_version():
    report = _inspect_report([
        ("parent", "1.0", ["child @ https://example.invalid/download/child.whl"]),
        ("child", "2.0", []),
        ("pip", "25.3", []),
    ])
    result = audit_installed_closure(
        "parent==1.0\nchild==2.0\npip==25.3\n",
        report, image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["unsupported_direct_urls"] == [
        "child @ https://example.invalid/download/child.whl"
    ]


def test_incomplete_python_patch_cannot_skip_required_transitive_marker():
    report = _inspect_report([
        ("parent", "1.0", ['child>=1; python_full_version >= "3.11.9"']),
        ("pip", "25.3", []),
    ])
    report["environment"]["python_full_version"] = "3.11"
    result = audit_installed_closure(
        "parent==1.0\npip==25.3\n", report,
        image_id=_IMAGE_ID, pip_check_ok=True,
    )
    assert result["status"] == "NO_VERIFICADO"
    assert result["graph_status"] == "NOT_CHECKED"


def test_commented_out_docker_lock_install_is_not_counted_as_real_install():
    docker = (
        "FROM python:3.11-slim@sha256:" + "a" * 64 + "\n"
        "# RUN pip install -r requirements.lock.txt\n"
        "RUN pip install -r requirements.txt\n"
    )
    result = audit("requests>=2\n", "requests==2.34.2\n", docker)
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["docker_uses_lock"] is False
