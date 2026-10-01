from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _timer(name: str) -> str:
    return (REPO / "systemd" / name).read_text(encoding="utf-8")


def test_postclose_heavy_jobs_are_staggered_out_of_old_cluster():
    assert "17:08:00 America/Argentina/Buenos_Aires" in _timer(
        "porota-snapshot-postclose-rc6.timer"
    )
    assert "*:35:00" in _timer("porota-introspection-rc6.timer")
    assert "*:42:00" in _timer("porota-introspection-publish-rc6.timer")
    assert "*:50:00" in _timer("porota-runtime-evidence-rc6.timer")


def test_introspection_chain_remains_ordered():
    collect = _timer("porota-introspection-rc6.timer")
    publish = _timer("porota-introspection-publish-rc6.timer")
    runtime = _timer("porota-runtime-evidence-rc6.timer")
    assert "*:35:00" in collect
    assert "*:42:00" in publish
    assert "*:50:00" in runtime
