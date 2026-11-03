"""Contratos del despliegue accesible y reanudable de HF2."""

from pathlib import Path

from scripts import v17_rc3_hf2_deploy as deploy


def test_deploy_no_hace_backup_antes_de_validar_runtime():
    source = Path(deploy.__file__).read_text(encoding="utf-8")
    activation = source.index('log("FASE=ACTIVACION_SIN_BACKUP_PREVIO")')
    validation = source.index('log("FASE=VALIDACION_CRITICA")')
    backup = source.index('log("FASE=BACKUP_GENERAL_POSTERIOR")')
    assert activation < validation < backup


def test_sudo_es_siempre_no_interactivo():
    source = Path(deploy.__file__).read_text(encoding="utf-8")
    assert '("sudo", "-n", "docker"' in source
    assert '("sudo", "-n", "true")' in source
    assert '("sudo", "-n", "python3"' in source
    assert '"sudo", "python3"' not in source


def test_entorno_del_contenedor_no_se_imprime_y_no_expone_secretos():
    source = Path(deploy.__file__).read_text(encoding="utf-8")
    assert 'capture=True, echo_capture=False' in source


def test_validacion_exige_modo_simulado_y_cero_ordenes_reales():
    source = Path(deploy.__file__).read_text(encoding="utf-8")
    for literal in (
        'mode.get("execution") == "SIMULATED"',
        'mode.get("apis", {}).get("PPI_ORDERS") == "BLOCKED"',
        'state["real_orders_sent"] != 0',
        '"PAPER_AI_GATE_MODE": "OFF"',
        '"PAPER_ECONOMIC_GATE_MODE": "SHADOW"',
    ):
        assert literal in source


def test_falla_posterior_a_activacion_dispara_rollback_hf1():
    source = Path(deploy.__file__).read_text(encoding="utf-8")
    assert 'if activated:' in source
    assert 'result["rollback"] = rollback(repo)' in source
    assert deploy.HF1_COMMIT == "1776fd0c29feb905451cff24bb3bf62f8debe724"
