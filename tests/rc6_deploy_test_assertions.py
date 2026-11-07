"""Behavioral assertions shared by Deploy V2 tests."""

import re

import yaml


_ROLLBACK_EXECUTION = re.compile(
    r"\brollback(?:_[a-z0-9]+)?\b|"
    r"\brc6_transactional_host_deploy_v3\.sh\b|"
    r"\bgit\s+(?:reset\s+--hard|checkout\s+--)\b|"
    r"\bdocker\s+(?:image\s+)?tag\s+[\"']?\$(?:OLD|PREVIOUS|PRIOR)_[A-Z_]+\b|"
    r"--action\s+rollback\b",
    re.IGNORECASE,
)


def assert_no_executable_rollback(workflow: str) -> None:
    """Reject executable rollback in promotion steps, not diagnostic wording.

    YAML is parsed to inspect run blocks only, so descriptions and event
    receipts containing NO_ROLLBACK cannot masquerade as host operations.
    """
    actions = yaml.safe_load(workflow)
    steps = actions["jobs"]["promote"]["steps"]
    offenders = []
    for step in steps:
        for number, raw in enumerate(step.get("run", "").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#") or re.match(
                r"^(?:echo|printf|print\s*\()\b", line
            ):
                continue
            if _ROLLBACK_EXECUTION.search(line):
                offenders.append((step.get("id", step.get("name")), number, line))
    assert not offenders, f"ROLLBACK_COMMAND_FORBIDDEN:{offenders!r}"
