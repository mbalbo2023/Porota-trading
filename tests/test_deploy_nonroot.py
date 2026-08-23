"""El despliegue remoto debe funcionar sin acceso SSH de root."""

from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]


def _workflow() -> str:
    return (RAIZ / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )


def test_referencia_no_se_interpola_dentro_del_shell_remoto():
    workflow = _workflow()

    assert "DEPLOY_REF: ${{ github.event.inputs.referencia }}" in workflow
    assert "envs: DEPLOY_REF" in workflow
    assert 'REFERENCIA="${DEPLOY_REF:-origin/main}"' in workflow
    assert 'REFERENCIA="${{ github.event.inputs.referencia }}"' not in workflow
    assert 'git rev-parse --verify "${REFERENCIA}^{commit}"' in workflow


def test_deploy_usa_sudo_con_el_usuario_administrativo():
    workflow = _workflow()

    assert "DO_USER debe ser" in workflow
    assert "porotaadmin" in workflow
    assert "sudo git fetch origin --tags" in workflow
    assert 'sudo git checkout --detach "$REFERENCIA"' in workflow
    assert "sudo docker compose build" in workflow
    assert "sudo docker compose up -d" in workflow
    assert "sudo docker compose ps" in workflow


def test_deploy_prueba_y_respalda_antes_de_recrear():
    workflow = _workflow()

    pruebas = workflow.index("--entrypoint pytest")
    backup = workflow.index("az_maintenance_job.py backup")
    despliegue = workflow.index("sudo docker compose up -d")

    assert pruebas < backup < despliegue
