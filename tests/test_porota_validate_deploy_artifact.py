from pathlib import Path

from scripts.porota_validate_deploy_artifact import is_runtime_relevant, validate


def write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


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
