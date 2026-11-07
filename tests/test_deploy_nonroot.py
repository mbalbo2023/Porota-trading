"""El despliegue remoto debe funcionar sin acceso SSH de root."""

import re
from pathlib import Path

import pytest

from rc6_deploy_test_assertions import assert_no_executable_rollback


RAIZ = Path(__file__).resolve().parents[1]


def _workflow() -> str:
    return (RAIZ / ".github" / "workflows" / "porota-deploy-v2-promote.yml").read_text(
        encoding="utf-8"
    )


def test_referencia_no_se_interpola_dentro_del_shell_remoto():
    workflow = _workflow()

    assert 'ref: ${{ github.sha }}' in workflow
    assert 'mapfile -t PARTS < <(git rev-list --parents -n 1 HEAD' in workflow
    assert 'CANDIDATE_SHA="${PARTS[2]}"' in workflow
    assert 'test "$CANDIDATE_TREE" = "$DEPLOY_TREE"' in workflow
    assert "github.event.inputs.referencia" not in workflow


def test_deploy_usa_sudo_con_el_usuario_administrativo():
    workflow = _workflow()

    assert 'DO_USER: ${{ secrets.DO_USER }}' in workflow
    assert 'test -n "$DO_HOST" && test -n "$DO_USER"' in workflow
    assert '"$DO_USER@$DO_HOST"' in workflow
    assert "sudo -n docker load" in workflow
    assert 'sudo -n docker tag "$FROZEN_IMAGE" "$STABLE_IMAGE"' in workflow
    assert "docker compose build" not in workflow
    assert "docker compose up" not in workflow


def test_deploy_prueba_sin_copia_de_persistencia_antes_de_recrear():
    workflow = _workflow()

    verificacion = workflow.index("POROTA_FROZEN_ARTIFACT_VERIFY=GREEN")
    instalacion = workflow.index("POROTA_BUNDLE_INSTALL=GREEN")
    promocion = workflow.index('porota_mode_manager.py" simulation')

    assert verificacion < instalacion < promocion
    assert not re.search(r"(?m)^\s*(?:sudo -n )?docker build(?:\s|$)", workflow)
    assert_no_executable_rollback(workflow)
    assert "FIX-FORWARD ONLY" in workflow


def test_no_rollback_diagnostic_receipt_is_allowed_and_executed():
    import subprocess

    workflow = _workflow()
    assert_no_executable_rollback(workflow)
    text = workflow.splitlines()
    receipt_line = next(line.strip() for line in text if "NO_ROLLBACK|CONTINUE_RUNTIME_VALIDATION" in line)
    script = "\n".join(["set -Eeuo pipefail", receipt_line])
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "NO_ROLLBACK|CONTINUE_RUNTIME_VALIDATION" in result.stdout


@pytest.mark.parametrize("forbidden_command", [
    "rollback_runtime",
    "sudo -n bash scripts/rc6_transactional_host_deploy_v3.sh",
    "git reset --hard HEAD~1",
    'sudo -n docker tag "$OLD_IMAGE" "$STABLE_IMAGE"',
])
def test_fix_forward_guard_rejects_executable_rollback_in_real_workflow(
    forbidden_command,
):
    workflow = _workflow()
    needle = "          echo 'RC6_DISK_PRECLEAN_VALIDATION=GREEN_PENDING_PRIVATE_SCRATCH_RELEASE'"
    assert workflow.count(needle) == 1
    corrupted = workflow.replace(needle, needle + "\n          " + forbidden_command)
    with pytest.raises(AssertionError, match="ROLLBACK_COMMAND_FORBIDDEN"):
        assert_no_executable_rollback(corrupted)
