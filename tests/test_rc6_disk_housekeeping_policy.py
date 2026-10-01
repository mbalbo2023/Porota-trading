from __future__ import annotations

import json
import time
from pathlib import Path

from scripts.rc6_disk_housekeeping import backup_plan, disk_band, tmp_candidates
from scripts.rc6_disk_space_guard import load_policy, required_pretransfer_free


REPO = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO / "ops/policy/rc6-disk-housekeeping-v1.json"


def policy() -> dict:
    return load_policy(POLICY_PATH)


def test_policy_retention_is_exactly_three_for_paper_and_history() -> None:
    p = policy()
    assert p["backups"]["paper"]["keep_newest"] == 3
    assert p["backups"]["history"]["keep_newest"] == 3
    assert p["backups"]["general"]["keep_newest"] == 3


def test_policy_has_conservative_disk_thresholds() -> None:
    p = policy()
    assert p["disk"]["green_free_bytes"] == 7 * 1024**3
    assert p["disk"]["deploy_pretransfer_min_free_bytes"] == 6 * 1024**3
    assert p["disk"]["post_cleanup_min_free_bytes"] == 5 * 1024**3
    assert p["disk"]["critical_free_bytes"] == 3 * 1024**3
    assert p["disk"]["min_inode_free_percent"] == 10


def test_pretransfer_formula_includes_transfer_plus_post_transfer_headroom() -> None:
    p = policy()
    image = 409_264_065
    bundle = 1_118_828
    required = required_pretransfer_free(image, bundle, p)
    calculated = image * 5 + bundle * 3 + 5 * 1024**3
    assert required == max(6 * 1024**3, calculated)


def test_backup_plan_keeps_three_newest(tmp_path: Path) -> None:
    now = time.time()
    files = []
    for idx in range(6):
        p = tmp_path / f"observer_2026-09-{25+idx:02d}.db.gz"
        p.write_bytes(bytes([idx]) * (idx + 1))
        ts = now - idx * 86400
        p.touch()
        Path(p).chmod(0o600)
        import os
        os.utime(p, (ts, ts))
        files.append(p)

    kept, delete = backup_plan(tmp_path, "observer_*.db.gz", 3)
    kept_names = {Path(x.path).name for x in kept}
    delete_names = {Path(x.path).name for x in delete}
    expected_kept = {p.name for p in files[:3]}
    assert kept_names == expected_kept
    assert delete_names == {p.name for p in files[3:]}


def test_backup_plan_preserves_explicit_pin_beyond_three(tmp_path: Path) -> None:
    import os
    now = time.time()
    rows = []
    for idx in range(5):
        p = tmp_path / f"porota-general-{idx}.tar.gz"
        p.write_bytes(b"x")
        ts = now - idx * 86400
        os.utime(p, (ts, ts))
        rows.append(p)

    pinned = rows[-1].name
    kept, delete = backup_plan(
        tmp_path,
        "porota-general-*.tar.gz",
        3,
        [pinned],
    )
    kept_names = {Path(x.path).name for x in kept}
    assert pinned in kept_names
    assert len(kept_names) == 4
    assert pinned not in {Path(x.path).name for x in delete}


def test_tmp_candidates_require_prefix_and_age(tmp_path: Path) -> None:
    import os
    now = time.time()
    old_porota = tmp_path / "porota-old"
    old_rc6 = tmp_path / "rc6-old"
    new_porota = tmp_path / "porota-new"
    unrelated = tmp_path / "other-old"
    for p in (old_porota, old_rc6, new_porota, unrelated):
        p.write_text("x", encoding="utf-8")
    old = now - 8 * 86400
    new = now - 2 * 86400
    for p in (old_porota, old_rc6, unrelated):
        os.utime(p, (old, old))
    os.utime(new_porota, (new, new))

    p = policy()
    p = json.loads(json.dumps(p))
    p["tmp"]["root"] = str(tmp_path)
    selected = {Path(x.path).name for x in tmp_candidates(p, now=now)}
    assert selected == {"porota-old", "rc6-old"}


def test_disk_band_boundaries() -> None:
    p = policy()
    gib = 1024**3
    assert disk_band(8 * gib, p) == "GREEN"
    assert disk_band(6 * gib, p) == "YELLOW"
    assert disk_band(4 * gib, p) == "RED"
    assert disk_band(2 * gib, p) == "CRITICAL"


def test_no_automatic_sqlite_or_containerd_mutation() -> None:
    p = policy()
    assert p["sqlite"]["automatic_vacuum_allowed"] is False
    assert p["sqlite"]["automatic_wal_delete_allowed"] is False
    assert p["docker"]["cleanup_mode"] == "OBSERVE_ONLY"
    assert p["docker"]["manual_containerd_mutation_allowed"] is False
