from pathlib import Path


def test_introspection_keeps_runtime_workers_and_removes_redundant_panels():
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    section = source.split("def introspection_content():", 1)[1].split("def scheduler_content():", 1)[0]

    assert "<h2>Workers</h2>" in section
    assert "<h2>Aprendizaje matemático</h2>" not in section
    assert "<h2>Familias financieras — descubrimiento y capacidad</h2>" not in section
