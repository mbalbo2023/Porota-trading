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


def _writer_fixture(tmp_path):
    from rc6_shadow_runtime.archive_format_contract import SOURCE_MEMBERS, writer_codec_restorer_binding
    import shutil
    actual = Path(__file__).absolute().parents[1]
    repo, artifact = tmp_path/'repo', tmp_path/'artifact'
    for name in SOURCE_MEMBERS:
        for root in (repo, artifact):
            (root/name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(actual/name, root/name)
            (root/name).chmod(0o644)
    binding = writer_codec_restorer_binding({name: (repo/name).read_bytes() for name in SOURCE_MEMBERS})
    return repo, artifact, list(SOURCE_MEMBERS), binding


def test_exact_g5_writer_abi_compares_all_eight_source_bytes_without_claiming_native_green(tmp_path):
    repo, artifact, expected, binding = _writer_fixture(tmp_path)
    result = validate(repo, artifact, expected, g5_writer_binding=binding)
    joined = result['g5_writer_byte_binding']
    assert joined['status'] == 'EXACT_BYTES_MATCHED_NOT_PROMOTION'
    assert joined['G5_certification_from_metadata_claimed'] is False
    assert joined['G7_G8_native_execution_still_required'] is True
    assert joined['native_v4_write_enabled'] is False


def test_g5_writer_binding_cannot_omit_worker_default_dispatch(tmp_path):
    repo, artifact, expected, binding = _writer_fixture(tmp_path)
    expected.remove('rc6_shadow_runtime/worker.py')
    result = validate(repo, artifact, expected, g5_writer_binding=binding)
    assert result['status'] == 'FAILED'
    assert result['g5_writer_byte_binding']['failure_signature'] == 'G5_ARTIFACT_COMPLETE_WRITER_ABI_INVENTORY_REQUIRED'


def test_g5_writer_binding_rejects_changed_codec_even_with_complete_runtime_files(tmp_path):
    repo, artifact, expected, binding = _writer_fixture(tmp_path)
    with (artifact/'rc6_shadow_runtime/exact_page_storage.py').open('a') as stream:
        stream.write('\n# mutated codec\n')
    result = validate(repo, artifact, expected, g5_writer_binding=binding)
    assert result['status'] == 'FAILED'
    assert result['g5_writer_byte_binding']['status'] == 'RED'


def test_g5_writer_binding_rejects_source_default_v3_relabelled_v4(tmp_path):
    repo, artifact, expected, binding = _writer_fixture(tmp_path)
    path = repo/'rc6_shadow_runtime/worker.py'
    assert b'kwargs.setdefault("archive_format", "COMPONENT_V3")' in path.read_bytes()
    path.write_bytes(path.read_bytes().replace(b'kwargs.setdefault("archive_format", "COMPONENT_V3")',
        b'kwargs.setdefault("archive_format", "COMPONENT_V4")'))
    (artifact/'rc6_shadow_runtime/worker.py').write_bytes(path.read_bytes())
    result = validate(repo, artifact, expected, g5_writer_binding=binding)
    assert result['status'] == 'FAILED'
    assert result['g5_writer_byte_binding']['status'] == 'RED'
