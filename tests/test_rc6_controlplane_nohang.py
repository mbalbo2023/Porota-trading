from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WF=ROOT/".github/workflows/rc6-consolidated-bounded-runtime-progress.yml"


def test_bounded_runtime_probe_cannot_drag_mutating_dependencies():
    text=WF.read_text(encoding="utf-8")
    assert "needs:" not in text
    assert "timeout-minutes: 5" in text
    assert "timeout --signal=TERM --kill-after=10s 90s" in text
    assert "ConnectTimeout=10" in text
    assert "ServerAliveCountMax=2" in text


def test_bounded_runtime_probe_is_read_only_by_construction():
    text=WF.read_text(encoding="utf-8")
    forbidden=(
        "docker restart", "docker stop", "docker rm", "docker image prune",
        "docker builder prune", "systemctl restart", "systemctl start",
        "systemctl stop", "systemctl enable", "systemctl disable",
        "sqlite3.connect(DB)", "rm -rf /opt/porota-trading",
    )
    for token in forbidden:
        assert token not in text
    assert "mode=ro" in text
    assert "PRAGMA query_only=ON" in text
