"""El despliegue remoto debe funcionar sin acceso SSH de root."""

import re
from pathlib import Path


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
    assert "rollback" not in workflow.lower()
    assert "FIX-FORWARD ONLY" in workflow
