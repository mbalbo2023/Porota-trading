"""Protected-main isolated pytest bootstrap with digest-pinned candidate hooks.

The plugin and its direct dependency are reused from the frozen candidate
tree **only after their exact independently reviewed SHA256s match**.  They
are installed in sys.modules before candidate project paths become importable.
No candidate code is imported by the GitHub-hosted outer supervisor; this
bootstrap executes solely as an unprivileged child inside the isolated OCI.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import types


REVIEWED_CANDIDATE_SHA = "2c4a6de6ead224589cead1e2859b72bffcb34a9b"
REVIEWED_CANDIDATE_TREE = "d053c2d8be145c83d6ef3bc5a31bb105465a5ab9"
REVIEWED_MODULES = {
    "rc6_ci_shard_quorum.py": "f6015eeede45f651bc4005e23e3613d65bf7835aa4179c2983ec3a917e91ac30",
    "rc6_ci_pytest_identity.py": "d3b801c37e58f24c72ac1bb7e3888d451ce585a0b11585692afe0c7e9a8e696f",
}


class TrustedPluginHold(ValueError):
    """Candidate pytest hooks have not been reviewed at this exact content."""


def load_verified_plugin(repo: Path, expected: dict[str, str]):
    """Verify source bytes before import; no editable candidate import path."""
    repo = Path(repo)
    scripts = repo / "scripts"
    if (repo.is_symlink() or not repo.is_dir() or scripts.is_symlink() or
            not scripts.is_dir() or set(expected) != set(REVIEWED_MODULES)):
        raise TrustedPluginHold("SOURCE_LAYOUT")
    verified = {}
    for name in ("rc6_ci_shard_quorum.py", "rc6_ci_pytest_identity.py"):
        path = scripts / name
        expected_hash = expected[name]
        if (not isinstance(expected_hash, str) or
                not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or
                path.is_symlink()):
            raise TrustedPluginHold("SOURCE_DIGEST")
        try:
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= 256 * 1024:
                raise TrustedPluginHold("SOURCE_TYPE")
            content = path.read_bytes()
            after = path.stat(follow_symlinks=False)
        except OSError:
            raise TrustedPluginHold("SOURCE_UNREADABLE") from None
        if ((before.st_dev, before.st_ino, before.st_size) !=
                (after.st_dev, after.st_ino, after.st_size) or
                len(content) != before.st_size or
                hashlib.sha256(content).hexdigest() != expected_hash):
            raise TrustedPluginHold("SOURCE_CHANGED")
        verified[name] = (path, content)
    # The package and both modules are preloaded from verified bytes.  The
    # candidate tree becomes importable only AFTER this import boundary.
    package = types.ModuleType("scripts")
    package.__path__ = [str(scripts)]
    package.__package__ = "scripts"
    sys.modules["scripts"] = package
    for name in ("rc6_ci_shard_quorum.py", "rc6_ci_pytest_identity.py"):
        path, content = verified[name]
        module_name = "scripts." + name.removesuffix(".py")
        module = types.ModuleType(module_name)
        module.__file__ = str(path)
        module.__package__ = "scripts"
        sys.modules[module_name] = module
        try:
            exec(compile(content, str(path), "exec"), module.__dict__)
        except Exception:
            raise TrustedPluginHold("SOURCE_IMPORT_UNVERIFIED") from None
    return sys.modules["scripts.rc6_ci_pytest_identity"]


def main(argv: list[str] | None = None) -> int:
    # `python3 -I -B` is mandatory: ignore cwd/PYTHONPATH/user-site injection
    # before loading reviewed hooks.  Image system site customization remains
    # part of the separately approved D_qa image trust boundary.
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        print("RC6_QA_PLUGIN=HOLD|qa_certified=false", file=sys.stderr)
        return 79
    if (os.environ.get("RC6_CI_SOURCE_SHA") != REVIEWED_CANDIDATE_SHA or
            os.environ.get("RC6_CI_SOURCE_TREE_SHA") != REVIEWED_CANDIDATE_TREE):
        print("RC6_QA_PLUGIN=HOLD|qa_certified=false", file=sys.stderr)
        return 79
    # Import pytest before introducing the candidate path into sys.path.
    import pytest
    try:
        plugin = load_verified_plugin(Path("/src"), REVIEWED_MODULES)
    except TrustedPluginHold:
        print("RC6_QA_PLUGIN=HOLD|qa_certified=false", file=sys.stderr)
        return 79
    if "/src" not in sys.path:
        sys.path.append("/src")
    return int(pytest.main(sys.argv[1:] if argv is None else argv,
                           plugins=[plugin]))


if __name__ == "__main__":
    raise SystemExit(main())
