from scripts.porota_dependency_repro_audit import audit, requirement_rows


def test_exact_requirement_is_recognized():
    rows = requirement_rows("requests==2.34.2\n# comment\n")
    assert rows == [{"requirement": "requests==2.34.2", "exact_pin": True}]


def test_source_ranges_are_allowed_when_lock_is_exact():
    result = audit(
        "requests>=2.32\npandas>=2,<3\n",
        "requests==2.34.2\npandas==2.3.3\n",
        "FROM python:3.11-slim@sha256:" + "a"*64 + "\nRUN pip install -r requirements.lock.txt\n",
    )
    assert result["status"] == "GREEN"
    assert result["lock_non_exact_requirements"] == []


def test_non_exact_lock_is_reported():
    result = audit(
        "requests>=2.32\n",
        "requests>=2.32\n",
        "FROM python:3.11-slim@sha256:" + "a"*64 + "\nRUN pip install -r requirements.lock.txt\n",
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["lock_non_exact_requirements"] == ["requests>=2.32"]


def test_unpinned_base_image_is_reported():
    result = audit(
        "requests>=2.32\n",
        "requests==2.34.2\n",
        "FROM python:3.11-slim\nRUN pip install -r requirements.lock.txt\n",
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["base_image_digest_pinned"] is False


def test_docker_must_install_the_lock():
    result = audit(
        "requests>=2.32\n",
        "requests==2.34.2\n",
        "FROM python:3.11-slim@sha256:" + "b"*64 + "\nRUN pip install -r requirements.txt\n",
    )
    assert result["status"] == "REPRODUCIBILITY_GAP"
    assert result["docker_uses_lock"] is False
