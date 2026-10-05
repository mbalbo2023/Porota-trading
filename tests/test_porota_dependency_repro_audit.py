from scripts.porota_dependency_repro_audit import audit, requirement_rows


def hashed_inputs():
    policy = {"schema": "rc6.hashed-distribution-lock.v1", "platform": {
        "os": "linux", "architecture": "x86_64", "python_minors": ["3.11", "3.12"], "glibc_minimum": "2.36"},
        "packages": [{"name": name, "version": version, "distributions": [{"sha256": "a"*64}]}
                     for name, version in (("requests", "2.34.2"), ("pandas", "2.3.3"))],
        "build_tools": [{"name": "wheel", "version": "0.45.1", "distributions": [{"sha256": "b"*64}]}],
        "allowed_sdists": [], "actions": {"actions/checkout": "c"*40}, "residual_boundaries": ["hosted runner"]}
    lock = "requests==2.34.2 --hash=sha256:" + "a"*64 + "\npandas==2.3.3 --hash=sha256:" + "a"*64 + "\n"
    kwargs = {"supply_chain_policy": policy, "build_lock_text": "wheel==0.45.1 --hash=sha256:" + "b"*64 + "\n",
              "workflow_texts": {"predeploy": "uses: actions/checkout@" + "c"*40},
              "current_platform": {"os": "linux", "architecture": "x86_64", "python_minor": "3.11",
                                   "libc_name": "glibc", "libc_version": "2.36"}}
    docker = ("FROM python:3.11-slim@sha256:" + "d"*64 + "\n"
              "RUN pip install --require-hashes --only-binary=:all: -r requirements.build.lock.txt\n"
              "RUN pip install --require-hashes --only-binary=:all: --no-binary= --no-build-isolation -r requirements.lock.txt\n")
    return lock, docker, kwargs


def test_exact_requirement_is_recognized():
    rows = requirement_rows("requests==2.34.2\n# comment\n")
    assert rows == [{"requirement": "requests==2.34.2", "exact_pin": True}]


def test_source_ranges_are_allowed_when_lock_is_exact():
    lock, docker, kwargs = hashed_inputs()
    result = audit(
        "requests>=2.32\npandas>=2,<3\n",
        lock, docker, **kwargs,
    )
    assert result["status"] == "GREEN"
    assert result["lock_non_exact_requirements"] == []
    assert result["hermetic_build"] is False


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
