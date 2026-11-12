"""A138 cleanup import regression against the authentic historical Git object.

This is an OFFHOST stdin/subprocess test.  It neither simulates a host receipt
nor touches Docker, systemd, PPI, SQLite production data or the deployment.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
QUIESCENCE_PATH = "scripts/rc6_deploy_quiescence.py"
A138_HEAD = "dd22a1b0761cc4f231813edb6b502d1dd21bb8de"
A138_SOURCE_LENGTH = 111_053
A138_SOURCE_SHA256 = "dcb0ed996a6be9aefb0a609139285f6160248909ccde92f189a24b77fb617662"


def _historical_a138_source() -> bytes:
    result = subprocess.run(
        ["git", "show", f"{A138_HEAD}:{QUIESCENCE_PATH}"],
        cwd=REPO,
        check=True,
        capture_output=True,
        timeout=20,
    )
    source = result.stdout
    assert len(source) == A138_SOURCE_LENGTH
    assert hashlib.sha256(source).hexdigest() == A138_SOURCE_SHA256
    return source


def _cleanup_stdin(source: bytes, *, host_root: Path, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Execute actual source as ``python3 -`` with no checkout in sys.path."""
    # The historic cleanup uses stdin (no reliable source file path).  Giving
    # the source a non-main module name avoids invoking its CLI main(), while
    # retaining the exact operator function body and imported dependencies.
    streamed = (
        b"__name__ = 'rc6_a138_stdin_replay'\n"
        + source
        + b"\nROOT = Path(" + repr(str(host_root)).encode("utf-8") + b")\n"
        + b"print('A138_TEST_DIGEST=' + operator_paper_no_go_proof())\n"
    )
    env = os.environ.copy()
    for name in (
        "PYTHONPATH",
        "RC6_A125_CANDIDATE_SOURCE_B64",
        "RC6_A125_CANDIDATE_SOURCE_GZIP_B64",
        "RC6_A125_CANDIDATE_SOURCE_SHA256",
    ):
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, "-"],
        input=streamed,
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _operator_stub(host_root: Path) -> None:
    """Only the fake operator import exists, never host state or permissions."""
    scripts = host_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "rc6_deploy_scheduler_suspend.py").write_text(
        "def exact_operator_preopen_pause(**kwargs):\n"
        "    return 'a' * 64\n"
        "def operator_preopen_timer_disabled():\n"
        "    return True\n",
        encoding="utf-8",
    )


def test_a138_historical_stdin_cleanup_fails_importing_scripts(tmp_path):
    """Historical A138 emitted ModuleNotFoundError under the observed route."""
    host_root = tmp_path / "fake_host_root"
    _operator_stub(host_root)
    outside = tmp_path / "outside_checkout"
    outside.mkdir()

    result = _cleanup_stdin(_historical_a138_source(), host_root=host_root, cwd=outside)

    assert result.returncode != 0
    assert b"ModuleNotFoundError: No module named 'scripts'" in result.stderr
    assert b"A138_TEST_DIGEST=" not in result.stdout


def test_a138_current_stdin_cleanup_resolves_only_pinned_root(tmp_path):
    """Regression: current fallback can find its operator outside the cwd."""
    host_root = tmp_path / "fake_host_root"
    _operator_stub(host_root)
    outside = tmp_path / "outside_checkout"
    outside.mkdir()
    current = (REPO / QUIESCENCE_PATH).read_bytes()
    # Manual negative control: run this very same contract against the actual
    # old source, and expect a causal RED before calling current code GREEN.
    if os.environ.get("RC6_A138_MUTATE_HISTORICAL") == "1":
        current = _historical_a138_source()

    result = _cleanup_stdin(current, host_root=host_root, cwd=outside)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.strip() == b"A138_TEST_DIGEST=" + (b"a" * 64)


def test_a138_current_stdin_cleanup_does_not_admit_missing_operator(tmp_path):
    """Absent installed module cannot falsely certify an operator pause."""
    host_root = tmp_path / "missing_host_root"
    outside = tmp_path / "outside_checkout"
    outside.mkdir()
    result = _cleanup_stdin(
        (REPO / QUIESCENCE_PATH).read_bytes(), host_root=host_root, cwd=outside
    )

    assert result.returncode != 0
    assert b"A138_TEST_DIGEST=" not in result.stdout
