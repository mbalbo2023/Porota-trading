from pathlib import Path


PRODUCTION_BRANCH = "deploy/rc6-pr69-isolated-20260915"
EXPECTED_PUSH_WORKFLOW = ".github/workflows/porota-deploy-v2-promote.yml"


def _push_trigger_block(text: str) -> str:
    """Return only the top-level on.push block, never pull_request siblings."""
    lines=text.splitlines()
    in_on=False
    in_push=False
    out=[]
    for line in lines:
        stripped=line.strip()
        indent=len(line)-len(line.lstrip(" "))
        if not in_on:
            if stripped=="on:" and indent==0:
                in_on=True
            continue
        if indent==0 and stripped and stripped!="on:":
            break
        if indent==2 and stripped.endswith(":"):
            if stripped=="push:":
                in_push=True
                out=[line]
                continue
            if in_push:
                break
            in_push=False
            continue
        if in_push:
            out.append(line)
    return "\n".join(out)


def test_exactly_one_workflow_can_push_deploy_current_production_branch():
    matches=[]
    for path in sorted(Path(".github/workflows").glob("*.y*ml")):
        text=path.read_text(encoding="utf-8")
        push=_push_trigger_block(text)
        if push and PRODUCTION_BRANCH in push:
            matches.append(path.as_posix())
    assert matches == [EXPECTED_PUSH_WORKFLOW]


def test_push_parser_does_not_confuse_pull_request_target_with_other_push_branch():
    fixture="""name: fixture
on:
  push:
    branches:
      - candidate/other
  pull_request:
    branches:
      - deploy/rc6-pr69-isolated-20260915
permissions:
  contents: read
"""
    assert PRODUCTION_BRANCH not in _push_trigger_block(fixture)


def test_legacy_rebuild_workflow_is_retired_from_actions():
    assert not Path(
        ".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml"
    ).exists()
