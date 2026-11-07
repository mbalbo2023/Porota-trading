"""A830: SOURCE publisher output isolation, with no provider/network calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from scripts import rc6_iol_shadow_collect as iol
from scripts import rc6_public_source_capture as public


PAYLOAD = {"schema": "offline-source-fixture", "sources": [], "received": True}


def _publish(monkeypatch, producer: str, market_root: Path, output: Path) -> None:
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(market_root))
    monkeypatch.setattr(iol, "DEFAULT_ROOT", market_root)
    if producer == "public":
        monkeypatch.setattr(public, "collect_public_sources", lambda: dict(PAYLOAD))
        assert public.capture(output) == PAYLOAD
    else:
        assert iol._atomic_json(output, PAYLOAD) is None


@pytest.mark.parametrize("producer", ["public", "iol"])
def test_normal_source_publish_preserves_json_bytes_and_regular_mode(tmp_path, monkeypatch, producer):
    market = tmp_path / "market"
    market.mkdir()
    output = market / f"{producer}.json"
    _publish(monkeypatch, producer, market, output)
    assert output.read_bytes() == json.dumps(PAYLOAD, ensure_ascii=False, sort_keys=True).encode()
    assert stat.S_ISREG(output.lstat().st_mode)
    assert output.stat().st_nlink == 1
    assert stat.S_IMODE(output.stat().st_mode) == 0o644


@pytest.mark.parametrize("producer", ["public", "iol"])
def test_owned_regular_file_may_be_atomically_replaced_without_changing_foreign_files(tmp_path, monkeypatch, producer):
    market = tmp_path / "market"
    market.mkdir()
    output = market / f"{producer}.json"
    output.write_text("STALE")
    previous_inode = output.stat().st_ino
    _publish(monkeypatch, producer, market, output)
    assert output.stat().st_ino != previous_inode
    assert json.loads(output.read_text()) == PAYLOAD
    assert not list(market.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize("producer", ["public", "iol"])
def test_legacy_fixed_tmp_symlink_does_not_write_foreign_file(tmp_path, monkeypatch, producer):
    market = tmp_path / "market"
    market.mkdir()
    output = market / f"{producer}.json"
    foreign = tmp_path / "other_app_owned.txt"
    foreign.write_text("FOREIGN_ORIGINAL")
    legacy = output.with_suffix(output.suffix + ".tmp")
    legacy.symlink_to(foreign)
    _publish(monkeypatch, producer, market, output)
    assert foreign.read_text() == "FOREIGN_ORIGINAL"
    assert legacy.is_symlink()  # Never modify or clean another owner's path.
    assert output.is_file() and not output.is_symlink()
    assert json.loads(output.read_text()) == PAYLOAD


@pytest.mark.parametrize("producer", ["public", "iol"])
@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_foreign_final_alias_is_rejected_without_overwrite(tmp_path, monkeypatch, producer, alias):
    market = tmp_path / "market"
    market.mkdir()
    foreign = tmp_path / "other_app_owned.txt"
    foreign.write_text("FOREIGN_ORIGINAL")
    output = market / f"{producer}.json"
    if alias == "symlink":
        output.symlink_to(foreign)
    else:
        os.link(foreign, output)
    with pytest.raises(ValueError, match="SOURCE_OUTPUT_UNSAFE"):
        _publish(monkeypatch, producer, market, output)
    assert foreign.read_text() == "FOREIGN_ORIGINAL"
    assert output.exists()


@pytest.mark.parametrize("producer", ["public", "iol"])
def test_symlinked_parent_cannot_redirect_publication_to_other_app(tmp_path, monkeypatch, producer):
    foreign = tmp_path / "other_app"
    foreign.mkdir()
    market = tmp_path / "market"
    market.symlink_to(foreign, target_is_directory=True)
    output = market / f"{producer}.json"
    with pytest.raises(ValueError, match="SOURCE_OUTPUT_UNSAFE"):
        _publish(monkeypatch, producer, market, output)
    assert not (foreign / output.name).exists()


@pytest.mark.parametrize("producer", ["public", "iol"])
def test_output_outside_declared_parent_rejected_even_without_symlinks(tmp_path, monkeypatch, producer):
    market = tmp_path / "market"
    market.mkdir()
    foreign = tmp_path / "other_app"
    foreign.mkdir()
    output = foreign / f"{producer}.json"
    with pytest.raises(ValueError, match="SOURCE_OUTPUT_UNSAFE"):
        _publish(monkeypatch, producer, market, output)
    assert not output.exists()


@pytest.mark.parametrize("producer", ["public", "iol"])
def test_unprovisioned_parent_never_created_for_source_output(tmp_path, monkeypatch, producer):
    market = tmp_path / "unprovisioned" / "market"
    output = market / f"{producer}.json"
    with pytest.raises(ValueError, match="SOURCE_OUTPUT_UNSAFE"):
        _publish(monkeypatch, producer, market, output)
    assert not market.exists()


@pytest.mark.parametrize("producer", ["public", "iol"])
def test_final_alias_appearing_during_stage_is_rejected(tmp_path, monkeypatch, producer):
    market = tmp_path / "market"
    market.mkdir()
    output = market / f"{producer}.json"
    foreign = tmp_path / "other_app_owned.txt"
    foreign.write_text("FOREIGN_ORIGINAL")
    real_fchmod = os.fchmod
    injected = []

    def inject_symlink(fd, mode):
        real_fchmod(fd, mode)
        if not injected:
            output.symlink_to(foreign)
            injected.append(True)

    monkeypatch.setattr(os, "fchmod", inject_symlink)
    with pytest.raises(ValueError, match="SOURCE_OUTPUT_UNSAFE"):
        _publish(monkeypatch, producer, market, output)
    assert injected == [True]
    assert foreign.read_text() == "FOREIGN_ORIGINAL"
    assert output.is_symlink()
    assert not list(market.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize("producer", ["public", "iol"])
def test_swap_after_final_recheck_cannot_follow_foreign_symlink(tmp_path, monkeypatch, producer):
    market = tmp_path / "market"
    market.mkdir()
    output = market / f"{producer}.json"
    foreign = tmp_path / "other_app_owned.txt"
    foreign.write_text("FOREIGN_ORIGINAL")
    actual_replace = os.replace
    injected = []

    def race_replace(source, target, **kwargs):
        if kwargs.get("src_dir_fd") is not None and kwargs.get("dst_dir_fd") is not None:
            output.symlink_to(foreign)
            injected.append(True)
        return actual_replace(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", race_replace)
    _publish(monkeypatch, producer, market, output)
    assert injected == [True]  # Anchored rename, not path-following write.
    assert foreign.read_text() == "FOREIGN_ORIGINAL"
    assert output.is_file() and not output.is_symlink()
