import json
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.porota_validate_deploy_artifact as artifact_guard
from scripts.porota_validate_deploy_artifact import is_runtime_relevant, validate


def write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def cli_fixture(tmp_path):
    """An exact Git checkout with a complete tiny image and one opaque host unit."""
    repo = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    repo.mkdir()
    artifact.mkdir()
    files = {
        "bv_paper_runtime.py": "import helper\n",
        "o_dashboard.py": "pass\n",
        "helper.py": "pass\n",
        "tests/test_helper.py": "pass\n",
        "systemd/porota-opaque-rc6.service": (
            "[Service]\nExecStart=/usr/bin/docker exec paper python /app/helper.py\n"
        ),
    }
    for rel, body in files.items():
        write(repo, rel, body)
        if not rel.startswith("tests/"):
            write(artifact, rel, body)
    policy = {
        "schema_version": 2,
        "units": {
            "systemd/porota-opaque-rc6.service": {"role": "ACTIVE_SERVICE"},
        },
        "external_host_units": {
            "systemd/ppi-watch.service": {"role": "ACTIVE_SERVICE"},
        },
    }
    write(repo, "ops/policy/host-control-plane-reconciliation-v2.json",
          json.dumps(policy))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(repo), "-c", "user.name=Porota QA",
        "-c", "user.email=qa@example.invalid", "commit", "-qm", "fixture",
    ], check=True)
    candidate_sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
    ).strip()
    return repo, artifact, candidate_sha


def test_main_embeds_git_python_provenance_without_claiming_runtime(tmp_path, monkeypatch, capsys):
    """Removing the main() provenance call must make this fail."""
    repo, artifact, candidate_sha = cli_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(sys, "argv", [
        "porota_validate_deploy_artifact.py", "--repo-root", str(repo),
        "--artifact-root", str(artifact), "--manifest-out", str(manifest),
        "--candidate-sha", candidate_sha,
    ])

    assert artifact_guard.main() == 0
    data = json.loads(manifest.read_text(encoding="utf-8"))
    audit = data["python_source_provenance"]
    rows = {row["path"]: row for row in audit["sources"]}
    assert data["status"] == "GREEN"
    assert data["candidate_sha"] == candidate_sha
    assert data["candidate_checkout_match"] == "MATCH"
    assert audit["source_count"] == 4
    assert audit["status"] == "NO_VERIFICADO"
    assert audit["unresolved_host_services"] == ["systemd/porota-opaque-rc6.service"]
    assert rows["bv_paper_runtime.py"]["classification"] == "ENTRYPOINT_DECLARED"
    assert rows["o_dashboard.py"]["classification"] == "ENTRYPOINT_DECLARED"
    assert rows["helper.py"]["classification"] == "DEPENDENCY_DECLARED"
    assert rows["tests/test_helper.py"]["classification"] == "TEST_ONLY"
    assert all(row["runtime_proven"] == "NO_VERIFICADO" for row in rows.values())
    assert "POROTA_ARTIFACT_INTEGRITY=GREEN" in capsys.readouterr().out


def test_main_preserves_existing_cli_until_workflow_passes_candidate_sha(tmp_path, monkeypatch):
    """An omitted new flag must not break the pre-existing Action invocation."""
    repo, artifact, _candidate_sha = cli_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(sys, "argv", [
        "porota_validate_deploy_artifact.py", "--repo-root", str(repo),
        "--artifact-root", str(artifact), "--manifest-out", str(manifest),
    ])

    assert artifact_guard.main() == 0
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["candidate_sha"] is None
    assert data["candidate_checkout_match"] == "NO_VERIFICADO"
    assert data["python_source_provenance"]["source_count"] == 4


@pytest.mark.parametrize("dirty_kind", ["unstaged", "staged-index-only"])
def test_main_rejects_dirty_tracked_checkout_even_when_head_matches(
    tmp_path, monkeypatch, capsys, dirty_kind,
):
    """A clean HEAD value alone cannot authorize modified tracked source bytes."""
    repo, artifact, candidate_sha = cli_fixture(tmp_path)
    write(repo, "helper.py", "CHANGED = True\n")
    if dirty_kind == "staged-index-only":
        subprocess.run(["git", "-C", str(repo), "add", "helper.py"], check=True)
        write(repo, "helper.py", "pass\n")  # Worktree clean against HEAD; index is not.
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(sys, "argv", [
        "porota_validate_deploy_artifact.py", "--repo-root", str(repo),
        "--artifact-root", str(artifact), "--manifest-out", str(manifest),
        "--candidate-sha", candidate_sha,
    ])

    assert artifact_guard.main() == 1
    assert not manifest.exists()
    output = capsys.readouterr()
    assert "POROTA_ARTIFACT_INTEGRITY=FAILED_CANDIDATE_CHECKOUT_DIRTY" in output.err
    assert "POROTA_ARTIFACT_INTEGRITY=GREEN" not in output.out


def test_main_allows_untracked_ci_outputs_with_exact_candidate(tmp_path, monkeypatch):
    """Build-created untracked files must not veto a clean tracked checkout."""
    repo, artifact, candidate_sha = cli_fixture(tmp_path)
    write(repo, "build/untracked-generated.dat", "transient\n")
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(sys, "argv", [
        "porota_validate_deploy_artifact.py", "--repo-root", str(repo),
        "--artifact-root", str(artifact), "--manifest-out", str(manifest),
        "--candidate-sha", candidate_sha,
    ])

    assert artifact_guard.main() == 0
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["candidate_checkout_match"] == "MATCH"
    assert data["status"] == "GREEN"


def test_main_rejects_git_failure_without_claiming_candidate_match(
    tmp_path, monkeypatch, capsys,
):
    """Missing Git metadata must fail with an auditable receipt, not a traceback."""
    repo, artifact, candidate_sha = cli_fixture(tmp_path)
    (repo / ".git").rename(tmp_path / "detached-git-metadata")
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(sys, "argv", [
        "porota_validate_deploy_artifact.py", "--repo-root", str(repo),
        "--artifact-root", str(artifact), "--manifest-out", str(manifest),
        "--candidate-sha", candidate_sha,
    ])

    assert artifact_guard.main() == 1
    assert not manifest.exists()
    output = capsys.readouterr()
    assert "POROTA_ARTIFACT_INTEGRITY=FAILED_CANDIDATE_GIT" in output.err
    assert "POROTA_ARTIFACT_INTEGRITY=GREEN" not in output.out


@pytest.mark.parametrize("candidate_sha", ["0" * 40, "HEAD", ""])
def test_main_rejects_wrong_or_unpinned_candidate_sha(tmp_path, monkeypatch, capsys,
                                                       candidate_sha):
    """An invalid candidate must never receive a GREEN artifact receipt."""
    repo, artifact, _real_sha = cli_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(sys, "argv", [
        "porota_validate_deploy_artifact.py", "--repo-root", str(repo),
        "--artifact-root", str(artifact), "--manifest-out", str(manifest),
        "--candidate-sha", candidate_sha,
    ])

    assert artifact_guard.main() == 1
    assert not manifest.exists()
    output = capsys.readouterr()
    assert "POROTA_ARTIFACT_INTEGRITY=FAILED_CANDIDATE_SHA" in output.err
    assert "POROTA_ARTIFACT_INTEGRITY=GREEN" not in output.out


@pytest.mark.parametrize("policy_failure", [
    "bad-schema", "missing-role", "unknown-role", "symlink",
])
def test_main_rejects_invalid_host_policy_before_writing_manifest(
    tmp_path, monkeypatch, capsys, policy_failure,
):
    """An absent trustworthy policy must not silently erase host roots."""
    repo, artifact, candidate_sha = cli_fixture(tmp_path)
    policy = repo / "ops/policy/host-control-plane-reconciliation-v2.json"
    if policy_failure == "bad-schema":
        policy.write_text('{"schema_version": 999, "units": {}}', encoding="utf-8")
    elif policy_failure == "missing-role":
        policy.write_text(json.dumps({
            "schema_version": 2,
            "units": {"systemd/porota-opaque-rc6.service": {"reason": "lost role"}},
        }), encoding="utf-8")
    elif policy_failure == "unknown-role":
        policy.write_text(json.dumps({
            "schema_version": 2,
            "units": {"systemd/porota-opaque-rc6.service": {"role": "ACTIVE"}},
        }), encoding="utf-8")
    else:
        target = tmp_path / "host-policy.json"
        target.write_text(policy.read_text(encoding="utf-8"), encoding="utf-8")
        policy.unlink()
        policy.symlink_to(target)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([
        "git", "-C", str(repo), "-c", "user.name=Porota QA",
        "-c", "user.email=qa@example.invalid", "commit", "-qm", "malformed policy",
    ], check=True)
    candidate_sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
    ).strip()
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(sys, "argv", [
        "porota_validate_deploy_artifact.py", "--repo-root", str(repo),
        "--artifact-root", str(artifact), "--manifest-out", str(manifest),
        "--candidate-sha", candidate_sha,
    ])

    assert artifact_guard.main() == 1
    assert not manifest.exists()
    output = capsys.readouterr()
    assert "POROTA_ARTIFACT_INTEGRITY=FAILED_PYTHON_SOURCE_PROVENANCE" in output.err


@pytest.mark.parametrize("symlink_component", ["file", "ancestor"])
@pytest.mark.parametrize("location", ["outside", "inside"])
def test_python_source_inventory_rejects_symlink(tmp_path, symlink_component, location):
    repo = tmp_path / "repo"
    repo.mkdir()
    target_dir = tmp_path / "outside" if location == "outside" else repo / "source"
    target_dir.mkdir()
    write(target_dir, "escape.py", "pass\n")
    if symlink_component == "file":
        (repo / "escape.py").symlink_to(target_dir / "escape.py")
        path = "escape.py"
    else:
        (repo / "package").symlink_to(target_dir, target_is_directory=True)
        path = "package/escape.py"

    with pytest.raises(ValueError, match="PYTHON_SOURCE_INVENTORY_INVALID"):
        artifact_guard.python_source_provenance(repo, [path], entrypoints=[path])


@pytest.mark.parametrize("symlink_component", ["file", "ancestor"])
@pytest.mark.parametrize("location", ["outside", "inside"])
def test_active_host_service_rejects_symlink(tmp_path, symlink_component, location):
    repo = tmp_path / "repo"
    repo.mkdir()
    target_dir = tmp_path / "outside" if location == "outside" else repo / "unit_sources"
    target_dir.mkdir()
    write(repo, "runner.py", "pass\n")
    unit = "systemd/porota-rc6.service"
    unit_body = "[Service]\nExecStart=/usr/bin/python3 /opt/porota-trading/runner.py\n"
    if symlink_component == "file":
        (repo / "systemd").mkdir()
        write(target_dir, "porota-rc6.service", unit_body)
        (repo / unit).symlink_to(target_dir / "porota-rc6.service")
    else:
        write(target_dir, "porota-rc6.service", unit_body)
        (repo / "systemd").symlink_to(target_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="PYTHON_HOST_SERVICE_INVALID"):
        artifact_guard.python_source_provenance(
            repo, ["runner.py"], host_services={unit: "ACTIVE_SERVICE"}
        )


@pytest.mark.parametrize("exec_start", [
    "/bin/echo /opt/porota-trading/runner.py",
    "/usr/bin/true /opt/porota-trading/runner.py",
    "/bin/sh -c '/usr/bin/python3 /opt/porota-trading/runner.py'",
    "/usr/bin/docker exec observer python /app/runner.py",
    "/usr/bin/python3 -m inspect /opt/porota-trading/runner.py",
])
def test_host_service_non_direct_python_command_stays_unresolved(tmp_path, exec_start):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "runner.py", "pass\n")
    unit = "systemd/porota-rc6.service"
    write(repo, unit, "[Service]\nExecStart=" + exec_start + "\n")

    result = artifact_guard.python_source_provenance(
        repo, ["runner.py"], host_services={unit: "ACTIVE_SERVICE"}
    )

    assert result["unresolved_host_services"] == [unit]
    assert result["sources"][0]["classification"] == "REVIEW_REQUIRED"
    assert result["sources"][0]["runtime_proven"] == "NO_VERIFICADO"


@pytest.mark.parametrize("exec_start", [
    "/usr/bin/python3 /opt/porota-trading/runner.py",
    "/usr/bin/env python3 -B /opt/porota-trading/runner.py",
    "/usr/bin/env JOB_KIND=paper python3.11 /app/runner.py",
])
def test_host_service_direct_python_interpreter_is_declared(tmp_path, exec_start):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "runner.py", "pass\n")
    unit = "systemd/porota-rc6.service"
    write(repo, unit, "[Service]\nExecStart=" + exec_start + "\n")

    result = artifact_guard.python_source_provenance(
        repo, ["runner.py"], host_services={unit: "ACTIVE_SERVICE"}
    )

    assert result["unresolved_host_services"] == []
    assert result["sources"][0]["classification"] == "HOST_ENTRYPOINT_DECLARED"
    assert result["sources"][0]["runtime_proven"] == "NO_VERIFICADO"


def test_python_inventory_counts_hidden_ci_and_root_tests(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for rel in (
        ".github/workflows/rc6_apply_patch.py",
        "test_hf6_history_data912_v2.py",
        "tests/test_regular.py",
        "runtime.py",
    ):
        write(repo, rel, "pass\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    assert artifact_guard.git_tracked_python_files(repo) == [
        ".github/workflows/rc6_apply_patch.py",
        "runtime.py",
        "test_hf6_history_data912_v2.py",
        "tests/test_regular.py",
    ]


def test_unwired_bundle_source_requires_review_not_runtime_active(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = ["runner.py", "unused.py", "test_legacy.py",
             "tests/test_unit.py", ".github/workflows/rc6_apply_patch.py"]
    for path in paths:
        write(repo, path, "pass\n")

    result = artifact_guard.python_source_provenance(
        repo, paths, entrypoints=["runner.py"]
    )
    rows = {row["path"]: row for row in result["sources"]}

    assert result["source_count"] == 5
    assert result["status"] == "NO_VERIFICADO"
    assert rows["runner.py"]["classification"] == "ENTRYPOINT_DECLARED"
    assert rows["runner.py"]["runtime_proven"] == "NO_VERIFICADO"
    assert rows["unused.py"]["classification"] == "REVIEW_REQUIRED"
    assert rows["unused.py"]["review_required"] is True
    assert rows["unused.py"]["provenance"] == []
    assert rows["test_legacy.py"]["classification"] == "TEST_ONLY"
    assert rows["tests/test_unit.py"]["classification"] == "TEST_ONLY"
    assert rows[".github/workflows/rc6_apply_patch.py"]["classification"] == "CI_ONLY"
    assert all(row["runtime_proven"] == "NO_VERIFICADO" for row in rows.values())


def test_active_service_declaration_tracks_source_cycle_subprocess_and_import(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    unit = "systemd/porota-iol-shadow-collector-rc6.service"
    write(repo, unit,
          "[Service]\nExecStart=/usr/bin/python3 "
          "/opt/porota-trading/scripts/rc6_source_cycle_dispatch.py\n")
    dispatcher = "scripts/rc6_source_cycle_dispatch.py"
    child = "scripts/rc6_iol_shadow_collect.py"
    write(repo, dispatcher, '''
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
STAGES = (("iol", "scripts/rc6_iol_shadow_collect.py", 110, "iol.json"),)
def run_cycle(runner=subprocess.run):
    for name, relative, timeout, output in STAGES:
        runner([sys.executable, str(ROOT / relative)])
''')
    write(repo, child, "import iol_helper\n")
    write(repo, "iol_helper.py", "pass\n")
    paths = [dispatcher, child, "iol_helper.py"]

    result = artifact_guard.python_source_provenance(
        repo, paths, host_services={unit: "ACTIVE_SERVICE"}
    )
    rows = {row["path"]: row for row in result["sources"]}

    assert rows[dispatcher]["classification"] == "HOST_ENTRYPOINT_DECLARED"
    assert rows[dispatcher]["provenance"] == ["HOST_ENTRYPOINT_DECLARED:" + unit]
    assert rows[child]["classification"] == "DEPENDENCY_DECLARED"
    assert "HOST_SOURCE_CYCLE_STAGE:" + dispatcher in rows[child]["provenance"]
    assert "STATIC_IMPORT:" + child in rows["iol_helper.py"]["provenance"]
    assert result["status"] == "NO_VERIFICADO"
    assert all(row["runtime_proven"] == "NO_VERIFICADO" for row in rows.values())


def test_aliased_literal_dynamic_import_reaches_exact_package_member(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "runner.py", '''
from importlib import import_module as load
load("pkg.worker")
''')
    write(repo, "pkg/__init__.py", "pass\n")
    write(repo, "pkg/worker.py", "pass\n")

    result = artifact_guard.python_source_provenance(
        repo, ["runner.py", "pkg/__init__.py", "pkg/worker.py"],
        entrypoints=["runner.py"],
    )
    rows = {row["path"]: row for row in result["sources"]}

    for path in ("pkg/__init__.py", "pkg/worker.py"):
        assert rows[path]["classification"] == "DEPENDENCY_DECLARED"
        assert "LITERAL_DYNAMIC_IMPORT:runner.py" in rows[path]["provenance"]
        assert rows[path]["runtime_proven"] == "NO_VERIFICADO"


def test_unresolved_dynamic_import_requires_review_even_in_declared_entrypoint(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "runner.py", '''
from importlib import import_module as load
def run(module_name):
    return load(module_name)
''')
    write(repo, "unknown_target.py", "pass\n")

    result = artifact_guard.python_source_provenance(
        repo, ["runner.py", "unknown_target.py"], entrypoints=["runner.py"]
    )
    rows = {row["path"]: row for row in result["sources"]}

    assert rows["runner.py"]["classification"] == "REVIEW_REQUIRED"
    assert rows["runner.py"]["review_required"] is True
    assert result["unresolved_dynamic_imports"] == [
        {"source": "runner.py", "reason": "DYNAMIC_IMPORT_ARGUMENT_NOT_LITERAL"}
    ]
    assert rows["unknown_target.py"]["classification"] == "REVIEW_REQUIRED"


def test_unresolved_source_cycle_stage_marks_launcher_for_review(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "scripts/dispatcher.py", '''
STAGES = (("iol", "scripts/missing_collector.py", 110, "iol.json"),)
def main(runner):
    for name, relative, timeout, output in STAGES:
        runner(["python", relative])
''')

    result = artifact_guard.python_source_provenance(
        repo, ["scripts/dispatcher.py"], entrypoints=["scripts/dispatcher.py"]
    )

    assert result["unresolved_source_cycle_stages"] == [
        {"source": "scripts/dispatcher.py", "target": "scripts/missing_collector.py"}
    ]
    assert result["sources"][0]["classification"] == "REVIEW_REQUIRED"
    assert result["sources"][0]["review_required"] is True


def test_unrelated_stages_table_does_not_claim_subprocess_wiring(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write(repo, "runner.py", '''
STAGES = (("iol", "scripts/unrelated.py", 110, "iol.json"),)
def report(runner):
    for name, relative, timeout, output in STAGES:
        runner(relative)
''')
    write(repo, "scripts/unrelated.py", "pass\n")

    result = artifact_guard.python_source_provenance(
        repo, ["runner.py", "scripts/unrelated.py"], entrypoints=["runner.py"]
    )
    rows = {row["path"]: row for row in result["sources"]}

    assert rows["scripts/unrelated.py"]["classification"] == "REVIEW_REQUIRED"
    assert rows["scripts/unrelated.py"]["provenance"] == []


def test_missing_tracked_runtime_file_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    repo.mkdir()
    artifact.mkdir()
    write(repo, "app.py", "import dependency\n")
    write(repo, "dependency.py", "VALUE = 1\n")
    write(artifact, "app.py", "import dependency\n")

    result = validate(repo, artifact, ["app.py", "dependency.py"])

    assert result["status"] == "FAILED"
    assert result["missing_runtime_files"] == ["dependency.py"]
    assert result["missing_local_imports"] == [
        {"file": "app.py", "module": "dependency", "expected_path": "dependency.py"}
    ]


def test_complete_artifact_is_green_and_hashed(tmp_path):
    repo = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    repo.mkdir()
    artifact.mkdir()
    write(repo, "app.py", "import dependency\n")
    write(repo, "dependency.py", "VALUE = 1\n")
    write(artifact, "app.py", "import dependency\n")
    write(artifact, "dependency.py", "VALUE = 1\n")

    result = validate(repo, artifact, ["app.py", "dependency.py"])

    assert result["status"] == "GREEN"
    assert result["missing_runtime_files"] == []
    assert result["missing_local_imports"] == []
    assert {f["path"] for f in result["files"]} == {"app.py", "dependency.py"}
    assert all(len(f["sha256"]) == 64 for f in result["files"])


def test_literal_dynamic_import_is_checked(tmp_path):
    repo = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    repo.mkdir()
    artifact.mkdir()
    write(repo, "app.py", 'import importlib\nimportlib.import_module("localmod")\n')
    write(repo, "localmod.py", "VALUE = 1\n")
    write(artifact, "app.py", 'import importlib\nimportlib.import_module("localmod")\n')

    result = validate(repo, artifact, ["app.py", "localmod.py"])

    assert result["status"] == "FAILED"
    assert result["missing_local_imports"][0]["module"] == "localmod"


def test_static_binding_assets_are_runtime_relevant():
    assert is_runtime_relevant("n_instrument_watchlist.json")
    assert is_runtime_relevant("POROTA_SECTOR_MAP_V1.csv")


def test_dependency_lock_is_runtime_relevant():
    assert is_runtime_relevant("requirements.lock.txt")

def native_fixture(tmp_path):
    repo=tmp_path/"repo";artifact=tmp_path/"artifact"
    repo.mkdir();artifact.mkdir()
    source="PyMODINIT_FUNC PyInit__bounded(void) { return 0; }\n"
    write(repo,"pkg/_bounded.c",source);write(artifact,"pkg/_bounded.c",source)
    return repo,artifact

def test_native_source_and_binary_assets_are_automatically_runtime_relevant():
    assert is_runtime_relevant("pkg/_bounded.c")
    assert is_runtime_relevant("pkg/contract.h")
    assert is_runtime_relevant("pkg/_bounded.so")
    assert not is_runtime_relevant("tests/fixture.c")

def test_missing_compiled_module_is_rejected_from_its_source_definition(tmp_path):
    repo,artifact=native_fixture(tmp_path)
    result=validate(repo,artifact,["pkg/_bounded.c"])
    assert result["status"]=="FAILED"
    assert result["missing_native_extensions"][0]["module"]=="_bounded"

def test_compiled_module_is_hashed_in_exact_image_manifest(tmp_path):
    repo,artifact=native_fixture(tmp_path)
    (artifact/"pkg/_bounded.so").write_bytes(b"synthetic closure asset")
    result=validate(repo,artifact,["pkg/_bounded.c"])
    assert result["status"]=="GREEN"
    assert {f["path"] for f in result["files"]}=={"pkg/_bounded.c","pkg/_bounded.so"}
    assert result["native_extensions"][0]["path"]=="pkg/_bounded.so"
    assert len(result["native_extensions"][0]["sha256"])==64

def test_multiple_possible_native_binaries_are_rejected(tmp_path):
    repo,artifact=native_fixture(tmp_path)
    (artifact/"pkg/_bounded.so").write_bytes(b"one")
    (artifact/"pkg/_bounded.abi3.so").write_bytes(b"two")
    result=validate(repo,artifact,["pkg/_bounded.c"])
    assert result["status"]=="FAILED" and result["missing_native_extensions"]

def test_aliased_native_binary_is_rejected(tmp_path):
    repo,artifact=native_fixture(tmp_path)
    target=tmp_path/"outside.so";target.write_bytes(b"synthetic outside")
    (artifact/"pkg/_bounded.so").symlink_to(target)
    result=validate(repo,artifact,["pkg/_bounded.c"])
    assert result["status"]=="FAILED" and result["missing_native_extensions"]
