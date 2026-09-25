from scripts.porota_dependency_repro_audit import audit, requirement_rows


def test_exact_requirement_is_recognized():
    rows = requirement_rows("requests==2.32.5\n# comment\n")
    assert rows == [{"requirement": "requests==2.32.5", "exact_pin": True}]


def test_range_requirement_is_reported_as_non_reproducible():
    result = audit("requests>=2.32\npandas>=2,<3\n", "FROM python:3.11-slim@sha256:" + "a"*64)
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["non_exact_requirements"] == ["requests>=2.32", "pandas>=2,<3"]
    assert result["base_image_digest_pinned"] is True


def test_unpinned_base_image_is_reported():
    result = audit("requests==2.32.5\n", "FROM python:3.11-slim\n")
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["non_exact_requirements"] == []
    assert result["base_image_digest_pinned"] is False


def test_fully_pinned_inputs_are_green():
    result = audit("requests==2.32.5\n", "FROM python:3.11-slim@sha256:" + "b"*64)
    assert result["status"] == "GREEN"
