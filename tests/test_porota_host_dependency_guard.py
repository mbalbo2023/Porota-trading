from pathlib import Path

from scripts.porota_host_dependency_guard import inspect_units, referenced_paths


def test_referenced_paths_extracts_embedded_bash_paths(tmp_path):
    root = tmp_path / "porota"
    text = (
        "ExecStart=/bin/bash -c '/usr/bin/python3 /opt/porota-trading/scripts/a.py; "
        "/bin/bash /opt/porota-trading/scripts/b.sh'\n"
    )
    refs = referenced_paths(text, root)
    assert refs == [root / "scripts/a.py", root / "scripts/b.sh"]


def test_guard_fails_closed_then_green(tmp_path):
    root = tmp_path / "porota"
    unit = tmp_path / "x.service"
    unit.write_text(
        "ExecStart=/usr/bin/python3 /opt/porota-trading/scripts/required.py\n",
        encoding="utf-8",
    )
    payload = inspect_units([unit], root)
    assert payload["status"] == "RED"
    assert payload["missing"] == [str(root / "scripts/required.py")]

    target = root / "scripts/required.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('ok')\n", encoding="utf-8")
    payload = inspect_units([unit], root)
    assert payload["status"] == "GREEN"
    assert payload["missing"] == []
