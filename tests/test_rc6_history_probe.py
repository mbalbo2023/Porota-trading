"""Offline evidence sampling keeps working across normal scratch cleanup."""
from pathlib import Path
import os
import shutil

from rc6_audit_evidence.history_probe import PeakDisk


def test_NEW_disk_sampling_survives_scratch_directory_disappearing_during_traversal(tmp_path, monkeypatch):
    stable = tmp_path/"retained-evidence.bin"
    stable.write_bytes(b"e"*4096)
    scratch = tmp_path/"vanishing-scratch"
    scratch.mkdir()
    (scratch/"copy.sqlite").write_bytes(b"temporary")
    original = os.scandir
    removed = []
    def cleaned_before_descending(path):
        if not isinstance(path,int) and Path(path)==scratch and scratch.exists():
            shutil.rmtree(scratch)
            removed.append(True)
        return original(path)
    monkeypatch.setattr(os,"scandir",cleaned_before_descending)
    sampler = PeakDisk(tmp_path)
    sampler.sample()
    assert removed and not scratch.exists()
    assert sampler.logical==4096
    assert sampler.allocated==stable.stat().st_blocks*512
    sampler.sample()
    assert sampler.logical==4096
