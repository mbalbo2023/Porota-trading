"""Bounded publication checks, not isolation from a hostile same-UID writer."""
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import cg_paper_workspace as workspace


def test_late_source_replacement_cannot_return_success(tmp_path, monkeypatch):
    target = tmp_path / 'paper.db'
    original = workspace._publish_no_replace

    def replace_after_fsync(source_fd, source_name, parent_fd, target_name):
        os.rename(source_name, 'preserved-original.db', src_dir_fd=source_fd,
                  dst_dir_fd=parent_fd)
        replacement = os.open(source_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                              0o600, dir_fd=source_fd)
        try:
            os.write(replacement, b'UNVERIFIED REPLACEMENT')
        finally:
            os.close(replacement)
        original(source_fd, source_name, parent_fd, target_name)

    monkeypatch.setattr(workspace, '_publish_no_replace', replace_after_fsync)
    with pytest.raises(ValueError, match='PUBLICATION_SOURCE_CHANGED'):
        workspace.runtime_store(target)
    # Failure preserves evidence; never delete the externally substituted file.
    assert target.read_bytes() == b'UNVERIFIED REPLACEMENT'
    assert workspace.read_identity(tmp_path / 'preserved-original.db')['initial_capital_json'] == workspace.capital_profile()


@pytest.mark.parametrize('boundary', ['source_file', 'parent_directory'])
def test_fsync_failure_never_returns_store_and_preserves_restart_contract(tmp_path, monkeypatch, boundary):
    target = tmp_path / 'paper.db'
    original = os.fsync

    def fail_at_boundary(fd):
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if is_directory == (boundary == 'parent_directory'):
            raise OSError('injected publication fsync failure')
        original(fd)

    monkeypatch.setattr(os, 'fsync', fail_at_boundary)
    with pytest.raises(OSError, match='injected publication'):
        workspace.runtime_store(target)
    assert target.exists() == (boundary == 'parent_directory')
    monkeypatch.setattr(os, 'fsync', original)
    restarted = workspace.runtime_store(target)
    assert Path(restarted.path) == target
    assert workspace.read_identity(target)['initial_capital_json'] == workspace.capital_profile()


@pytest.mark.parametrize('boundary', ['source_file', 'parent_directory'])
def test_process_exit_at_publication_boundary_is_restartable(tmp_path, boundary):
    """Process-crash recovery only; not a power-loss/filesystem durability proof."""
    target = tmp_path / 'paper.db'
    program = '''
import os, stat, sys
import cg_paper_workspace as workspace
original = os.fsync
def exit_at_boundary(fd):
    is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
    if is_directory == (sys.argv[2] == 'parent_directory'):
        os._exit(73)
    original(fd)
os.fsync = exit_at_boundary
workspace.runtime_store(sys.argv[1])
'''
    process = subprocess.run([sys.executable, '-c', program, str(target), boundary],
                             cwd=Path(__file__).resolve().parents[1],
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 73, process.stderr
    assert target.exists() == (boundary == 'parent_directory')
    restarted = workspace.runtime_store(target)
    assert Path(restarted.path) == target
    assert workspace.read_identity(target)['initial_capital_json'] == workspace.capital_profile()
