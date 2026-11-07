"""Executable, off-host guard for the frozen Docker image promoted by Deploy V2.

The regression is a frozen candidate manifest whose config digest disagrees
with Docker's loaded image ID despite the tar SHA remaining unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap

import pytest


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/porota-deploy-v2-promote.yml"
)


def _run_materialize_gate(tmp_path: Path, expected_image_id: str, loaded_image_id: str):
    """Execute the actual promote fragment; substitute only external Docker CLI."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    begin = workflow.index('          echo "POROTA_IMAGE_ID_VERIFY=')
    finish = workflow.index('          echo "POROTA_BUILD_ONCE_MATERIALIZED=', begin)
    finish = workflow.index("\n", finish)
    script = textwrap.dedent(workflow[begin:finish]) + "\necho RC6_START_PERMITTED\n"

    executable = tmp_path / "sudo"
    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$TAG_TRACE\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    tag_trace = tmp_path / "tag-trace.txt"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "TAG_TRACE": str(tag_trace),
        "EXPECTED_IMAGE_ID": expected_image_id,
        "LOADED_IMAGE_ID": loaded_image_id,
        "RUNTIME_IMAGE_ID": loaded_image_id,
        "IMAGE_TAR_SHA": "a" * 64,
        "EXPECTED_TAR_SHA": "a" * 64,
        "IMAGE_CONFIG_SHA": "b" * 64,
        "EXPECTED_CONFIG_SHA": "b" * 64,
        "FROZEN_IMAGE": "porota-frozen-fixture",
        "CANDIDATE_IMAGE": "porota-candidate-fixture",
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    trace = tag_trace.read_text(encoding="utf-8") if tag_trace.exists() else ""
    return result, trace


@pytest.mark.parametrize(
    "expected_image_id, loaded_image_id",
    [
        ("sha256:" + "a" * 64, "sha256:" + "b" * 64),
        ("", "sha256:" + "b" * 64),
        ("sha256:malformed", "sha256:" + "b" * 64),
        ("sha256:" + "b" * 64, "not-a-digest"),
    ],
)
def test_mismatched_or_malformed_image_id_never_tags_or_starts(
    tmp_path, expected_image_id, loaded_image_id
):
    result, trace = _run_materialize_gate(tmp_path, expected_image_id, loaded_image_id)
    assert result.returncode != 0, result.stdout
    assert "POROTA_FROZEN_IMAGE_ID_GATE=RED" in result.stdout
    assert "docker tag" not in trace
    assert "RC6_START_PERMITTED" not in result.stdout


def test_exact_matching_image_id_can_tag_and_continue(tmp_path):
    matching = "sha256:" + "c" * 64
    result, trace = _run_materialize_gate(tmp_path, matching, matching)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "POROTA_FROZEN_IMAGE_ID_GATE=GREEN" in result.stdout
    assert "-n docker tag porota-frozen-fixture porota-candidate-fixture" in trace
    assert "RC6_START_PERMITTED" in result.stdout
